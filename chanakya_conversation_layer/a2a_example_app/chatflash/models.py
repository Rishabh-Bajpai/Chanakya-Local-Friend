from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass(slots=True)
class SessionRecord:
    id: str
    title: str
    agent_id: str
    remote_context_id: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class AgentDescriptor:
    id: str
    label: str
    backend: str
    description: str
    badges: list[str]
    available: bool = True
    detail: str | None = None
