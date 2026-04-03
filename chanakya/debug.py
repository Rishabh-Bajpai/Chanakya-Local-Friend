from __future__ import annotations

import json
from typing import Any

from chanakya.config import env_flag


def debug_enabled() -> bool:
    return env_flag("CHANAKYA_DEBUG", default=False)


def debug_log(label: str, payload: dict[str, Any] | None = None) -> None:
    if not debug_enabled():
        return
    print(f"[chanakya-debug] {label}")
    if payload:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
