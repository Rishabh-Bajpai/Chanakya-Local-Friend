from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chanakya.db import session_scope
from chanakya.domain import now_iso
from chanakya.model import (
    AgentProfileModel,
    AppEventModel,
    ChatMessageModel,
    ChatSessionModel,
)


class ChanakyaStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_session(self, session_id: str, title: str) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            session.add(
                ChatSessionModel(
                    id=session_id,
                    title=title,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()

    def ensure_session(self, session_id: str, title: str = "New chat") -> None:
        with session_scope(self.Session) as session:
            existing = session.get(ChatSessionModel, session_id)
            if existing is None:
                timestamp = now_iso()
                session.add(
                    ChatSessionModel(
                        id=session_id,
                        title=title,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                session.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        request_id: str | None = None,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ensure_session(session_id)
        with session_scope(self.Session) as session:
            session.add(
                ChatMessageModel(
                    session_id=session_id,
                    role=role,
                    content=content,
                    request_id=request_id,
                    route=route,
                    metadata_json=metadata or {},
                    created_at=now_iso(),
                )
            )
            chat_session = session.get(ChatSessionModel, session_id)
            if chat_session is not None:
                chat_session.updated_at = now_iso()
            session.commit()

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.id.asc())
            ).all()
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "request_id": row.request_id,
                "route": row.route,
                "metadata": row.metadata_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with session_scope(self.Session) as session:
            session.add(
                AppEventModel(
                    event_type=event_type,
                    payload_json=payload,
                    created_at=now_iso(),
                )
            )
            session.commit()

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(AppEventModel).order_by(AppEventModel.id.desc()).limit(limit)
            ).all()
        events = [
            {
                "id": row.id,
                "event_type": row.event_type,
                "payload": row.payload_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        events.reverse()
        return events

    def upsert_agent_profile(self, profile: AgentProfileModel) -> None:
        with session_scope(self.Session) as session:
            row = session.get(AgentProfileModel, profile.id)
            if row is None:
                session.add(profile)
            else:
                row.name = profile.name
                row.role = profile.role
                row.system_prompt = profile.system_prompt
                row.personality = profile.personality
                row.tool_ids_json = profile.tool_ids_json
                row.workspace = profile.workspace
                row.heartbeat_enabled = profile.heartbeat_enabled
                row.heartbeat_interval_seconds = profile.heartbeat_interval_seconds
                row.heartbeat_file_path = profile.heartbeat_file_path
                row.is_active = profile.is_active
                row.updated_at = profile.updated_at
            session.commit()

    def list_agent_profiles(self) -> list[AgentProfileModel]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(AgentProfileModel).order_by(AgentProfileModel.name.asc())
            ).all()
        return cast(list[AgentProfileModel], rows)

    def get_agent_profile(self, agent_id: str) -> AgentProfileModel:
        with session_scope(self.Session) as session:
            row = session.get(AgentProfileModel, agent_id)
        if row is None:
            raise KeyError(f"Agent profile not found: {agent_id}")
        return row
