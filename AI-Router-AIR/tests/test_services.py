import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from server.services.provider_manager import ProviderManager, infer_model_type
from server.core.config import ProviderConfig

def test_infer_model_type():
    # Test case A: Task field
    assert infer_model_type({"id": "some-id", "task": "text-to-speech"}) == "tts"
    assert infer_model_type({"id": "some-id", "task": "automatic-speech-recognition"}) == "stt"
    assert infer_model_type({"id": "some-id", "task": "text-generation"}) == "llm"
    
    # Test case B: Voices field
    assert infer_model_type({"id": "some-id", "voices": ["v1", "v2"]}) == "tts"
    
    # Test case C: Name heuristics
    assert infer_model_type({"id": "whisper-large-v3"}) == "stt"
    assert infer_model_type({"id": "tts-model-1"}) == "tts"
    assert infer_model_type({"id": "gpt-4"}) == "llm"
    
    # Default fallback
    assert infer_model_type({"id": "unknown"}, default_type="custom") == "custom"

@pytest.mark.asyncio
async def test_provider_manager_refresh_models():
    pm = ProviderManager()
    
    # Mock settings.PROVIDERS
    mock_providers = [
        ProviderConfig(name="P1", base_url="http://p1/v1", api_key="k1", type="llm"),
        ProviderConfig(name="P2", base_url="http://p2/v1", api_key="k2", type="stt")
    ]
    
    with patch("server.services.provider_manager.settings") as mock_settings:
        mock_settings.PROVIDERS = mock_providers
        
        # Mock _fetch_models_from_provider
        pm._fetch_models_from_provider = AsyncMock()
        pm._fetch_models_from_provider.side_effect = [
            [{"id": "m1", "provider_name": "P1"}],
            [{"id": "m2", "provider_name": "P2"}]
        ]
        
        models = await pm.refresh_models()
        
        assert len(models) == 2
        
        model_ids = {m["id"] for m in models}
        assert model_ids == {"m1", "m2"}
        
        assert pm.models_cache["all"] == models

@pytest.mark.asyncio
async def test_provider_manager_lookup():
    pm = ProviderManager()
    pm.models_cache["all"] = [
        {"id": "m1", "provider_name": "P1", "provider_type": "llm"},
        {"id": "m2", "provider_name": "P2", "provider_type": "stt"}
    ]
    
    mock_providers = [
        ProviderConfig(name="P1", base_url="http://p1/v1", api_key="k1", type="llm"),
        ProviderConfig(name="P2", base_url="http://p2/v1", api_key="k2", type="stt")
    ]
    
    with patch("server.services.provider_manager.settings") as mock_settings:
        mock_settings.PROVIDERS = mock_providers
        
        # Test lookup by model ID
        p = pm.get_provider_for_model("m1")
        assert p is not None
        assert p.name == "P1"
        
        # Test lookup by type
        p = pm.get_provider_by_type("stt")
        assert p is not None
        assert p.name == "P2"
        
        # Test non-existent
        assert pm.get_provider_for_model("ghost") is None
        assert pm.get_provider_by_type("vision") is None

@pytest.mark.asyncio
async def test_discovery_service_proc_net_tcp():
    from server.services.discovery import DiscoveryService
    import asyncio
    from unittest.mock import mock_open
    ds = DiscoveryService()
    
    mock_proc_content = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:2B9D 00000000:0000 0A 00000000:0000 00:00000000 00000000     0        0 12345 1 0000000000000000\n" # 11165 (dec)
    )
    
    with patch("builtins.open", mock_open(read_data=mock_proc_content)):
        with patch("os.path.exists", return_value=True):
            ports = ds._read_local_tcp_ports()
            assert 11165 in ports

@pytest.mark.asyncio
async def test_discovery_service_probe():
    from server.services.discovery import DiscoveryService
    import asyncio
    ds = DiscoveryService()
    
    mock_models = {"data": [{"id": "m1", "task": "text-generation"}]}
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_models
    
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    
    sem = asyncio.Semaphore(1)
    
    # Mock infer_model_type to ensure it returns 'llm'
    with patch("server.services.discovery.infer_model_type", return_value="llm") as mock_infer:
        # Test successful probe using a port NOT in FRIENDLY_NAMES
        provider = await ds._probe_endpoint(mock_client, sem, "TestP", "127.0.0.1", 9999, "/v1")
        assert provider is not None
        assert provider.name == "TestP"
        assert provider.base_url == "http://127.0.0.1:9999/v1"
        assert "llm" in provider.detected_types
        
        mock_client.get.assert_called_once_with(
            "http://127.0.0.1:9999/v1/models",
            headers={"Content-Type": "application/json"}
        )
        mock_infer.assert_called_once_with(mock_models["data"][0], default_type="llm")
