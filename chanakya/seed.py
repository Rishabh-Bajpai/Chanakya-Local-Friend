from __future__ import annotations

import json
from pathlib import Path

from chanakya.models import AgentProfile, now_iso
from chanakya.store import ChanakyaStore


def load_agent_seeds(store: ChanakyaStore, seed_file: Path) -> None:
    if not seed_file.exists():
        return
    raw_items = json.loads(seed_file.read_text(encoding="utf-8"))
    for item in raw_items:
        timestamp = now_iso()
        store.upsert_agent_profile(
            AgentProfile(
                id=str(item["id"]),
                name=str(item["name"]),
                role=str(item["role"]),
                system_prompt=str(item["system_prompt"]),
                personality=str(item.get("personality", "")),
                tool_ids=list(item.get("tool_ids", [])),
                workspace=item.get("workspace"),
                heartbeat_enabled=bool(item.get("heartbeat_enabled", False)),
                heartbeat_interval_seconds=int(item.get("heartbeat_interval_seconds", 300)),
                heartbeat_file_path=item.get("heartbeat_file_path"),
                is_active=bool(item.get("is_active", True)),
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
