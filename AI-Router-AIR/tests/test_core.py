import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock, mock_open
from server.core.config import Settings
from server.core.env_manager import EnvFileManager
from server.core.exceptions import ProviderNotFoundError, ProviderUnavailableError, ProxyError, global_exception_handler
from server.core.logging import setup_logging
from server.schemas.provider_schema import ProviderConfig


class TestSettings:
    """Test Settings configuration class"""

    def test_settings_initialization(self):
        """Test Settings initializes with default values"""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.PROJECT_NAME == "AI Router (AIR)"
            assert settings.VERSION == "0.1.0"
            assert isinstance(settings.PROVIDERS, list)
            assert settings.DISCOVERY_ENABLED is True

    def test_load_providers_llm(self):
        """Test loading LLM providers from environment variables"""
        env_vars = {
            "LLM_BASE_URL_1": "http://localhost:8000/v1",
            "LLM_API_KEY_1": "test-key-1"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 1
            assert settings.PROVIDERS[0].type == "llm"
            assert settings.PROVIDERS[0].base_url == "http://localhost:8000/v1"
            assert settings.PROVIDERS[0].api_key == "test-key-1"
            assert settings.PROVIDERS[0].name == "LLM Provider 1"

    def test_load_providers_multiple_llm(self):
        """Test loading multiple LLM providers"""
        env_vars = {
            "LLM_BASE_URL_1": "http://localhost:8000/v1",
            "LLM_API_KEY_1": "key1",
            "LLM_BASE_URL_2": "http://localhost:8001/v1",
            "LLM_API_KEY_2": "key2"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 2
            assert all(p.type == "llm" for p in settings.PROVIDERS)

    def test_load_providers_tts(self):
        """Test loading TTS providers"""
        env_vars = {
            "TTS_BASE_URL_1": "http://localhost:9000/v1",
            "TTS_API_KEY_1": "tts-key"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 1
            assert settings.PROVIDERS[0].type == "tts"
            assert settings.PROVIDERS[0].base_url == "http://localhost:9000/v1"

    def test_load_providers_tts_legacy_fallback(self):
        """Test TTS provider loading with legacy TTS_BASE_URL (no index)"""
        env_vars = {
            "TTS_BASE_URL": "http://legacy-tts/v1"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 1
            assert settings.PROVIDERS[0].type == "tts"
            assert settings.PROVIDERS[0].base_url == "http://legacy-tts/v1"

    def test_load_providers_stt(self):
        """Test loading STT providers"""
        env_vars = {
            "STT_BASE_URL_1": "http://localhost:7000/v1",
            "STT_API_KEY_1": "stt-key"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 1
            assert settings.PROVIDERS[0].type == "stt"

    def test_load_providers_stt_legacy_fallback(self):
        """Test STT provider loading with legacy STT_BASE_URL (no index)"""
        env_vars = {
            "STT_BASE_URL": "http://legacy-stt/v1"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 1
            assert settings.PROVIDERS[0].type == "stt"

    def test_load_providers_mixed_types(self):
        """Test loading providers of all types together"""
        env_vars = {
            "LLM_BASE_URL_1": "http://llm:8000/v1",
            "TTS_BASE_URL_1": "http://tts:9000/v1",
            "STT_BASE_URL_1": "http://stt:7000/v1"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert len(settings.PROVIDERS) == 3
            types = {p.type for p in settings.PROVIDERS}
            assert types == {"llm", "tts", "stt"}

    def test_load_providers_default_api_key(self):
        """Test providers get default 'na' API key when not specified"""
        env_vars = {
            "LLM_BASE_URL_1": "http://localhost:8000/v1"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            assert settings.PROVIDERS[0].api_key == "na"

    def test_update_env_variable(self):
        """Test updating environment variable delegates to EnvFileManager"""
        with patch.object(EnvFileManager, 'update_env_variable') as mock_update:
            settings = Settings()
            settings.update_env_variable("TEST_KEY", "test_value")
            mock_update.assert_called_once_with("TEST_KEY", "test_value")

    def test_remove_env_variable(self):
        """Test removing environment variable delegates to EnvFileManager"""
        with patch.object(EnvFileManager, 'remove_env_variable') as mock_remove:
            settings = Settings()
            settings.remove_env_variable("TEST_KEY")
            mock_remove.assert_called_once_with("TEST_KEY")

    def test_reload_clears_and_reloads(self):
        """Test reload clears provider-related env vars and reloads"""
        env_vars = {
            "LLM_BASE_URL_1": "http://old:8000/v1",
            "LLM_API_KEY_1": "old-key",
            "UNRELATED_VAR": "keep-me"
        }

        with patch.dict(os.environ, env_vars, clear=True):
            with patch('dotenv.load_dotenv') as mock_load_dotenv:
                settings = Settings()
                initial_providers = len(settings.PROVIDERS)

                # Simulate adding a new provider
                os.environ["LLM_BASE_URL_2"] = "http://new:8001/v1"

                settings.reload()

                # Verify load_dotenv was called
                mock_load_dotenv.assert_called_once_with(override=True)

                # Verify unrelated var is kept
                assert os.environ.get("UNRELATED_VAR") == "keep-me"


class TestEnvFileManager:
    """Test EnvFileManager for .env file operations"""

    def test_read_env_lines_file_exists(self):
        """Test reading .env file when it exists"""
        mock_content = "KEY1=value1\nKEY2=value2\n"

        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=mock_content)):
                lines = EnvFileManager._read_env_lines()
                assert len(lines) == 2
                assert lines[0] == "KEY1=value1\n"

    def test_read_env_lines_file_not_exists(self):
        """Test reading .env file when it doesn't exist"""
        with patch('os.path.exists', return_value=False):
            lines = EnvFileManager._read_env_lines()
            assert lines == []

    def test_write_env_lines(self):
        """Test writing lines to .env file"""
        lines = ["KEY1=value1\n", "KEY2=value2\n"]
        mock_file = mock_open()

        with patch('builtins.open', mock_file):
            EnvFileManager._write_env_lines(lines)
            mock_file().writelines.assert_called_once_with(lines)

    def test_update_env_variable_existing_key(self):
        """Test updating an existing environment variable"""
        existing_lines = ["KEY1=old_value\n", "KEY2=value2\n"]

        with patch.object(EnvFileManager, '_read_env_lines', return_value=existing_lines):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.update_env_variable("KEY1", "new_value")

                    written_lines = mock_write.call_args[0][0]
                    assert "KEY1=new_value\n" in written_lines
                    assert "KEY2=value2\n" in written_lines

    def test_update_env_variable_new_key(self):
        """Test adding a new environment variable"""
        existing_lines = ["KEY1=value1\n"]

        with patch.object(EnvFileManager, '_read_env_lines', return_value=existing_lines):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.update_env_variable("KEY2", "value2")

                    written_lines = mock_write.call_args[0][0]
                    assert "KEY1=value1\n" in written_lines
                    assert "KEY2=value2\n" in written_lines

    def test_update_env_variable_empty_file(self):
        """Test adding variable to empty .env file"""
        with patch.object(EnvFileManager, '_read_env_lines', return_value=[]):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.update_env_variable("KEY1", "value1")

                    written_lines = mock_write.call_args[0][0]
                    assert "KEY1=value1\n" in written_lines

    def test_update_env_variable_handles_missing_newline(self):
        """Test adding variable when last line lacks newline"""
        existing_lines = ["KEY1=value1"]  # No trailing newline

        with patch.object(EnvFileManager, '_read_env_lines', return_value=existing_lines):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.update_env_variable("KEY2", "value2")

                    written_lines = mock_write.call_args[0][0]
                    # Should add newline to previous line
                    assert written_lines[0] == "KEY1=value1\n"
                    assert "KEY2=value2\n" in written_lines

    def test_remove_env_variable(self):
        """Test removing an environment variable"""
        existing_lines = ["KEY1=value1\n", "KEY2=value2\n", "KEY3=value3\n"]

        with patch.object(EnvFileManager, '_read_env_lines', return_value=existing_lines):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.remove_env_variable("KEY2")

                    written_lines = mock_write.call_args[0][0]
                    assert "KEY1=value1\n" in written_lines
                    assert "KEY3=value3\n" in written_lines
                    assert not any("KEY2" in line for line in written_lines)

    def test_remove_env_variable_not_exists(self):
        """Test removing a non-existent variable doesn't error"""
        existing_lines = ["KEY1=value1\n"]

        with patch.object(EnvFileManager, '_read_env_lines', return_value=existing_lines):
            with patch.object(EnvFileManager, '_write_env_lines') as mock_write:
                with patch('server.core.env_manager.env_lock'):
                    EnvFileManager.remove_env_variable("NONEXISTENT")

                    written_lines = mock_write.call_args[0][0]
                    assert written_lines == existing_lines


class TestExceptions:
    """Test custom exception classes"""

    def test_provider_not_found_error_default(self):
        """Test ProviderNotFoundError with default message"""
        exc = ProviderNotFoundError()
        assert exc.status_code == 404
        assert "No suitable provider found" in exc.detail

    def test_provider_not_found_error_custom(self):
        """Test ProviderNotFoundError with custom message"""
        exc = ProviderNotFoundError("Custom error message")
        assert exc.status_code == 404
        assert exc.detail == "Custom error message"

    def test_provider_unavailable_error_default(self):
        """Test ProviderUnavailableError with default message"""
        exc = ProviderUnavailableError()
        assert exc.status_code == 503
        assert "currently unavailable" in exc.detail

    def test_provider_unavailable_error_custom(self):
        """Test ProviderUnavailableError with custom message"""
        exc = ProviderUnavailableError("Service down")
        assert exc.status_code == 503
        assert exc.detail == "Service down"

    def test_proxy_error_default(self):
        """Test ProxyError with default message"""
        exc = ProxyError()
        assert exc.status_code == 502
        assert "Error proxying request" in exc.detail

    def test_proxy_error_custom(self):
        """Test ProxyError with custom message"""
        exc = ProxyError("Connection timeout")
        assert exc.status_code == 502
        assert exc.detail == "Connection timeout"

    @pytest.mark.asyncio
    async def test_global_exception_handler(self):
        """Test global exception handler returns generic error"""
        from fastapi import Request
        from unittest.mock import AsyncMock

        # Create a mock request
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/test"

        # Create a test exception
        test_exc = Exception("Test error")

        # Call the handler
        response = await global_exception_handler(mock_request, test_exc)

        # Verify response
        assert response.status_code == 500
        assert response.body == b'{"error":"Internal Server Error"}'


class TestLogging:
    """Test logging configuration"""

    def test_setup_logging_returns_logger(self):
        """Test setup_logging returns a logger instance"""
        import logging

        logger = setup_logging()
        assert isinstance(logger, logging.Logger)
        assert logger.name == "air"

    def test_setup_logging_configures_handlers(self):
        """Test setup_logging configures stream handler"""
        import logging

        # Clear any existing handlers
        root_logger = logging.getLogger()
        root_logger.handlers = []

        logger = setup_logging()

        # Check that handlers were configured
        assert len(root_logger.handlers) > 0

        # Check for StreamHandler
        has_stream_handler = any(
            isinstance(h, logging.StreamHandler) for h in root_logger.handlers
        )
        assert has_stream_handler

    def test_logger_level_is_info(self):
        """Test logger is configured with INFO level"""
        import logging

        setup_logging()
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
