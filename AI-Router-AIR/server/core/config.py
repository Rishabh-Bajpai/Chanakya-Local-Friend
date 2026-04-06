import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from server.schemas.provider_schema import ProviderConfig
from server.core.env_manager import EnvFileManager

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Router (AIR)"
    VERSION: str = "0.1.0"
    PROVIDERS: List[ProviderConfig] = []
    DISCOVERY_ENABLED: bool = True
    EXTRA_SCAN_PORTS: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
        case_sensitive=True
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure we always have empty list instead of None if no providers are loaded
        self.PROVIDERS = []
        self.load_providers()

    def load_providers(self):
        # We manually read the .env state for dynamic loading
        # Load LLM providers
        for i in range(1, 51):
            base_url = os.getenv(f"LLM_BASE_URL_{i}")
            if base_url:
                api_key = os.getenv(f"LLM_API_KEY_{i}", "na")
                self.PROVIDERS.append(ProviderConfig(type="llm", base_url=base_url, api_key=api_key, name=f"LLM Provider {i}"))
        
        # Load TTS providers
        for i in range(1, 51):
            base_url = os.getenv(f"TTS_BASE_URL_{i}")
            if not base_url and i == 1:
                base_url = os.getenv("TTS_BASE_URL")

            if base_url:
                api_key = os.getenv(f"TTS_API_KEY_{i}", "na")
                self.PROVIDERS.append(ProviderConfig(type="tts", base_url=base_url, api_key=api_key, name=f"TTS Provider {i}"))

        # Load STT providers
        for i in range(1, 51):
            base_url = os.getenv(f"STT_BASE_URL_{i}")
            if not base_url and i == 1:
                 base_url = os.getenv("STT_BASE_URL")
            
            if base_url:
                api_key = os.getenv(f"STT_API_KEY_{i}", "na")
                self.PROVIDERS.append(ProviderConfig(type="stt", base_url=base_url, api_key=api_key, name=f"STT Provider {i}"))

    def update_env_variable(self, key: str, value: str):
        EnvFileManager.update_env_variable(key, value)

    def remove_env_variable(self, key: str):
        EnvFileManager.remove_env_variable(key)

    def reload(self):
        """Reloads providers from the .env file (clears os.environ keys first)."""
        prefixes = ["LLM_BASE_URL_", "LLM_API_KEY_", "TTS_BASE_URL_", "TTS_API_KEY_", "STT_BASE_URL_", "STT_API_KEY_", "TTS_BASE_URL", "STT_BASE_URL"]
        
        for key in list(os.environ.keys()):
            for prefix in prefixes:
                if key.startswith(prefix):
                    os.environ.pop(key, None)
                    break 

        self.PROVIDERS = []
        # Force reload dotenv
        from dotenv import load_dotenv
        load_dotenv(override=True)
        self.load_providers()

settings = Settings()
