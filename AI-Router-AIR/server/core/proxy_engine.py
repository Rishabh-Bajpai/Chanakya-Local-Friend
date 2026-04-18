from fastapi import Request
import httpx
from fastapi.responses import StreamingResponse, JSONResponse
from server.schemas.provider_schema import ProviderConfig
from server.core.exceptions import ProxyError
from server.core.logging import logger

class ProxyEngine:
    def __init__(self):
        # Long-lived client for connection pooling
        self._client = httpx.AsyncClient(timeout=60.0)

    async def forward_request(self, request: Request, provider: ProviderConfig, path: str, body_bytes: bytes | None = None, *, is_stream: bool | None = None):
        url = f"{provider.base_url.rstrip('/')}/{path}"
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        
        if provider.api_key and provider.api_key != "na":
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            if body_bytes is not None:
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
                    
                    async def stream_generator():
                        try:
                            async for chunk in r.aiter_bytes():
                                yield chunk
                        finally:
                            await r.aclose()

                    return StreamingResponse(
                        stream_generator(),
                        status_code=r.status_code,
                        media_type=r.headers.get("content-type")
                    )
                else:
                    resp = await self._client.post(url, headers=headers, content=body_bytes)
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
                
                req = self._client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    json=body
                )
                
                if is_stream:
                    r = await self._client.send(req, stream=True)
                    
                    async def stream_generator():
                        try:
                            chunk_count = 0
                            async for chunk in r.aiter_bytes():
                                if chunk:
                                    chunk_count += 1
                                    if chunk_count % 10 == 0:
                                        logger.info(f"Stream progress: {chunk_count} chunks forwarded to client")
                                    yield chunk
                            logger.info(f"Stream complete: {chunk_count} total chunks forwarded")
                        finally:
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
                    
                    if "application/json" in content_type:
                        return JSONResponse(content=resp.json(), status_code=resp.status_code)
                    else:
                        from fastapi import Response
                        # Forward relevant headers from upstream
                        headers = {}
                        if "content-length" in resp.headers:
                            headers["content-length"] = resp.headers["content-length"]
                        
                        return Response(
                            content=resp.content,
                            status_code=resp.status_code,
                            media_type=content_type,
                            headers=headers
                        )
        except Exception as e:
            logger.error(f"ProxyEngine error forwarding to {url}: {e}")
            raise ProxyError(detail=str(e)) from e

    async def forward_multipart_request(self, request: Request, provider: ProviderConfig, path: str, data: dict, files: dict, *, is_stream: bool | None = False):
        url = f"{provider.base_url.rstrip('/')}/{path}"
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("content-type", None) # httpx will set this with the boundary
        
        if provider.api_key and provider.api_key != "na":
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            req = self._client.build_request(
                "POST",
                url,
                headers=headers,
                data=data,
                files=files
            )
            
            if is_stream:
                r = await self._client.send(req, stream=True)
                
                async def stream_generator():
                    try:
                        chunk_count = 0
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                chunk_count += 1
                                yield chunk
                    finally:
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
                return StreamingResponse(
                    iter([resp.content]),
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type")
                )
        except Exception as e:
            logger.error(f"ProxyEngine error forwarding to {url}: {e}")
            raise ProxyError(detail=str(e)) from e

proxy_engine = ProxyEngine()
