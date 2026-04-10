from __future__ import annotations

import os
import shlex
from pathlib import Path

_LOCAL_ENV_LOADED = False


def env_flag(name: str, default: bool = False) -> bool:
    load_local_env()
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_local_env(env_file: str = ".env") -> None:
    global _LOCAL_ENV_LOADED
    if _LOCAL_ENV_LOADED:
        return
    path = Path(env_file)
    if not path.exists():
        _LOCAL_ENV_LOADED = True
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
    _LOCAL_ENV_LOADED = True


def get_openai_compatible_config() -> dict[str, str | None]:
    load_local_env()
    model = (
        os.getenv("AIR_DEFAULT_LLM_MODEL")
        or os.getenv("OPENAI_CHAT_MODEL_ID")
        or os.getenv("OPENAI_RESPONSES_MODEL_ID")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL")
    )
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if not base_url and env_flag("AIR_ENABLED", default=True):
        base_url = f"{get_air_server_url()}/v1"
    return {
        "base_url": base_url,
        "api_key": os.getenv("OPENAI_API_KEY") or os.getenv("AIR_API_KEY"),
        "model": model,
    }


def get_conversation_openai_config() -> dict[str, str | None]:
    load_local_env()
    core = get_openai_compatible_config()
    return {
        "base_url": os.getenv("CONVERSATION_OPENAI_BASE_URL") or core.get("base_url"),
        "api_key": os.getenv("CONVERSATION_OPENAI_API_KEY") or core.get("api_key"),
        "model": os.getenv("CONVERSATION_OPENAI_CHAT_MODEL_ID") or core.get("model"),
    }


def get_air_server_url() -> str:
    load_local_env()
    configured = os.getenv("AIR_SERVER_URL")
    if configured:
        return configured.rstrip("/")
    port = os.getenv("AIR_SERVER_PORT", "5512").strip() or "5512"
    return f"http://localhost:{port}"


def get_air_dashboard_url() -> str:
    return get_air_server_url()


def get_air_status_url() -> str:
    return f"{get_air_server_url()}/status"


def get_data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "chanakya_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_database_url() -> str:
    load_local_env()
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    return f"sqlite:///{get_data_dir() / 'chanakya.db'}"


def _parse_cli_args(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return default
    parsed = shlex.split(value.strip())
    return parsed or default


def get_mcp_request_timeout_seconds() -> int:
    load_local_env()
    raw = os.getenv("MCP_REQUEST_TIMEOUT_SECONDS", "20")
    try:
        value = int(raw)
    except ValueError:
        return 20
    return value if value > 0 else 20


def get_agent_request_timeout_seconds() -> int:
    load_local_env()
    raw = os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "120")
    try:
        value = int(raw)
    except ValueError:
        return 120
    return value if value > 0 else 120


def get_long_running_agent_request_timeout_seconds() -> int:
    load_local_env()
    raw = os.getenv("AGENT_LONG_RUNNING_TIMEOUT_SECONDS", "600")
    try:
        value = int(raw)
    except ValueError:
        return 600
    return value if value > 0 else 600


def get_subagent_group_chat_round_multiplier() -> int:
    load_local_env()
    raw = os.getenv("CHANAKYA_SUBAGENT_GROUP_CHAT_ROUND_MULTIPLIER", "2")
    try:
        value = int(raw)
    except ValueError:
        return 2
    return value if value > 0 else 2


def _get_positive_int_env(name: str, default: int) -> int:
    load_local_env()
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_history_recent_window_messages() -> int:
    return _get_positive_int_env("CHANAKYA_HISTORY_RECENT_WINDOW_MESSAGES", 16)


def get_history_max_messages() -> int:
    return _get_positive_int_env("CHANAKYA_HISTORY_MAX_MESSAGES", 48)


def get_history_max_chars() -> int:
    return _get_positive_int_env("CHANAKYA_HISTORY_MAX_CHARS", 24000)


def get_history_max_message_chars() -> int:
    return _get_positive_int_env("CHANAKYA_HISTORY_MAX_MESSAGE_CHARS", 3000)


def force_subagents_enabled() -> bool:
    return env_flag("CHANAKYA_FORCE_SUBAGENTS", default=False)
