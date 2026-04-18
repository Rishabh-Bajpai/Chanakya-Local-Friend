from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


REQUEST_STATUS_CREATED = "created"
REQUEST_STATUS_IN_PROGRESS = "in_progress"
REQUEST_STATUS_COMPLETED = "completed"
REQUEST_STATUS_FAILED = "failed"
REQUEST_STATUS_CANCELLED = "cancelled"

TASK_STATUS_CREATED = "created"
TASK_STATUS_READY = "ready"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_WAITING_INPUT = "waiting_input"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass(slots=True)
class ChatReply:
    request_id: str
    session_id: str
    work_id: str | None
    route: str
    message: str
    model: str | None
    endpoint: str | None
    runtime: str
    agent_name: str
    request_status: str | None = None
    root_task_id: str | None = None
    root_task_status: str | None = None
    response_mode: str = "direct_answer"
    tool_calls_used: int = 0
    tool_trace_ids: list[str] = field(default_factory=list)
    requires_input: bool = False
    waiting_task_id: str | None = None
    input_prompt: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
