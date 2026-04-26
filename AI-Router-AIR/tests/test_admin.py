import pytest
from httpx import AsyncClient, ASGITransport
from server.main import app
from unittest.mock import AsyncMock, patch, MagicMock
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
            with patch("server.api.admin.refresh_model_registry", new_callable=AsyncMock) as mock_refresh:
                with patch("server.api.admin.os.getenv", return_value=None):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                        response = await ac.post("/api/config/providers", json=new_provider)

                assert response.status_code == 200
                assert mock_update.called
                mock_refresh.assert_awaited_once()

@pytest.mark.asyncio
async def test_admin_config_add_provider_llm():
    """Test adding an LLM provider."""
    new_provider = {
        "base_url": "http://llm/v1",
        "api_key": "llm-key",
        "type": "llm"
    }

    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            with patch("server.api.admin.os.getenv", return_value=None):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post("/api/config/providers", json=new_provider)

                assert response.status_code == 200
                data = response.json()
                assert "index" in data
                assert data["index"] == 1

@pytest.mark.asyncio
async def test_admin_config_add_provider_invalid_type():
    """Test adding provider with invalid type returns error."""
    new_provider = {
        "base_url": "http://test/v1",
        "api_key": "key",
        "type": "invalid"
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/config/providers", json=new_provider)

    assert response.status_code == 400
    assert "Invalid provider type" in response.json()["detail"]

@pytest.mark.asyncio
async def test_admin_config_update_provider():
    """Test updating an existing provider."""
    update_data = {
        "base_url": "http://updated/v1",
        "api_key": "updated-key",
        "type": "llm"
    }

    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            with patch("server.api.admin.refresh_model_registry", new_callable=AsyncMock) as mock_refresh:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.put("/api/config/providers/1", json=update_data)

            assert response.status_code == 200
            data = response.json()
            assert data["index"] == 1
            assert mock_update.called
            mock_refresh.assert_awaited_once()

@pytest.mark.asyncio
async def test_admin_config_update_provider_na_api_key():
    """Test updating provider with 'na' API key."""
    update_data = {
        "base_url": "http://test/v1",
        "api_key": "na",
        "type": "tts"
    }

    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.put("/api/config/providers/2", json=update_data)

            assert response.status_code == 200
            # Should still call update with "na"
            assert mock_update.called

@pytest.mark.asyncio
async def test_admin_config_delete_provider():
    """Test deleting a provider."""
    with patch("server.api.admin.settings.remove_env_variable") as mock_remove:
        with patch("server.api.admin.settings.reload"):
            with patch("server.api.admin.refresh_model_registry", new_callable=AsyncMock) as mock_refresh:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.delete("/api/config/providers/1?type=llm")

            assert response.status_code == 200
            assert mock_remove.called
            mock_refresh.assert_awaited_once()

@pytest.mark.asyncio
async def test_admin_config_delete_provider_invalid_type():
    """Test deleting provider with invalid type."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/api/config/providers/1?type=invalid")

    assert response.status_code == 400

@pytest.mark.asyncio
async def test_admin_check_provider_status_online():
    """Test checking provider status when online."""
    provider_data = {
        "base_url": "http://test/v1",
        "api_key": "key",
        "type": "llm"
    }

    # Create mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"id": "model1"}, {"id": "model2"}]}

    # Create mock client instance
    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    # Patch AsyncClient to return our mock instance
    with patch("server.api.admin.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/config/providers/check", json=provider_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert "2 models" in data["details"]

@pytest.mark.asyncio
async def test_admin_check_provider_status_offline():
    """Test checking provider status when offline."""
    provider_data = {
        "base_url": "http://unreachable/v1",
        "api_key": "key",
        "type": "llm"
    }

    with patch("server.api.admin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Connection error")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/config/providers/check", json=provider_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "offline"

@pytest.mark.asyncio
async def test_admin_check_provider_status_error():
    """Test checking provider status when HTTP error."""
    provider_data = {
        "base_url": "http://error/v1",
        "api_key": "key",
        "type": "llm"
    }

    mock_response = AsyncMock()
    mock_response.status_code = 500

    with patch("server.api.admin.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/config/providers/check", json=provider_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "500" in data["details"]

@pytest.mark.asyncio
async def test_admin_get_all_status():
    """Test getting status of all configured providers."""
    from server.core.config import ProviderConfig
    mock_providers = [
        ProviderConfig(name="P1", base_url="http://p1/v1", api_key="k1", type="llm")
    ]

    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("server.api.admin.settings") as mock_settings:
        mock_settings.PROVIDERS = mock_providers

        with patch("server.api.admin.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/config/status")

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["name"] == "P1"
            assert data[0]["status"] == "online"

@pytest.mark.asyncio
async def test_admin_get_discovered_providers_disabled():
    """Test getting discovered providers when discovery is disabled."""
    with patch("server.api.admin.settings") as mock_settings:
        mock_settings.DISCOVERY_ENABLED = False

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/config/discovered")

        assert response.status_code == 200
        assert response.json() == []

@pytest.mark.asyncio
async def test_admin_accept_discovered_providers():
    """Test accepting discovered providers."""
    providers_to_accept = [
        {
            "name": "Discovered1",
            "base_url": "http://disc1/v1",
            "detected_types": ["llm"],
            "api_key": "na"
        }
    ]

    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            with patch("server.api.admin.refresh_model_registry", new_callable=AsyncMock) as mock_refresh:
                with patch("server.api.admin.os.getenv", return_value=None):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                        response = await ac.post("/api/config/discovered/accept", json=providers_to_accept)

                assert response.status_code == 200
                data = response.json()
                assert "added" in data
                assert len(data["added"]) > 0
                mock_refresh.assert_awaited_once()

@pytest.mark.asyncio
async def test_admin_accept_discovered_providers_multiple_types():
    """Test accepting provider with multiple detected types."""
    providers_to_accept = [
        {
            "name": "MultiProvider",
            "base_url": "http://multi/v1",
            "detected_types": ["llm", "tts"],
            "api_key": "multi-key"
        }
    ]

    with patch("server.api.admin.settings.update_env_variable") as mock_update:
        with patch("server.api.admin.settings.reload"):
            with patch("server.api.admin.os.getenv", return_value=None):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post("/api/config/discovered/accept", json=providers_to_accept)

                assert response.status_code == 200
                data = response.json()
                # Should add 2 mappings (one for each type)
                assert len(data["added"]) == 2
