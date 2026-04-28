import json
import os
from pathlib import Path
from typing import Any

from chanakya.config import load_local_env

def _resolve_mcp_config_path() -> Path:
    """Find mcp_config_file.json relative to the project layout."""
    candidates = [
        # Repo root when running this package from source.
        Path(__file__).resolve().parents[2] / "mcp_config_file.json",
        # Current working directory fallback.
        Path.cwd() / "mcp_config_file.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    # Default to the source-layout location for consistent error reporting.
    return candidates[0]


def get_mcp_config_path() -> Path:
    return _resolve_mcp_config_path()


def load_mcp_config() -> dict[str, dict[str, Any]]:
    """Reads mcp_config_file.json and returns the mcpServers dict."""
    config_path = _resolve_mcp_config_path()
    if not config_path.exists():
        return {}
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("mcpServers", {})
    except Exception as e:
        print(f"Failed to load MCP config: {e}")
        return {}

def merge_env_with_os(server_env: dict[str, str]) -> dict[str, str]:
    """Merges config environmental variables with real OS vars (OS wins)."""
    load_local_env()
    effective_env = dict(os.environ)
    for key, fallback in server_env.items():
        val = os.environ.get(key)
        effective_env[key] = str(val if val is not None else fallback)
    return effective_env
