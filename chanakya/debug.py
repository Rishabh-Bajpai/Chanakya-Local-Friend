from __future__ import annotations

import json
import os
from typing import Any


def debug_enabled() -> bool:
    value = os.getenv("CHANAKYA_DEBUG", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def debug_log(label: str, payload: dict[str, Any] | None = None) -> None:
    if not debug_enabled():
        return
    print(f"[chanakya-debug] {label}")
    if payload:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
