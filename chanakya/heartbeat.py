from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chanakya.domain import now_iso
from chanakya.model import AgentProfileModel


def resolve_heartbeat_path(file_path: str, repo_root: Path) -> Path:
    raw_path = Path(file_path)
    if raw_path.is_absolute():
        raise ValueError("heartbeat_file_path must be relative")

    cleaned_parts = tuple(part for part in raw_path.parts if part not in ("", "."))
    if not cleaned_parts:
        raise ValueError("heartbeat_file_path must not be empty")
    if any(part == ".." for part in cleaned_parts):
        raise ValueError("heartbeat_file_path must not contain parent traversal")

    if cleaned_parts[:2] == ("chanakya_data", "heartbeats"):
        relative_parts = cleaned_parts[2:]
    else:
        relative_parts = cleaned_parts

    if not relative_parts:
        raise ValueError("heartbeat_file_path must point to a file under chanakya_data/heartbeats")

    heartbeat_root = (repo_root / "chanakya_data" / "heartbeats").resolve()
    target = (heartbeat_root.joinpath(*relative_parts)).resolve()
    if target != heartbeat_root and heartbeat_root not in target.parents:
        raise ValueError("heartbeat_file_path resolves outside chanakya_data/heartbeats")
    return target


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
        resolved = resolve_heartbeat_path(file_path, repo_root)
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
