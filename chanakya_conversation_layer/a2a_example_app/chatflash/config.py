from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    app_name: str = "ChatFlash"
    host: str = os.getenv("CHATFLASH_HOST", "127.0.0.1")
    port: int = int(os.getenv("CHATFLASH_PORT", "18550"))
    database_path: Path = Path(os.getenv("CHATFLASH_DB_PATH", "chatflash.db"))
    secret_key: str = os.getenv("CHATFLASH_SECRET_KEY", "chatflash-dev-secret")
    model_base_url: str = os.getenv(
        "CHATFLASH_MODEL_BASE_URL", "http://192.168.1.51:1234/v1"
    )
    model_api_key: str = os.getenv("CHATFLASH_MODEL_API_KEY", "na")
    model_id: str = os.getenv("CHATFLASH_MODEL_ID", "qwen/qwen3.6-35b-a3b")
    opencode_http_url: str = os.getenv(
        "CHATFLASH_OPENCODE_HTTP_URL", "http://127.0.0.1:18496"
    )
    opencode_a2a_url: str = os.getenv(
        "CHATFLASH_OPENCODE_A2A_URL", "http://127.0.0.1:18770"
    )
    default_remote_agents: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("CHATFLASH_DEFAULT_REMOTE_AGENTS", "build,plan").split(
            ","
        )
        if item.strip()
    )
    session_history_limit: int = int(os.getenv("CHATFLASH_SESSION_HISTORY_LIMIT", "18"))


def get_settings() -> Settings:
    return Settings()
