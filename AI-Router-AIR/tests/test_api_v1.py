import pytest
from httpx import AsyncClient, ASGITransport
from server.main import app
from unittest.mock import AsyncMock, patch, MagicMock
import json

@pytest.mark.asyncio
async def test_models_endpoint():
    """Test /v1/models returns the aggregated registry."""
    mock_models = [
        {"id": "gpt-4", "provider_name": "P1", "provider_type": "llm"},
        {"id": "whisper-1", "provider_name": "P2", "provider_type": "stt"}
    ]

    with patch("server.api.v1.models.provider_manager") as mock_pm:
        mock_pm.models_cache = {"all": mock_models}
        mock_pm.refresh_models = AsyncMock(return_value=mock_models)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Note the trailing slash to avoid 307 redirect redirect_slashes
            response = await ac.get("/v1/models/")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) == 2
        assert data["data"][0]["id"] == "gpt-4"

@pytest.mark.asyncio
async def test_models_endpoint_refresh_forces_reload():
    """Test /v1/models refresh=true bypasses the cached registry."""
    cached_models = [
        {"id": "cached-model", "provider_name": "P1", "provider_type": "llm"}
    ]
    refreshed_models = [
        {"id": "fresh-model", "provider_name": "P2", "provider_type": "tts"}
    ]

    with patch("server.api.v1.models.provider_manager") as mock_pm:
        mock_pm.models_cache = {"all": cached_models}
        mock_pm.refresh_models = AsyncMock(return_value=refreshed_models)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/models/?refresh=true")

        assert response.status_code == 200
        data = response.json()
        assert data["data"] == refreshed_models
        mock_pm.refresh_models.assert_awaited_once()

@pytest.mark.asyncio
async def test_chat_completions_proxy():
    """Test /v1/chat/completions correctly calls the proxy engine."""
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}]
    }

    from server.core.config import ProviderConfig
    from server.core.dependencies import get_provider
    from fastapi.responses import JSONResponse
    mock_provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="na", type="llm")

    # Override the dependency
    async def override_get_provider():
        return mock_provider

    app.dependency_overrides[get_provider] = override_get_provider

    # Mock proxy_engine.forward_request to return a proper response
    mock_proxy_resp = JSONResponse(content={"choices": [{"message": {"content": "test"}}]}, status_code=200)

    try:
        with patch("server.api.v1.chat.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/chat/completions", json=payload)

            assert response.status_code == 200
            mock_forward.assert_called_once()
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_audio_speech_proxy():
    """Test /v1/audio/speech correctly calls the proxy engine."""
    payload = {
        "model": "tts-1",
        "input": "hello"
    }

    from server.core.config import ProviderConfig
    from server.core.dependencies import get_provider
    from fastapi.responses import Response
    mock_provider = ProviderConfig(name="P2", base_url="http://p2/v1", api_key="na", type="tts")

    mock_proxy_resp = Response(content=b"fake-audio", status_code=200, media_type="audio/mpeg")

    # Override the dependency
    async def override_get_provider():
        return mock_provider

    app.dependency_overrides[get_provider] = override_get_provider

    try:
        with patch("server.api.v1.audio.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/audio/speech", json=payload)

            assert response.status_code == 200
            mock_forward.assert_called_once()
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_audio_speech_streaming():
    """Test /v1/audio/speech with streaming enabled."""
    payload = {
        "model": "tts-1",
        "input": "hello",
        "stream": True
    }

    from server.core.config import ProviderConfig
    from server.core.dependencies import get_provider
    from fastapi.responses import StreamingResponse
    mock_provider = ProviderConfig(name="TTS", base_url="http://tts/v1", api_key="na", type="tts")

    async def fake_stream():
        yield b"audio-chunk"

    mock_proxy_resp = StreamingResponse(fake_stream(), status_code=200, media_type="audio/mpeg")

    # Override the dependency
    async def override_get_provider():
        return mock_provider

    app.dependency_overrides[get_provider] = override_get_provider

    try:
        with patch("server.api.v1.audio.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/audio/speech", json=payload)

            assert response.status_code == 200
            # Verify is_stream parameter was passed
            call_args = mock_forward.call_args
            assert call_args[1]["is_stream"] is True
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_audio_transcriptions_multipart():
    """Test /v1/audio/transcriptions with multipart form data."""
    from server.core.config import ProviderConfig
    mock_provider = ProviderConfig(name="STT", base_url="http://stt/v1", api_key="na", type="stt")

    mock_proxy_resp = MagicMock()
    mock_proxy_resp.status_code = 200

    with patch("server.api.v1.audio.provider_manager") as mock_pm:
        mock_pm.get_provider_for_model.return_value = mock_provider

        with patch("server.api.v1.audio.proxy_engine.forward_multipart_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                files = {"file": ("audio.wav", b"fake-audio-data", "audio/wav")}
                data = {"model": "whisper-1"}
                response = await ac.post("/v1/audio/transcriptions", files=files, data=data)

            assert response.status_code == 200
            mock_forward.assert_called_once()

@pytest.mark.asyncio
async def test_audio_transcriptions_fallback_to_type():
    """Test /v1/audio/transcriptions falls back to provider by type."""
    from server.core.config import ProviderConfig
    mock_provider = ProviderConfig(name="STT", base_url="http://stt/v1", api_key="na", type="stt")

    mock_proxy_resp = MagicMock()
    mock_proxy_resp.status_code = 200

    with patch("server.api.v1.audio.provider_manager") as mock_pm:
        mock_pm.get_provider_for_model.return_value = None
        mock_pm.get_provider_by_type.return_value = mock_provider

        with patch("server.api.v1.audio.proxy_engine.forward_multipart_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                files = {"file": ("audio.wav", b"fake-audio", "audio/wav")}
                response = await ac.post("/v1/audio/transcriptions", files=files)

            assert response.status_code == 200

@pytest.mark.asyncio
async def test_audio_transcriptions_no_provider_error():
    """Test /v1/audio/transcriptions returns error when no provider."""
    with patch("server.api.v1.audio.provider_manager") as mock_pm:
        mock_pm.get_provider_for_model.return_value = None
        mock_pm.get_provider_by_type.return_value = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            files = {"file": ("audio.wav", b"fake", "audio/wav")}
            response = await ac.post("/v1/audio/transcriptions", files=files)

        assert response.status_code == 503

@pytest.mark.asyncio
async def test_audio_translations():
    """Test /v1/audio/translations endpoint."""
    from server.core.config import ProviderConfig
    mock_provider = ProviderConfig(name="STT", base_url="http://stt/v1", api_key="na", type="stt")

    mock_proxy_resp = MagicMock()
    mock_proxy_resp.status_code = 200

    with patch("server.api.v1.audio.provider_manager") as mock_pm:
        mock_pm.get_provider_for_model.return_value = mock_provider

        with patch("server.api.v1.audio.proxy_engine.forward_multipart_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                files = {"file": ("audio.wav", b"audio", "audio/wav")}
                data = {"model": "whisper-1"}
                response = await ac.post("/v1/audio/translations", files=files, data=data)

            assert response.status_code == 200

@pytest.mark.asyncio
async def test_chat_completions_streaming():
    """Test /v1/chat/completions with streaming enabled."""
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True
    }

    from server.core.config import ProviderConfig
    from server.core.dependencies import get_provider
    from fastapi.responses import StreamingResponse
    mock_provider = ProviderConfig(name="LLM", base_url="http://llm/v1", api_key="na", type="llm")

    async def fake_stream():
        yield b"data: test\n\n"

    mock_proxy_resp = StreamingResponse(fake_stream(), status_code=200, media_type="text/event-stream")

    # Override the dependency
    async def override_get_provider():
        return mock_provider

    app.dependency_overrides[get_provider] = override_get_provider

    try:
        with patch("server.api.v1.chat.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/chat/completions", json=payload)

            assert response.status_code == 200
            mock_forward.assert_called_once()
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_models_endpoint_with_refresh():
    """Test /v1/models with refresh parameter."""
    mock_models = [
        {"id": "gpt-4", "provider_name": "P1", "provider_type": "llm"}
    ]

    with patch("server.api.v1.models.provider_manager") as mock_pm:
        mock_pm.refresh_models = AsyncMock(return_value=mock_models)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/models/?refresh=true")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        mock_pm.refresh_models.assert_called_once()

@pytest.mark.asyncio
async def test_models_endpoint_empty_cache_refreshes():
    """Test /v1/models refreshes when cache is empty."""
    mock_models = [
        {"id": "model1", "provider_name": "P1", "provider_type": "llm"}
    ]

    with patch("server.api.v1.models.provider_manager") as mock_pm:
        mock_pm.models_cache = {"all": []}
        mock_pm.refresh_models = AsyncMock(return_value=mock_models)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/models/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1
        mock_pm.refresh_models.assert_called_once()

@pytest.mark.asyncio
async def test_models_endpoint_uses_cache():
    """Test /v1/models uses cache when available."""
    cached_models = [
        {"id": "cached-model", "provider_name": "P1", "provider_type": "llm"}
    ]

    with patch("server.api.v1.models.provider_manager") as mock_pm:
        mock_pm.models_cache = {"all": cached_models}
        mock_pm.refresh_models = AsyncMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/models/")

        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["id"] == "cached-model"
        # Should not refresh when cache exists
        mock_pm.refresh_models.assert_not_called()
