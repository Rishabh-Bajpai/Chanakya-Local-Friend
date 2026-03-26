from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chanakya_mvp.models import now_iso


class JsonlLogger:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": now_iso(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
