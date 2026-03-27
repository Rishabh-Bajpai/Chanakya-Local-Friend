from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


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
