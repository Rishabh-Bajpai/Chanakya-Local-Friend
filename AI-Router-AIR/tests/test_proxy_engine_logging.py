import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request

from server.core.proxy_engine import ProxyEngine
from server.schemas.provider_schema import ProviderConfig


def _build_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer secret-token"),
            (b"x-request-id", b"req-123"),
            (b"x-chanakya-request-id", b"trace-abc"),
            (b"x-session-id", b"sess-1"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.request", "body": b"", "more_body": False}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _render_log_calls(call_args_list: list) -> list[str]:
    rendered = []
    for call in call_args_list:
        fmt, *args = call.args
        rendered.append(fmt % tuple(args) if args else fmt)
    return rendered


def test_forward_request_logs_request_and_response_details():
    async def run_test() -> None:
        engine = ProxyEngine()
        payload = {
            "model": "gpt-4",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        }
        request = _build_request(payload)
        provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="secret-key", type="llm")
        upstream_response = httpx.Response(
            200,
            json={"id": "chatcmpl-123", "choices": [{"message": {"role": "assistant", "content": "hello"}}]},
            request=httpx.Request("POST", "http://p1/v1/chat/completions"),
        )

        with patch("server.core.proxy_engine.logger") as mock_logger:
            with patch.object(engine._client, "send", new=AsyncMock(return_value=upstream_response)) as mock_send:
                response = await engine.forward_request(request, provider, "chat/completions")

        assert response.status_code == 200
        mock_send.assert_awaited_once()

        debug_messages = _render_log_calls(mock_logger.debug.call_args_list)
        info_messages = _render_log_calls(mock_logger.info.call_args_list)

        assert any(
            "[trace=trace-abc req=req-123 seq=1] Request summary method=POST path=/v1/chat/completions" in message
            and '"messages": 1' in message
            and '"model": "gpt-4"' in message
            for message in info_messages
        )
        assert any(
            "[trace=trace-abc req=req-123 seq=1] Received request: POST /v1/chat/completions" in message
            for message in debug_messages
        )
        assert any('"model": "gpt-4"' in message for message in debug_messages)
        assert any("<redacted>" in message for message in debug_messages)
        assert any(
            "[trace=trace-abc req=req-123 seq=1] Response body=" in message and "chatcmpl-123" in message
            for message in debug_messages
        )
        assert any(
            "[trace=trace-abc req=req-123 seq=1] Upstream response status=200 content_type=application/json" in message
            for message in info_messages
        )

    asyncio.run(run_test())


def test_forward_request_logs_trace_summary_block():
    async def run_test() -> None:
        engine = ProxyEngine()
        payload = {
            "model": "gpt-4",
            "stream": False,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "Hi"},
            ],
            "tools": [{"type": "function"}],
        }
        request = _build_request(payload)
        provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="secret-key", type="llm")
        upstream_response = httpx.Response(
            200,
            json={
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
            request=httpx.Request("POST", "http://p1/v1/chat/completions"),
        )

        with patch("server.core.proxy_engine.logger") as mock_logger:
            with patch.object(engine._client, "send", new=AsyncMock(return_value=upstream_response)):
                await engine.forward_request(request, provider, "chat/completions")

        info_messages = _render_log_calls(mock_logger.info.call_args_list)

        assert any("Trace Summary: trace-abc" in message for message in info_messages)
        assert any("Message: Hi" in message for message in info_messages)
        assert any("Top-level request: req-123" in message for message in info_messages)
        assert any("Counts: total_requests=1 total_prompt_tokens=12 total_completion_tokens=3" in message for message in info_messages)
        assert any("-----------------------------*****-----------------------------" in message for message in info_messages)

    asyncio.run(run_test())


def test_forward_request_shared_trace_emits_single_summary_after_last_response():
    async def run_test() -> None:
        engine = ProxyEngine()
        provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="secret-key", type="llm")
        gate = asyncio.Event()
        started = 0

        async def send_side_effect(*_args, **_kwargs):
            nonlocal started
            started += 1
            if started == 2:
                gate.set()
            await gate.wait()
            return httpx.Response(
                200,
                json={"id": f"chatcmpl-{started}", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
                request=httpx.Request("POST", "http://p1/v1/chat/completions"),
            )

        request_one = _build_request({"model": "gpt-4", "messages": [{"role": "user", "content": "first"}]})
        request_two = _build_request({"model": "gpt-4", "messages": [{"role": "user", "content": "second"}]})

        with patch("server.core.proxy_engine.logger") as mock_logger:
            with patch.object(engine._client, "send", new=AsyncMock(side_effect=send_side_effect)):
                await asyncio.gather(
                    engine.forward_request(request_one, provider, "chat/completions"),
                    engine.forward_request(request_two, provider, "chat/completions"),
                )

        info_messages = _render_log_calls(mock_logger.info.call_args_list)
        trace_summaries = [message for message in info_messages if "Trace Summary: trace-abc" in message]

        assert len(trace_summaries) == 1
        assert any("Counts: total_requests=2 total_prompt_tokens=2 total_completion_tokens=2" in message for message in info_messages)
        assert any("seq=1" in message for message in info_messages)
        assert any("seq=2" in message for message in info_messages)

    asyncio.run(run_test())


def test_streaming_request_logs_summary_after_stream_completion():
    async def run_test() -> None:
        engine = ProxyEngine()
        payload = {
            "model": "gpt-4",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }
        request = _build_request(payload)
        provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="secret-key", type="llm")
        upstream_response = AsyncMock()
        upstream_response.status_code = 200
        upstream_response.headers = {"content-type": "text/event-stream"}

        async def iter_bytes():
            yield b"data: chunk\n\n"

        upstream_response.aiter_bytes = iter_bytes
        upstream_response.aclose = AsyncMock()

        with patch("server.core.proxy_engine.logger") as mock_logger:
            with patch.object(engine._client, "send", new=AsyncMock(return_value=upstream_response)):
                response = await engine.forward_request(request, provider, "chat/completions")
                chunks = [chunk async for chunk in response.body_iterator]

        assert chunks == [b"data: chunk\n\n"]
        info_messages = _render_log_calls(mock_logger.info.call_args_list)
        assert any("[trace=trace-abc req=req-123 seq=1] Upstream response status=200 content_type=text/event-stream" in message for message in info_messages)
        assert any("Trace Summary: trace-abc" in message for message in info_messages)
        upstream_response.aclose.assert_awaited_once()

    asyncio.run(run_test())
