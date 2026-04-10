import pytest
from httpx import AsyncClient, ASGITransport
from server.main import app
from unittest.mock import AsyncMock, patch
import json

@pytest.mark.asyncio
async def test_admin_config_providers_get():
    """Test /api/config/providers returns the current configuration."""
    from server.core.config import ProviderConfig
    mock_providers = [
        ProviderConfig(name="P1", base_url="http://p1/v1", api_key="k1", type="llm")
    ]
    
    with patch("server.api.admin.settings") as mock_settings:
        mock_settings.PROVIDERS = mock_providers
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/config/providers")
            
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "P1"

@pytest.mark.asyncio
async def test_admin_config_add_provider():
    """Test adding a new provider via POST."""
    new_provider = {
        "name": "NewP",
        "base_url": "http://newp/v1",
        "api_key": "newk",
        "type": "stt"
    }
    
    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/api/config/providers", json=new_provider)
                
            assert response.status_code == 200
            assert mock_update.called
