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
async def test_chat_completions_proxy():
    """Test /v1/chat/completions correctly calls the proxy engine."""
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}]
    }
    
    from server.core.config import ProviderConfig
    mock_provider = ProviderConfig(name="P1", base_url="http://p1/v1", api_key="na", type="llm")
    
    # Mock proxy_engine.forward_request
    mock_proxy_resp = MagicMock()
    mock_proxy_resp.status_code = 200
    
    with patch("server.api.v1.chat.get_provider", return_value=mock_provider):
        with patch("server.api.v1.chat.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/chat/completions", json=payload)
                
            assert response.status_code == 200
            mock_forward.assert_called_once()

@pytest.mark.asyncio
async def test_audio_speech_proxy():
    """Test /v1/audio/speech correctly calls the proxy engine."""
    payload = {
        "model": "tts-1",
        "input": "hello"
    }
    
    mock_proxy_resp = MagicMock()
    mock_proxy_resp.status_code = 200
    
    from server.core.config import ProviderConfig
    mock_provider = ProviderConfig(name="P2", base_url="http://p2/v1", api_key="na", type="tts")

    with patch("server.api.v1.audio.get_provider", return_value=mock_provider):
        with patch("server.api.v1.audio.proxy_engine.forward_request", new_callable=AsyncMock) as mock_forward:
            mock_forward.return_value = mock_proxy_resp
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/audio/speech", json=payload)
                
            assert response.status_code == 200
            mock_forward.assert_called_once()
