from __future__ import annotations

from collections.abc import Sequence
import json
import re
from typing import Any

from agent_framework import HistoryProvider, Message
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chanakya.db import session_scope
from chanakya.debug import debug_log
from chanakya.config import (
    get_history_max_chars,
    get_history_max_message_chars,
    get_history_max_messages,
    get_history_recent_window_messages,
)
from chanakya.domain import now_iso
from chanakya.model import ChatMessageModel, ChatSessionModel


class SQLAlchemyHistoryProvider(HistoryProvider):
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

        rows = [row for row in rows if not self._is_control_history_row(row)]
        query_text = ""
        if isinstance(state, dict):
            query_text = str(state.get("history_query_text") or "").strip()
        if not query_text:
            query_text = str(kwargs.get("history_query_text") or "").strip()
        selected, stats = self._compress_history_rows_with_stats(
            rows,
            query_text=query_text,
            recent_window=get_history_recent_window_messages(),
            max_messages=get_history_max_messages(),
            max_chars=get_history_max_chars(),
            max_message_chars=get_history_max_message_chars(),
        )
        if isinstance(state, dict):
            selected_chars = sum(len(text) for _, text in selected)
            state["history_context_stats"] = {
                "available_messages": len(rows),
                "selected_messages": len(selected),
                "selected_chars": selected_chars,
                "query_text": self._bounded_text(query_text, 300),
                "relevance_hits": int(stats.get("relevance_hits", 0)),
                "backfill_hits": int(stats.get("backfill_hits", 0)),
                "truncated_messages": int(stats.get("truncated_messages", 0)),
            }
            debug_log(
                "history_context_selected",
                {
                    "session_id": session_id,
                    "request_id": state.get("request_id"),
                    "available_messages": len(rows),
                    "selected_messages": len(selected),
                    "selected_chars": selected_chars,
                    "relevance_hits": int(stats.get("relevance_hits", 0)),
                    "backfill_hits": int(stats.get("backfill_hits", 0)),
                    "truncated_messages": int(stats.get("truncated_messages", 0)),
                },
            )

        return [
            Message(
                role=row.role,
                contents=[content],
                additional_properties=dict(row.metadata_json or {}),
            )
            for row, content in selected
        ]

    @staticmethod
    def _compress_history_rows(
        rows: Sequence[ChatMessageModel],
        *,
        query_text: str,
        recent_window: int,
        max_messages: int,
        max_chars: int,
        max_message_chars: int,
    ) -> list[tuple[ChatMessageModel, str]]:
        selected_rows, _ = SQLAlchemyHistoryProvider._compress_history_rows_with_stats(
            rows,
            query_text=query_text,
            recent_window=recent_window,
            max_messages=max_messages,
            max_chars=max_chars,
            max_message_chars=max_message_chars,
        )
        return selected_rows

    @staticmethod
    def _compress_history_rows_with_stats(
        rows: Sequence[ChatMessageModel],
        *,
        query_text: str,
        recent_window: int,
        max_messages: int,
        max_chars: int,
        max_message_chars: int,
    ) -> tuple[list[tuple[ChatMessageModel, str]], dict[str, int]]:
        if not rows:
            return [], {
                "relevance_hits": 0,
                "backfill_hits": 0,
                "truncated_messages": 0,
            }
        total_rows = len(rows)
        recent_window = max(1, recent_window)
        max_messages = max(1, max_messages)
        max_chars = max(1, max_chars)
        max_message_chars = max(128, max_message_chars)
        selected_indices: set[int] = set(range(max(0, total_rows - recent_window), total_rows))
        query_tokens = SQLAlchemyHistoryProvider._tokenize_for_relevance(query_text)
        relevance_hits = 0
        backfill_hits = 0

        if query_tokens:
            scored: list[tuple[float, int]] = []
            for idx, row in enumerate(rows):
                if idx in selected_indices:
                    continue
                overlap = SQLAlchemyHistoryProvider._message_overlap_score(
                    str(row.content or ""), query_tokens
                )
                if overlap <= 0:
                    continue
                recency = (idx + 1) / total_rows
                score = float(overlap) + recency * 0.25
                scored.append((score, idx))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            for _, idx in scored:
                if len(selected_indices) >= max_messages:
                    break
                selected_indices.add(idx)
                relevance_hits += 1

        if len(selected_indices) < max_messages:
            for idx in range(total_rows - 1, -1, -1):
                if idx in selected_indices:
                    continue
                selected_indices.add(idx)
                backfill_hits += 1
                if len(selected_indices) >= max_messages:
                    break

        selected_rows: list[tuple[ChatMessageModel, str]] = []
        used_chars = 0
        truncated_messages = 0
        for idx in sorted(selected_indices):
            row = rows[idx]
            bounded = SQLAlchemyHistoryProvider._bounded_text(
                str(row.content or ""), max_message_chars
            )
            if len(str(row.content or "").replace("\x00", "").strip()) > len(bounded):
                truncated_messages += 1
            if not bounded:
                continue
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if len(bounded) > remaining:
                if remaining < 32:
                    break
                bounded = bounded[:remaining].rstrip() + "..."
                truncated_messages += 1
            selected_rows.append((row, bounded))
            used_chars += len(bounded)
        return selected_rows, {
            "relevance_hits": relevance_hits,
            "backfill_hits": backfill_hits,
            "truncated_messages": truncated_messages,
        }

    @staticmethod
    def _tokenize_for_relevance(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z0-9_]{3,}", (text or "").lower())
        return set(tokens)

    @staticmethod
    def _message_overlap_score(content: str, query_tokens: set[str]) -> int:
        if not query_tokens:
            return 0
        message_tokens = SQLAlchemyHistoryProvider._tokenize_for_relevance(content)
        if not message_tokens:
            return 0
        return len(message_tokens.intersection(query_tokens))

    @staticmethod
    def _bounded_text(text: str, limit: int) -> str:
        normalized = (text or "").replace("\x00", "").strip()
        if not normalized:
            return ""
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "..."

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
        history_context_stats = None if state is None else state.get("history_context_stats")
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
                additional = dict(msg.additional_properties or {})
                if isinstance(history_context_stats, dict):
                    additional.setdefault("history_context", dict(history_context_stats))
                message_request_id = additional.get("request_id", request_id)
                message_route = additional.get("route", route)
                session.add(
                    ChatMessageModel(
                        session_id=session_id,
                        role=str(msg.role),
                        content=msg.text or "",
                        request_id=(
                            str(message_request_id) if message_request_id is not None else None
                        ),
                        route=(str(message_route) if message_route is not None else None),
                        metadata_json=additional,
                        created_at=now_iso(),
                    )
                )

            chat_session.updated_at = now_iso()
            session.commit()
