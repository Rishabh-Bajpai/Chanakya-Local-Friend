from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any

from agent_framework import BaseHistoryProvider, Message
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chanakya.db import session_scope
from chanakya.domain import now_iso
from chanakya.model import ChatMessageModel, ChatSessionModel


class SQLAlchemyHistoryProvider(BaseHistoryProvider):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        source_id: str = "sqlalchemy_history",
        *,
        load_messages: bool = True,
        store_inputs: bool = True,
        store_outputs: bool = True,
    ) -> None:
        super().__init__(
            source_id=source_id,
            load_messages=load_messages,
            store_inputs=store_inputs,
            store_outputs=store_outputs,
        )
        self.session_factory = session_factory

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        if not session_id:
            return []

        with session_scope(self.session_factory) as session:
            rows = session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.id.asc())
            ).all()

        return [
            Message(
                role=row.role,
                text=row.content,
                additional_properties=dict(row.metadata_json or {}),
            )
            for row in rows
            if not self._is_control_history_row(row)
        ]

    @staticmethod
    def _is_control_history_row(row: ChatMessageModel) -> bool:
        metadata = dict(row.metadata_json or {})
        if metadata.get("history_control") is True:
            return True
        if row.role != "assistant":
            return False
        content = (row.content or "").strip()
        if not content.startswith("{"):
            return False
        try:
            payload = json.loads(content)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        control_keys = {
            "selected_agent_id",
            "should_create_subagents",
            "needs_input",
        }
        return any(key in payload for key in control_keys)

    async def after_run(
        self,
        *,
        agent: Any,
        session: Any,
        context: Any,
        state: dict[str, Any],
    ) -> None:
        messages_to_store: list[Message] = []
        messages_to_store.extend(self._get_context_messages_to_store(context))
        if self.store_inputs:
            messages_to_store.extend(context.input_messages)
        if self.store_outputs and context.response and context.response.messages:
            messages_to_store.extend(context.response.messages)
        if messages_to_store:
            await self.save_messages(context.session_id, messages_to_store, state=session.state)

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not session_id or not messages:
            return

        request_id = None if state is None else state.get("request_id")
        route = None if state is None else state.get("route")
        created_at = now_iso()

        with session_scope(self.session_factory) as session:
            chat_session = session.get(ChatSessionModel, session_id)
            if chat_session is None:
                chat_session = ChatSessionModel(
                    id=session_id,
                    title="New chat",
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(chat_session)

            for msg in messages:
                message_request_id = msg.additional_properties.get("request_id", request_id)
                message_route = msg.additional_properties.get("route", route)
                session.add(
                    ChatMessageModel(
                        session_id=session_id,
                        role=str(msg.role),
                        content=msg.text or "",
                        request_id=(
                            str(message_request_id) if message_request_id is not None else None
                        ),
                        route=(str(message_route) if message_route is not None else None),
                        metadata_json=dict(msg.additional_properties or {}),
                        created_at=now_iso(),
                    )
                )

            chat_session.updated_at = now_iso()
            session.commit()
