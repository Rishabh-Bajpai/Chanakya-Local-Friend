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
        os.getenv("OPENAI_CHAT_MODEL_ID")
        or os.getenv("OPENAI_RESPONSES_MODEL_ID")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL")
    )
    return {
        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE"),
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": model,
    }


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


def force_subagents_enabled() -> bool:
    return env_flag("CHANAKYA_FORCE_SUBAGENTS", default=False)
