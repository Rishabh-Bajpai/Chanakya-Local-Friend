import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from server.core.dependencies import get_provider
from server.core.exceptions import ProviderNotFoundError
from server.schemas.provider_schema import ProviderConfig


class TestGetProvider:
    """Test get_provider dependency for routing requests to providers"""

    @pytest.mark.asyncio
    async def test_get_provider_models_endpoint_raises_error(self):
        """Test that models endpoint raises error (not applicable)"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/models"

        with pytest.raises(ProviderNotFoundError) as exc_info:
            await get_provider(mock_request)

        assert "not applicable for models endpoint" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_provider_chat_completions_with_model(self):
        """Test get_provider for chat completions with specific model"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/chat/completions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        # Mock json() to be async
        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="llm",
            base_url="http://test/v1",
            api_key="test-key",
            name="Test Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            mock_pm.get_provider_for_model.assert_called_once_with("gpt-4")

    @pytest.mark.asyncio
    async def test_get_provider_chat_completions_fallback_to_type(self):
        """Test get_provider falls back to type when model not found"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/chat/completions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"model": "unknown-model"}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="llm",
            base_url="http://test/v1",
            api_key="test-key",
            name="Test Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            mock_pm.get_provider_by_type.assert_called_once_with("llm")

    @pytest.mark.asyncio
    async def test_get_provider_chat_completions_no_provider_raises(self):
        """Test get_provider raises when no LLM provider available"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/chat/completions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"model": "gpt-4"}

        mock_request.json = mock_json

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = None

            with pytest.raises(ProviderNotFoundError) as exc_info:
                await get_provider(mock_request)

            assert "No LLM provider found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_provider_audio_speech_with_model(self):
        """Test get_provider for audio speech with specific model"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/speech"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"model": "tts-1", "input": "hello"}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="tts",
            base_url="http://tts/v1",
            api_key="tts-key",
            name="TTS Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            assert result.type == "tts"

    @pytest.mark.asyncio
    async def test_get_provider_audio_speech_fallback_to_type(self):
        """Test get_provider for audio speech falls back to TTS type"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/speech"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"input": "hello"}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="tts",
            base_url="http://tts/v1",
            api_key="na",
            name="Default TTS"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            mock_pm.get_provider_by_type.assert_called_once_with("tts")

    @pytest.mark.asyncio
    async def test_get_provider_audio_speech_no_provider_raises(self):
        """Test get_provider raises when no TTS provider available"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/speech"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"input": "hello"}

        mock_request.json = mock_json

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = None

            with pytest.raises(ProviderNotFoundError) as exc_info:
                await get_provider(mock_request)

            assert "No TTS provider found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_provider_audio_transcriptions_with_multipart(self):
        """Test get_provider for audio transcriptions with multipart data"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/transcriptions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "multipart/form-data"

        # Mock form() to return model info
        async def mock_form():
            return {"model": "whisper-1"}

        mock_request.form = mock_form

        mock_provider = ProviderConfig(
            type="stt",
            base_url="http://stt/v1",
            api_key="stt-key",
            name="STT Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            assert result.type == "stt"

    @pytest.mark.asyncio
    async def test_get_provider_audio_transcriptions_fallback_to_type(self):
        """Test get_provider for transcriptions falls back to STT type"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/transcriptions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "multipart/form-data"

        async def mock_form():
            return {}

        mock_request.form = mock_form

        mock_provider = ProviderConfig(
            type="stt",
            base_url="http://stt/v1",
            api_key="na",
            name="Default STT"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            mock_pm.get_provider_by_type.assert_called_once_with("stt")

    @pytest.mark.asyncio
    async def test_get_provider_audio_translations(self):
        """Test get_provider for audio translations"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/audio/translations"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "multipart/form-data"

        async def mock_form():
            return {"model": "whisper-large"}

        mock_request.form = mock_form

        mock_provider = ProviderConfig(
            type="stt",
            base_url="http://stt/v1",
            api_key="na",
            name="STT Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider

    @pytest.mark.asyncio
    async def test_get_provider_embeddings(self):
        """Test get_provider for embeddings endpoint"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/embeddings"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        mock_provider = ProviderConfig(
            type="llm",
            base_url="http://llm/v1",
            api_key="na",
            name="LLM Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_by_type.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
            mock_pm.get_provider_by_type.assert_called_once_with("llm")

    @pytest.mark.asyncio
    async def test_get_provider_embeddings_no_provider_raises(self):
        """Test get_provider raises when no LLM provider for embeddings"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/embeddings"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_by_type.return_value = None

            with pytest.raises(ProviderNotFoundError) as exc_info:
                await get_provider(mock_request)

            assert "No LLM provider found for embeddings" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_provider_completions_endpoint(self):
        """Test get_provider for /completions endpoint (legacy)"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/completions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        async def mock_json():
            return {"model": "text-davinci-003"}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="llm",
            base_url="http://llm/v1",
            api_key="na",
            name="LLM Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider

    @pytest.mark.asyncio
    async def test_get_provider_unknown_endpoint_raises(self):
        """Test get_provider raises for unknown endpoint"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/unknown"
        mock_request.method = "POST"

        with pytest.raises(ProviderNotFoundError):
            await get_provider(mock_request)

    @pytest.mark.asyncio
    async def test_get_provider_no_body_no_model(self):
        """Test get_provider with no body falls back to type"""
        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/v1/chat/completions"
        mock_request.method = "POST"
        mock_request.headers.get.return_value = "application/json"

        # Simulate empty body
        async def mock_json():
            return {}

        mock_request.json = mock_json

        mock_provider = ProviderConfig(
            type="llm",
            base_url="http://llm/v1",
            api_key="na",
            name="LLM Provider"
        )

        with patch('server.core.dependencies.provider_manager') as mock_pm:
            mock_pm.get_provider_for_model.return_value = None
            mock_pm.get_provider_by_type.return_value = mock_provider

            result = await get_provider(mock_request)

            assert result == mock_provider
