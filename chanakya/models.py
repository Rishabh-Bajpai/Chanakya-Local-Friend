from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass(slots=True)
class AgentProfile:
    id: str
    name: str
    role: str
    system_prompt: str
    personality: str = ""
    tool_ids: list[str] = field(default_factory=list)
    workspace: str | None = None
    heartbeat_enabled: bool = False
    heartbeat_interval_seconds: int = 300
    heartbeat_file_path: str | None = None
    is_active: bool = True
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass(slots=True)
class ChatReply:
    request_id: str
    session_id: str
    route: str
    message: str
    model: str | None
    endpoint: str | None
    runtime: str
    agent_name: str
    created_at: str = field(default_factory=now_iso)
