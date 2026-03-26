"""Configuration helpers, including local .env loading."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(env_file: str = ".env") -> None:
    """Load key/value pairs from a local .env file into process env.

    Existing process env keys win and are not overwritten.
    """
    path = Path(env_file)
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_openai_compatible_config() -> dict[str, str | None]:
    """Return endpoint config expected from an OpenAI-compatible setup."""
    return {
        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE"),
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": os.getenv("OPENAI_MODEL") or os.getenv("MODEL"),
    }
