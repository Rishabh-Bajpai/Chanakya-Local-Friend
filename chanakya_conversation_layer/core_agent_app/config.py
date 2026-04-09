from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_json(value: str | None, default):
    if value is None or not value.strip():
        return default
    return json.loads(value)


@dataclass(slots=True)
class Config:
    core_agent_backend: str = os.getenv("CORE_AGENT_BACKEND", "local")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "")
    openai_chat_model_id: str = os.getenv("OPENAI_CHAT_MODEL_ID", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    a2a_agent_url: str = os.getenv("A2A_AGENT_URL", "")
    opencode_base_url: str = os.getenv("OPENCODE_BASE_URL", "")
    default_core_agent_target: str = os.getenv("DEFAULT_CORE_AGENT_TARGET", "default")
    openai_compatible_targets: list[dict] = field(
        default_factory=lambda: _as_json(
            os.getenv("OPENAI_COMPATIBLE_TARGETS_JSON"),
            [],
        )
    )
    a2a_targets: list[dict] = field(
        default_factory=lambda: _as_json(
            os.getenv("A2A_TARGETS_JSON"),
            [],
        )
    )
    conversation_openai_base_url: str = os.getenv(
        "CONVERSATION_OPENAI_BASE_URL", os.getenv("OPENAI_BASE_URL", "")
    )
    conversation_openai_chat_model_id: str = os.getenv(
        "CONVERSATION_OPENAI_CHAT_MODEL_ID", os.getenv("OPENAI_CHAT_MODEL_ID", "")
    )
    conversation_openai_api_key: str = os.getenv(
        "CONVERSATION_OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "")
    )
    chanakya_debug: bool = _as_bool(os.getenv("CHANAKYA_DEBUG"), default=False)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///chanakya.db")
    agent_database_url: str = os.getenv("AGENT_DATABASE_URL", "")
    conversation_state_store_backend: str = os.getenv("CONVERSATION_STATE_STORE_BACKEND", "memory")
    conversation_state_store_redis_url: str = os.getenv("CONVERSATION_STATE_STORE_REDIS_URL", "")
    conversation_state_store_redis_key_prefix: str = os.getenv(
        "CONVERSATION_STATE_STORE_REDIS_KEY_PREFIX",
        "conversation:working-memory:",
    )
    conversation_state_store_ttl_seconds: int = int(
        os.getenv("CONVERSATION_STATE_STORE_TTL_SECONDS", "86400")
    )
    env_file_path: str = os.getenv("ENV_FILE_PATH", str(PROJECT_ROOT / ".env"))

    def to_flask_config(self) -> dict:
        shared_database_url = self.database_url
        return {
            "OPENAI_BASE_URL": self.openai_base_url,
            "OPENAI_CHAT_MODEL_ID": self.openai_chat_model_id,
            "OPENAI_API_KEY": self.openai_api_key,
            "CORE_AGENT_BACKEND": self.core_agent_backend,
            "A2A_AGENT_URL": self.a2a_agent_url,
            "OPENCODE_BASE_URL": self.opencode_base_url,
            "DEFAULT_CORE_AGENT_TARGET": self.default_core_agent_target,
            "OPENAI_COMPATIBLE_TARGETS_JSON": self.openai_compatible_targets,
            "A2A_TARGETS_JSON": self.a2a_targets,
            "CONVERSATION_OPENAI_BASE_URL": self.conversation_openai_base_url,
            "CONVERSATION_OPENAI_CHAT_MODEL_ID": self.conversation_openai_chat_model_id,
            "CONVERSATION_OPENAI_API_KEY": self.conversation_openai_api_key,
            "CHANAKYA_DEBUG": self.chanakya_debug,
            "DATABASE_URL": shared_database_url,
            "AGENT_DATABASE_URL": self.agent_database_url or shared_database_url,
            "CONVERSATION_STATE_STORE_BACKEND": self.conversation_state_store_backend,
            "CONVERSATION_STATE_STORE_REDIS_URL": self.conversation_state_store_redis_url,
            "CONVERSATION_STATE_STORE_REDIS_KEY_PREFIX": self.conversation_state_store_redis_key_prefix,
            "CONVERSATION_STATE_STORE_TTL_SECONDS": self.conversation_state_store_ttl_seconds,
            "ENV_FILE_PATH": self.env_file_path,
            "JSON_SORT_KEYS": False,
        }
