from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class Route(str, Enum):
    DIRECT = "direct"
    TOOL = "tool"
    MANAGER = "manager"


class TaskStatus(str, Enum):
    CREATED = "created"
    READY = "ready"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    WAITING_INPUT = "waiting_input"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.READY, TaskStatus.FAILED},
    TaskStatus.READY: {TaskStatus.ASSIGNED, TaskStatus.BLOCKED, TaskStatus.FAILED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.FAILED},
    TaskStatus.IN_PROGRESS: {
        TaskStatus.WAITING_INPUT,
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
    },
    TaskStatus.WAITING_INPUT: {TaskStatus.READY, TaskStatus.FAILED},
    TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.FAILED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: set(),
}


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass(slots=True)
class Task:
    id: str
    description: str
    owner: str
    status: TaskStatus
    dependencies: list[str] = field(default_factory=list)
    parent_task_id: str | None = None
    result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass(slots=True)
class RequestEnvelope:
    request_id: str
    text: str
    route: Route
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
