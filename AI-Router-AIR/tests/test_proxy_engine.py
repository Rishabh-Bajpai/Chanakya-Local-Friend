import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
from server.core.proxy_engine import ProxyEngine
from server.core.exceptions import ProxyError
from server.schemas.provider_schema import ProviderConfig


class TestProxyEngine:
    """Test ProxyEngine for forwarding requests to upstream providers"""

    @pytest.fixture
    def proxy_engine(self):
        """Create a ProxyEngine instance for testing"""
        return ProxyEngine()

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider config"""
        return ProviderConfig(
            type="llm",
            base_url="http://upstream.test/v1",
            api_key="test-api-key",
            name="Test Provider"
        )

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request"""
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {"user-agent": "test-client"}
        mock_req.method = "POST"
        return mock_req

    @pytest.mark.asyncio
    async def test_forward_request_json_non_streaming(self, proxy_engine, mock_provider, mock_request):
        """Test forwarding a non-streaming JSON request"""
        async def mock_json():
            return {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}

        mock_request.json = mock_json

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hello!"}}]}

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions",
                    is_stream=False
                )

                # Verify result is JSONResponse
                assert isinstance(result, JSONResponse)
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_forward_request_adds_authorization_header(self, proxy_engine, mock_provider, mock_request):
        """Test that API key is added to Authorization header"""
        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions",
                    is_stream=False
                )

                # Check that build_request was called with correct headers
                call_kwargs = mock_build.call_args[1]
                assert "Authorization" in call_kwargs["headers"]
                assert call_kwargs["headers"]["Authorization"] == "Bearer test-api-key"

    @pytest.mark.asyncio
    async def test_forward_request_no_api_key_if_na(self, proxy_engine, mock_request):
        """Test that Authorization header is not added when api_key is 'na'"""
        provider_no_key = ProviderConfig(
            type="llm",
            base_url="http://upstream.test/v1",
            api_key="na",
            name="Test Provider"
        )

        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                await proxy_engine.forward_request(
                    mock_request,
                    provider_no_key,
                    "chat/completions",
                    is_stream=False
                )

                # Check that Authorization header is not present
                call_kwargs = mock_build.call_args[1]
                assert "Authorization" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_forward_request_strips_host_header(self, proxy_engine, mock_provider, mock_request):
        """Test that proxy-only transport headers are stripped"""
        mock_request.headers = {
            "host": "original-host.com",
            "content-length": "123",
            "accept-encoding": "gzip, deflate, br",
            "user-agent": "test"
        }

        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions",
                    is_stream=False
                )

                call_kwargs = mock_build.call_args[1]
                assert "host" not in call_kwargs["headers"]
                assert "content-length" not in call_kwargs["headers"]
                assert "accept-encoding" not in call_kwargs["headers"]
                assert "user-agent" in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_forward_request_constructs_correct_url(self, proxy_engine, mock_provider, mock_request):
        """Test that URL is constructed correctly"""
        async def mock_json():
            return {}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions"
                )

                # Check URL construction
                call_args = mock_build.call_args[0]
                assert call_args[1] == "http://upstream.test/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_forward_request_streaming_json(self, proxy_engine, mock_provider, mock_request):
        """Test forwarding a streaming JSON request"""
        async def mock_json():
            return {"model": "gpt-4", "messages": [], "stream": True}

        mock_request.json = mock_json

        # Mock streaming response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}

        async def mock_aiter_bytes():
            yield b"data: chunk1\n\n"
            yield b"data: chunk2\n\n"

        mock_response.aiter_bytes = mock_aiter_bytes
        mock_response.aclose = AsyncMock()

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions",
                    is_stream=True
                )

                # Verify result is StreamingResponse
                assert isinstance(result, StreamingResponse)
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_forward_request_detects_stream_from_body(self, proxy_engine, mock_provider, mock_request):
        """Test that stream is detected from request body"""
        async def mock_json():
            return {"model": "gpt-4", "stream": True}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}

        async def mock_aiter_bytes():
            yield b"data: test\n\n"

        mock_response.aiter_bytes = mock_aiter_bytes
        mock_response.aclose = AsyncMock()

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                # Don't pass is_stream, let it detect from body
                result = await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "chat/completions"
                )

                assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_forward_request_non_json_response(self, proxy_engine, mock_provider, mock_request):
        """Test handling non-JSON response (e.g., binary audio)"""
        async def mock_json():
            return {"model": "tts-1", "input": "hello"}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "audio/mpeg", "content-length": "12345"}
        mock_response.content = b"fake-audio-data"

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "audio/speech",
                    is_stream=False
                )

                # Should return Response with binary content
                assert result.status_code == 200
                assert result.media_type == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_forward_request_with_body_bytes(self, proxy_engine, mock_provider, mock_request):
        """Test forwarding request with raw body bytes"""
        body_bytes = b'{"test": "data"}'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{"result": "ok"}'

        with patch.object(proxy_engine._client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await proxy_engine.forward_request(
                mock_request,
                mock_provider,
                "test/endpoint",
                body_bytes=body_bytes,
                is_stream=False
            )

            assert isinstance(result, StreamingResponse)
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_forward_request_exception_raises_proxy_error(self, proxy_engine, mock_provider, mock_request):
        """Test that exceptions are caught and raise ProxyError"""
        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.side_effect = httpx.ConnectError("Connection failed")

                with pytest.raises(ProxyError) as exc_info:
                    await proxy_engine.forward_request(
                        mock_request,
                        mock_provider,
                        "chat/completions"
                    )

                assert "Connection failed" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_forward_multipart_request(self, proxy_engine, mock_provider, mock_request):
        """Test forwarding multipart form data request"""
        data = {"model": "whisper-1", "language": "en"}
        files = {"file": ("audio.wav", b"audio-data", "audio/wav")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{"text": "transcription"}'

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_multipart_request(
                    mock_request,
                    mock_provider,
                    "audio/transcriptions",
                    data=data,
                    files=files,
                    is_stream=False
                )

                assert isinstance(result, StreamingResponse)
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_forward_multipart_request_streaming(self, proxy_engine, mock_provider, mock_request):
        """Test forwarding streaming multipart request"""
        data = {"model": "whisper-1"}
        files = {"file": ("audio.wav", b"audio-data", "audio/wav")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/event-stream"}

        async def mock_aiter_bytes():
            yield b"data: chunk\n\n"

        mock_response.aiter_bytes = mock_aiter_bytes
        mock_response.aclose = AsyncMock()

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_multipart_request(
                    mock_request,
                    mock_provider,
                    "audio/transcriptions",
                    data=data,
                    files=files,
                    is_stream=True
                )

                assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_forward_multipart_strips_content_type_header(self, proxy_engine, mock_provider, mock_request):
        """Test that content-type header is stripped for multipart (httpx sets it)"""
        mock_request.headers = {
            "content-type": "multipart/form-data; boundary=----WebKitFormBoundary",
            "user-agent": "test"
        }

        data = {"model": "whisper-1"}
        files = {"file": ("audio.wav", b"audio", "audio/wav")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{}'

        with patch.object(proxy_engine._client, 'build_request') as mock_build:
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                await proxy_engine.forward_multipart_request(
                    mock_request,
                    mock_provider,
                    "audio/transcriptions",
                    data=data,
                    files=files
                )

                call_kwargs = mock_build.call_args[1]
                assert "content-type" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_forward_multipart_request_exception_raises_proxy_error(self, proxy_engine, mock_provider, mock_request):
        """Test that multipart request exceptions raise ProxyError"""
        data = {"model": "whisper-1"}
        files = {"file": ("audio.wav", b"audio", "audio/wav")}

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.side_effect = httpx.TimeoutException("Request timeout")

                with pytest.raises(ProxyError) as exc_info:
                    await proxy_engine.forward_multipart_request(
                        mock_request,
                        mock_provider,
                        "audio/transcriptions",
                        data=data,
                        files=files
                    )

                assert "Request timeout" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_forward_request_audio_streaming_headers(self, proxy_engine, mock_provider, mock_request):
        """Test that audio streaming doesn't use X-Accel-Buffering header"""
        async def mock_json():
            return {"model": "tts-1", "input": "hello", "stream": True}

        mock_request.json = mock_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "audio/mpeg"}

        async def mock_aiter_bytes():
            yield b"audio-chunk"

        mock_response.aiter_bytes = mock_aiter_bytes
        mock_response.aclose = AsyncMock()

        with patch.object(proxy_engine._client, 'build_request'):
            with patch.object(proxy_engine._client, 'send', new_callable=AsyncMock) as mock_send:
                mock_send.return_value = mock_response

                result = await proxy_engine.forward_request(
                    mock_request,
                    mock_provider,
                    "audio/speech",
                    is_stream=True
                )

                # For audio, X-Accel-Buffering should not be in headers
                assert "X-Accel-Buffering" not in result.headers

    @pytest.mark.asyncio
    async def test_proxy_engine_reuses_client(self, proxy_engine):
        """Test that ProxyEngine reuses the same httpx client"""
        client1 = proxy_engine._client
        client2 = proxy_engine._client

        assert client1 is client2
        assert isinstance(client1, httpx.AsyncClient)
