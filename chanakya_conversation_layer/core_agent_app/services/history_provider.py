from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_framework import BaseHistoryProvider, Message
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core_agent_app.db import HistoryMessageRecord


class SQLAlchemyHistoryProvider(BaseHistoryProvider):
    def __init__(
        self,
        db_session_factory: sessionmaker[Session],
        source_id: str = "sqlalchemy_history",
        *,
        load_messages: bool = True,
        store_inputs: bool = True,
        store_context_messages: bool = False,
        store_context_from: set[str] | None = None,
        store_outputs: bool = True,
    ) -> None:
        super().__init__(
            source_id=source_id,
            load_messages=load_messages,
            store_inputs=store_inputs,
            store_context_messages=store_context_messages,
            store_context_from=store_context_from,
            store_outputs=store_outputs,
        )
        self.db_session_factory = db_session_factory

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        resolved_session_id = session_id or "default"
        with self.db_session_factory() as db:
            rows = db.execute(
                select(HistoryMessageRecord)
                .where(HistoryMessageRecord.session_id == resolved_session_id)
                .order_by(HistoryMessageRecord.id.asc())
            ).scalars()
            return [Message.from_dict(json.loads(row.payload_json)) for row in rows]

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_session_id = session_id or "default"
        with self.db_session_factory() as db:
            for message in messages:
                db.add(
                    HistoryMessageRecord(
                        session_id=resolved_session_id,
                        role=message.role,
                        text=message.text,
                        payload_json=json.dumps(message.to_dict(), sort_keys=True),
                    )
                )
            db.commit()

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self.db_session_factory() as db:
            rows = db.execute(
                select(HistoryMessageRecord)
                .where(HistoryMessageRecord.session_id == session_id)
                .order_by(HistoryMessageRecord.id.asc())
            ).scalars()
            return [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "role": row.role,
                    "text": row.text,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.db_session_factory() as db:
            rows = list(
                db.execute(
                    select(HistoryMessageRecord).order_by(
                        HistoryMessageRecord.id.desc()
                    )
                ).scalars()
            )
            sessions: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                if row.session_id in seen:
                    continue
                seen.add(row.session_id)
                sessions.append(
                    {
                        "session_id": row.session_id,
                        "last_message_preview": row.text[:120],
                        "last_message_role": row.role,
                        "updated_at": row.created_at.isoformat(),
                    }
                )
            sessions.reverse()
            return sessions

    def delete_session(self, session_id: str) -> None:
        with self.db_session_factory() as db:
            rows = list(
                db.execute(
                    select(HistoryMessageRecord).where(
                        HistoryMessageRecord.session_id == session_id
                    )
                ).scalars()
            )
            for row in rows:
                db.delete(row)
            db.commit()

    def rewrite_latest_assistant_turn(
        self, session_id: str, assistant_messages: Sequence[str]
    ) -> None:
        with self.db_session_factory() as db:
            rows = list(
                db.execute(
                    select(HistoryMessageRecord)
                    .where(HistoryMessageRecord.session_id == session_id)
                    .order_by(HistoryMessageRecord.id.asc())
                ).scalars()
            )
            for row in reversed(rows):
                if row.role != "assistant":
                    break
                if row.role == "assistant":
                    db.delete(row)

            for text in assistant_messages:
                message = Message("assistant", [text])
                db.add(
                    HistoryMessageRecord(
                        session_id=session_id,
                        role=message.role,
                        text=message.text,
                        payload_json=json.dumps(message.to_dict(), sort_keys=True),
                    )
                )
            db.commit()

    def append_conversation_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self.db_session_factory() as db:
            for message in (
                Message("user", [user_message]),
                Message("assistant", [assistant_message]),
            ):
                db.add(
                    HistoryMessageRecord(
                        session_id=session_id,
                        role=message.role,
                        text=message.text,
                        payload_json=json.dumps(message.to_dict(), sort_keys=True),
                    )
                )
            db.commit()
