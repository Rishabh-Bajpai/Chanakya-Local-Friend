from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chanakya.domain import now_iso
from chanakya.model import AgentProfileModel


@dataclass(slots=True)
class HeartbeatSnapshot:
    agent_id: str
    enabled: bool
    interval_seconds: int
    file_path: str | None
    content_preview: str | None
    checked_at: str


def read_heartbeat(profile: AgentProfileModel, repo_root: Path) -> HeartbeatSnapshot:
    preview: str | None = None
    file_path = profile.heartbeat_file_path
    if profile.heartbeat_enabled and file_path:
        resolved = repo_root / file_path
        if resolved.exists():
            preview = resolved.read_text(encoding="utf-8").strip()[:400] or None
    return HeartbeatSnapshot(
        agent_id=profile.id,
        enabled=profile.heartbeat_enabled,
        interval_seconds=profile.heartbeat_interval_seconds,
        file_path=file_path,
        content_preview=preview,
        checked_at=now_iso(),
    )
