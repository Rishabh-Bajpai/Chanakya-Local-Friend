"""Proxying utilities for forwarding AIR requests to upstream providers."""

import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import Request
import httpx
from fastapi.responses import StreamingResponse, JSONResponse
from server.schemas.provider_schema import ProviderConfig
from server.core.exceptions import ProxyError
from server.core.logging import logger

LOG_PREVIEW_CHAR_LIMIT = 12000
STREAM_PROGRESS_CHUNK_INTERVAL = 10
TRACE_SUMMARY_DIVIDER = "-----------------------------*****-----------------------------"


@dataclass(slots=True)
class TraceRequestRecord:
    """Trace metadata captured for a single upstream request."""

    sequence: int
    path: str
    model: str | None
    messages: int | None
    tools: int | None
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    status_code: int | None = None
    content_type: str | None = None


@dataclass(slots=True)
class TraceSummary:
    """Aggregated trace state for a top-level AIR request."""

    trace_id: str
    top_level_request_id: str
    original_message: str = ""
    next_sequence: int = 1
    active_requests: set[int] = field(default_factory=set)
    requests: list[TraceRequestRecord] = field(default_factory=list)


_trace_summaries: dict[str, TraceSummary] = {}


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with sensitive authorization values removed."""
    redacted_headers = dict(headers)
    for header_name in ("authorization", "x-api-key", "api-key", "proxy-authorization"):
        for existing_name in list(redacted_headers.keys()):
            if existing_name.lower() == header_name:
                redacted_headers[existing_name] = "<redacted>"
    return redacted_headers


def _truncate_text(text: str, limit: int = LOG_PREVIEW_CHAR_LIMIT) -> str:
    """Trim long log payloads to the configured preview limit."""
    if len(text) <= limit:
        return text
    truncated_chars = len(text) - limit
    return f"{text[:limit]}\n... <truncated {truncated_chars} chars>"


def _serialize_for_log(payload: object) -> str:
    """Serialize an arbitrary payload into a bounded log-safe string."""
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        serialized = str(payload)
    return _truncate_text(serialized)


def _response_preview(content: bytes, content_type: str) -> str:
    """Build a human-readable preview for text and binary upstream responses."""
    lowered_content_type = content_type.lower()
    if any(token in lowered_content_type for token in ("json", "text", "xml", "html", "javascript")):
        return _truncate_text(content.decode("utf-8", errors="replace"))
    return f"<{len(content)} bytes of {content_type or 'application/octet-stream'}>"


def _request_id_for(request: Request) -> str:
    """Return the request identifier used for per-request trace logging."""
    return request.headers.get("x-request-id") or uuid4().hex[:8]


def _trace_id_for(request: Request, request_id: str) -> str:
    """Resolve the broader trace identifier shared across chained requests."""
    return (
        request.headers.get("x-chanakya-request-id")
        or request.headers.get("x-session-id")
        or request.headers.get("x-request-id")
        or request_id
    )


def _request_sequence_for(trace_id: str) -> int:
    """Allocate the next sequence number for a trace."""
    summary = _trace_summaries.get(trace_id)
    if summary is None:
        return 1
    sequence = summary.next_sequence
    summary.next_sequence += 1
    return sequence


def _message_count(payload: object) -> int | None:
    """Extract the number of chat messages from a JSON payload when present."""
    if isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list):
            return len(messages)
    return None


def _tool_count(payload: object) -> int | None:
    """Extract the number of tool definitions from a JSON payload when present."""
    if isinstance(payload, dict):
        tools = payload.get("tools")
        if isinstance(tools, list):
            return len(tools)
    return None


def _model_name(payload: object) -> str | None:
    """Extract the requested model name from a JSON payload when present."""
    if isinstance(payload, dict):
        model = payload.get("model")
        if model is not None:
            return str(model)
    return None


def _summary_payload(payload: object) -> dict[str, object]:
    """Reduce a request payload to the fields used in summary logging."""
    summary: dict[str, object] = {}
    message_count = _message_count(payload)
    if message_count is not None:
        summary["messages"] = message_count
    tool_count = _tool_count(payload)
    if tool_count is not None:
        summary["tools"] = tool_count
    model = _model_name(payload)
    if model:
        summary["model"] = model
    if isinstance(payload, dict):
        if "stream" in payload:
            summary["stream"] = bool(payload.get("stream"))
        if "store" in payload:
            summary["store"] = bool(payload.get("store"))
        if "tool_choice" in payload:
            summary["tool_choice"] = payload.get("tool_choice")
    return summary


def _extract_original_message(payload: object) -> str:
    """Extract the most recent user message text for trace summaries."""
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _usage_from_payload(payload: object | None) -> tuple[int | None, int | None, int | None]:
    """Extract token usage counters from a JSON response payload."""
    if not isinstance(payload, dict):
        return None, None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    return (
        int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
        int(completion_tokens) if isinstance(completion_tokens, int) else None,
        int(total_tokens) if isinstance(total_tokens, int) else None,
    )


def _record_trace_request(
    *,
    trace_id: str,
    request_id: str,
    sequence: int,
    path: str,
    payload: object,
) -> None:
    """Create or update trace state for an outbound upstream request."""
    summary = _trace_summaries.get(trace_id)
    if summary is None:
        summary = TraceSummary(trace_id=trace_id, top_level_request_id=request_id)
        summary.next_sequence = sequence + 1
        _trace_summaries[trace_id] = summary
    else:
        summary.next_sequence = max(summary.next_sequence, sequence + 1)
    if not summary.original_message:
        summary.original_message = _extract_original_message(payload)
    summary.active_requests.add(sequence)
    summary.requests.append(
        TraceRequestRecord(
            sequence=sequence,
            path=path,
            model=_model_name(payload),
            messages=_message_count(payload),
            tools=_tool_count(payload),
        )
    )


def _complete_trace_request(trace_id: str, sequence: int) -> TraceSummary | None:
    """Mark a traced upstream request complete and return the finished summary if any."""
    summary = _trace_summaries.get(trace_id)
    if summary is None:
        return None
    summary.active_requests.discard(sequence)
    if summary.active_requests:
        return None
    return _trace_summaries.pop(trace_id, None)


def _update_trace_response(
    *,
    trace_id: str,
    sequence: int,
    status_code: int,
    content_type: str,
    elapsed_ms: float,
    payload: object | None,
) -> None:
    """Attach response metadata and usage information to a traced request."""
    summary = _trace_summaries.get(trace_id)
    if summary is None:
        return
    for item in summary.requests:
        if item.sequence != sequence:
            continue
        prompt_tokens, completion_tokens, total_tokens = _usage_from_payload(payload)
        item.status_code = status_code
        item.content_type = content_type or "<unknown>"
        item.elapsed_ms = elapsed_ms
        item.prompt_tokens = prompt_tokens
        item.completion_tokens = completion_tokens
        item.total_tokens = total_tokens
        return


def _request_kind(record: TraceRequestRecord) -> str:
    """Classify a traced request into a coarse request kind for logs."""
    if record.messages and record.messages > 2 and record.tools:
        return "core_agent"
    if record.messages == 2:
        return "planner"
    return "request"


def _emit_trace_summary(summary: TraceSummary | None) -> None:
    """Emit the aggregated trace summary once all related requests finish."""
    if summary is None or not summary.requests:
        return

    total_prompt_tokens = sum(item.prompt_tokens or 0 for item in summary.requests)
    total_completion_tokens = sum(item.completion_tokens or 0 for item in summary.requests)
    total_elapsed_ms = sum(item.elapsed_ms or 0.0 for item in summary.requests)

    logger.info(TRACE_SUMMARY_DIVIDER)
    logger.info("Trace Summary: %s", summary.trace_id)
    logger.info("Message: %s", summary.original_message or "<unknown>")
    logger.info("Top-level request: %s", summary.top_level_request_id)
    logger.info("Requests:")
    for item in summary.requests:
        logger.info(
            "- seq=%s kind=%s path=%s model=%s messages=%s tools=%s status=%s elapsed_ms=%.2f prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            item.sequence,
            _request_kind(item),
            item.path,
            item.model or "<unknown>",
            item.messages if item.messages is not None else "-",
            item.tools if item.tools is not None else "-",
            item.status_code if item.status_code is not None else "-",
            item.elapsed_ms or 0.0,
            item.prompt_tokens if item.prompt_tokens is not None else "-",
            item.completion_tokens if item.completion_tokens is not None else "-",
            item.total_tokens if item.total_tokens is not None else "-",
        )
    logger.info(
        "Counts: total_requests=%s total_prompt_tokens=%s total_completion_tokens=%s total_elapsed_ms=%.2f",
        len(summary.requests),
        total_prompt_tokens,
        total_completion_tokens,
        total_elapsed_ms,
    )
    logger.info(TRACE_SUMMARY_DIVIDER)


def _log_request_snapshot(
    *,
    request_id: str,
    trace_id: str,
    sequence: int,
    request: Request,
    provider: ProviderConfig,
    upstream_url: str,
    headers: dict[str, str],
    payload: object,
    is_stream: bool | None,
) -> None:
    """Record summary and debug logs for an outbound upstream request."""
    _record_trace_request(
        trace_id=trace_id,
        request_id=request_id,
        sequence=sequence,
        path=request.url.path,
        payload=payload,
    )
    logger.info(
        "[trace=%s req=%s seq=%s] Request summary method=%s path=%s provider=%s upstream=%s summary=%s",
        trace_id,
        request_id,
        sequence,
        request.method,
        request.url.path,
        provider.name,
        upstream_url,
        _serialize_for_log(_summary_payload(payload)),
    )
    logger.debug(
        "[trace=%s req=%s seq=%s] Received request: %s %s -> provider=%s upstream=%s stream=%s headers=%s\nbody=%s",
        trace_id,
        request_id,
        sequence,
        request.method,
        request.url.path,
        provider.name,
        upstream_url,
        is_stream,
        _serialize_for_log(_redact_headers(headers)),
        _serialize_for_log(payload),
    )


def _log_response_snapshot(
    *,
    request_id: str,
    trace_id: str,
    sequence: int,
    status_code: int,
    content_type: str,
    elapsed_ms: float,
    payload_preview: str,
    payload: object | None = None,
) -> None:
    """Record summary and debug logs for a completed upstream response."""
    usage_summary = ""
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            usage_summary = (
                f" prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} total_tokens={total_tokens}"
            )
    logger.info(
        "[trace=%s req=%s seq=%s] Upstream response status=%s content_type=%s elapsed_ms=%.2f%s",
        trace_id,
        request_id,
        sequence,
        status_code,
        content_type or "<unknown>",
        elapsed_ms,
        usage_summary,
    )
    logger.debug("[trace=%s req=%s seq=%s] Response body=%s", trace_id, request_id, sequence, payload_preview)
    _update_trace_response(
        trace_id=trace_id,
        sequence=sequence,
        status_code=status_code,
        content_type=content_type,
        elapsed_ms=elapsed_ms,
        payload=payload,
    )
    _emit_trace_summary(_complete_trace_request(trace_id, sequence))


def _finalize_stream_trace(
    *,
    request_id: str,
    trace_id: str,
    sequence: int,
    status_code: int,
    content_type: str,
    started_at: float,
) -> None:
    """Close out trace bookkeeping for a streamed upstream response."""
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "[trace=%s req=%s seq=%s] Upstream response status=%s content_type=%s elapsed_ms=%.2f",
        trace_id,
        request_id,
        sequence,
        status_code,
        content_type or "<unknown>",
        elapsed_ms,
    )
    _update_trace_response(
        trace_id=trace_id,
        sequence=sequence,
        status_code=status_code,
        content_type=content_type,
        elapsed_ms=elapsed_ms,
        payload=None,
    )
    _emit_trace_summary(_complete_trace_request(trace_id, sequence))

class ProxyEngine:
    """Forward JSON and multipart requests to upstream AI providers."""

    def __init__(self):
        # Long-lived client for connection pooling
        self._client = httpx.AsyncClient(timeout=300.0)

    async def forward_request(self, request: Request, provider: ProviderConfig, path: str, body_bytes: bytes | None = None, *, is_stream: bool | None = None):
        """Forward a JSON or raw-byte request to the selected provider."""
        url = f"{provider.base_url.rstrip('/')}/{path}"
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("accept-encoding", None)
        headers.pop("authorization", None)
        headers.pop("Authorization", None)
        headers.pop("x-api-key", None)
        headers.pop("X-API-Key", None)
        request_id = _request_id_for(request)
        trace_id = _trace_id_for(request, request_id)
        sequence = _request_sequence_for(trace_id)
        started_at = time.perf_counter()

        if provider.api_key and provider.api_key != "na":
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            if body_bytes is not None:
                multipart_payload = {
                    "content_type": request.headers.get("content-type", ""),
                    "byte_length": len(body_bytes),
                }
                _log_request_snapshot(
                    request_id=request_id,
                    trace_id=trace_id,
                    sequence=sequence,
                    request=request,
                    provider=provider,
                    upstream_url=url,
                    headers=headers,
                    payload=multipart_payload,
                    is_stream=is_stream,
                )

                # Multipart or raw byte forwarding
                # If is_stream is not explicitly passed, can we detect it?
                # For now use the override.
                if is_stream:
                    req = self._client.build_request(
                        "POST",
                        url,
                        headers=headers,
                        content=body_bytes
                    )
                    r = await self._client.send(req, stream=True)
                    logger.info(
                        "[trace=%s req=%s seq=%s] Upstream stream opened status=%s content_type=%s",
                        trace_id,
                        request_id,
                        sequence,
                        r.status_code,
                        r.headers.get("content-type", "<unknown>"),
                    )

                    async def stream_generator():
                        """Yield raw upstream bytes while updating stream progress logs."""
                        try:
                            chunk_count = 0
                            byte_count = 0
                            async for chunk in r.aiter_bytes():
                                if chunk:
                                    chunk_count += 1
                                    byte_count += len(chunk)
                                    if chunk_count % STREAM_PROGRESS_CHUNK_INTERVAL == 0:
                                        logger.info(
                                            "[trace=%s req=%s seq=%s] Stream progress: chunks=%s bytes=%s",
                                            trace_id,
                                            request_id,
                                            sequence,
                                            chunk_count,
                                            byte_count,
                                        )
                                yield chunk
                            logger.info(
                                "[trace=%s req=%s seq=%s] Stream complete status=%s chunks=%s bytes=%s elapsed_ms=%.2f",
                                trace_id,
                                request_id,
                                sequence,
                                r.status_code,
                                chunk_count,
                                byte_count,
                                (time.perf_counter() - started_at) * 1000,
                            )
                        finally:
                            _finalize_stream_trace(
                                request_id=request_id,
                                trace_id=trace_id,
                                sequence=sequence,
                                status_code=r.status_code,
                                content_type=r.headers.get("content-type", ""),
                                started_at=started_at,
                            )
                            await r.aclose()

                    return StreamingResponse(
                        stream_generator(),
                        status_code=r.status_code,
                        media_type=r.headers.get("content-type")
                    )
                else:
                    resp = await self._client.post(url, headers=headers, content=body_bytes)
                    _log_response_snapshot(
                        request_id=request_id,
                        trace_id=trace_id,
                        sequence=sequence,
                        status_code=resp.status_code,
                        content_type=resp.headers.get("content-type", ""),
                        elapsed_ms=(time.perf_counter() - started_at) * 1000,
                        payload_preview=_response_preview(resp.content, resp.headers.get("content-type", "")),
                    )
                    return StreamingResponse(
                        iter([resp.content]),
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type")
                    )
            else:
                # JSON formulation
                body = await request.json()
                if is_stream is None:
                    is_stream = body.get("stream", False)

                # Strip the `store` parameter to prevent 400 Bad Request errors from non-OpenAI models
                if isinstance(body, dict):
                    body.pop("store", None)

                _log_request_snapshot(
                    request_id=request_id,
                    trace_id=trace_id,
                    sequence=sequence,
                    request=request,
                    provider=provider,
                    upstream_url=url,
                    headers=headers,
                    payload=body,
                    is_stream=is_stream,
                )

                req = self._client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    json=body
                )

                if is_stream:
                    r = await self._client.send(req, stream=True)
                    logger.info(
                        "[trace=%s req=%s seq=%s] Upstream stream opened status=%s content_type=%s",
                        trace_id,
                        request_id,
                        sequence,
                        r.status_code,
                        r.headers.get("content-type", "<unknown>"),
                    )

                    async def stream_generator():
                        """Yield streamed JSON or audio bytes from the upstream response."""
                        try:
                            chunk_count = 0
                            byte_count = 0
                            async for chunk in r.aiter_bytes():
                                if chunk:
                                    chunk_count += 1
                                    byte_count += len(chunk)
                                    if chunk_count % STREAM_PROGRESS_CHUNK_INTERVAL == 0:
                                        logger.info(
                                            "[trace=%s req=%s seq=%s] Stream progress: chunks=%s bytes=%s",
                                            trace_id,
                                            request_id,
                                            sequence,
                                            chunk_count,
                                            byte_count,
                                        )
                                    yield chunk
                            logger.info(
                                "[trace=%s req=%s seq=%s] Stream complete status=%s chunks=%s bytes=%s elapsed_ms=%.2f",
                                trace_id,
                                request_id,
                                sequence,
                                r.status_code,
                                chunk_count,
                                byte_count,
                                (time.perf_counter() - started_at) * 1000,
                            )
                        finally:
                            _finalize_stream_trace(
                                request_id=request_id,
                                trace_id=trace_id,
                                sequence=sequence,
                                status_code=r.status_code,
                                content_type=r.headers.get("content-type", ""),
                                started_at=started_at,
                            )
                            await r.aclose()

                    headers = {
                        "X-Accel-Buffering": "no",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    }

                    # Propagate content type from upstream exactly, default to event-stream if text
                    content_type = r.headers.get("content-type", "text/event-stream")
                    if "audio" in content_type.lower():
                        # For audio streaming, do not use SSE headers
                        headers.pop("X-Accel-Buffering", None)

                    return StreamingResponse(
                        stream_generator(),
                        status_code=r.status_code,
                        media_type=content_type,
                        headers=headers
                    )
                else:
                    # For non-streaming requests, we can just read the response synchronously
                    # Using stream=True here caused httpx.ReadErrors with binary data
                    resp = await self._client.send(req)
                    content_type = resp.headers.get("content-type", "")
                    elapsed_ms = (time.perf_counter() - started_at) * 1000

                    if "application/json" in content_type:
                        response_json = resp.json()
                        _log_response_snapshot(
                            request_id=request_id,
                            trace_id=trace_id,
                            sequence=sequence,
                            status_code=resp.status_code,
                            content_type=content_type,
                            elapsed_ms=elapsed_ms,
                            payload_preview=_serialize_for_log(response_json),
                            payload=response_json,
                        )
                        return JSONResponse(content=response_json, status_code=resp.status_code)
                    else:
                        from fastapi import Response
                        # Forward relevant headers from upstream
                        headers = {}
                        if "content-length" in resp.headers:
                            headers["content-length"] = resp.headers["content-length"]
                        _log_response_snapshot(
                            request_id=request_id,
                            trace_id=trace_id,
                            sequence=sequence,
                            status_code=resp.status_code,
                            content_type=content_type,
                            elapsed_ms=elapsed_ms,
                            payload_preview=_response_preview(resp.content, content_type),
                        )

                        return Response(
                            content=resp.content,
                            status_code=resp.status_code,
                            media_type=content_type,
                            headers=headers
                        )
        except Exception as e:
            logger.error(
                "[trace=%s req=%s seq=%s] ProxyEngine error forwarding to %s: %s",
                trace_id,
                request_id,
                sequence,
                url,
                e,
                exc_info=True,
            )
            _trace_summaries.pop(trace_id, None)
            raise ProxyError(detail=str(e)) from e

    async def forward_multipart_request(self, request: Request, provider: ProviderConfig, path: str, data: dict, files: dict, *, is_stream: bool | None = False):
        """Forward multipart form data and file uploads to the selected provider."""
        url = f"{provider.base_url.rstrip('/')}/{path}"
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("accept-encoding", None)
        headers.pop("content-type", None) # httpx will set this with the boundary
        headers.pop("authorization", None)
        headers.pop("Authorization", None)
        headers.pop("x-api-key", None)
        headers.pop("X-API-Key", None)
        request_id = _request_id_for(request)
        trace_id = _trace_id_for(request, request_id)
        sequence = _request_sequence_for(trace_id)
        started_at = time.perf_counter()

        if provider.api_key and provider.api_key != "na":
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            _log_request_snapshot(
                request_id=request_id,
                trace_id=trace_id,
                sequence=sequence,
                request=request,
                provider=provider,
                upstream_url=url,
                headers=headers,
                payload={
                    "form": data,
                    "files": {
                        name: {
                            "filename": file_tuple[0],
                            "content_type": file_tuple[2],
                        }
                        for name, file_tuple in files.items()
                    },
                },
                is_stream=is_stream,
            )
            req = self._client.build_request(
                "POST",
                url,
                headers=headers,
                data=data,
                files=files
            )

            if is_stream:
                r = await self._client.send(req, stream=True)
                logger.info(
                    "[trace=%s req=%s seq=%s] Upstream stream opened status=%s content_type=%s",
                    trace_id,
                    request_id,
                    sequence,
                    r.status_code,
                    r.headers.get("content-type", "<unknown>"),
                )

                async def stream_generator():
                    """Yield streamed multipart response bytes from the upstream response."""
                    try:
                        chunk_count = 0
                        byte_count = 0
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                chunk_count += 1
                                byte_count += len(chunk)
                                if chunk_count % STREAM_PROGRESS_CHUNK_INTERVAL == 0:
                                    logger.info(
                                        "[trace=%s req=%s seq=%s] Stream progress: chunks=%s bytes=%s",
                                        trace_id,
                                        request_id,
                                        sequence,
                                        chunk_count,
                                        byte_count,
                                    )
                                yield chunk
                        logger.info(
                            "[trace=%s req=%s seq=%s] Stream complete status=%s chunks=%s bytes=%s elapsed_ms=%.2f",
                            trace_id,
                            request_id,
                            sequence,
                            r.status_code,
                            chunk_count,
                            byte_count,
                            (time.perf_counter() - started_at) * 1000,
                        )
                    finally:
                        _finalize_stream_trace(
                            request_id=request_id,
                            trace_id=trace_id,
                            sequence=sequence,
                            status_code=r.status_code,
                            content_type=r.headers.get("content-type", ""),
                            started_at=started_at,
                        )
                        await r.aclose()

                resp_headers = {
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }

                content_type = r.headers.get("content-type", "text/event-stream")

                return StreamingResponse(
                    stream_generator(),
                    status_code=r.status_code,
                    media_type=content_type,
                    headers=resp_headers
                )
            else:
                resp = await self._client.send(req)
                _log_response_snapshot(
                    request_id=request_id,
                    trace_id=trace_id,
                    sequence=sequence,
                    status_code=resp.status_code,
                    content_type=resp.headers.get("content-type", ""),
                    elapsed_ms=(time.perf_counter() - started_at) * 1000,
                    payload_preview=_response_preview(resp.content, resp.headers.get("content-type", "")),
                )
                return StreamingResponse(
                    iter([resp.content]),
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type")
                )
        except Exception as e:
            logger.error(
                "[trace=%s req=%s seq=%s] ProxyEngine error forwarding to %s: %s",
                trace_id,
                request_id,
                sequence,
                url,
                e,
                exc_info=True,
            )
            _trace_summaries.pop(trace_id, None)
            raise ProxyError(detail=str(e)) from e

proxy_engine = ProxyEngine()
