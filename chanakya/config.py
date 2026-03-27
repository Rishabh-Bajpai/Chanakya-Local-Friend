from __future__ import annotations

import os
from pathlib import Path


def load_local_env(env_file: str = ".env") -> None:
    path = Path(env_file)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
