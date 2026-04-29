from __future__ import annotations

import re
from typing import Any

from chanakya.config import (
    get_long_term_memory_default_owner_id,
    get_long_term_memory_max_injected_chars,
    get_long_term_memory_max_injected_items,
)
from chanakya.services.memory_manager_service import run_memory_manager_update_job
from chanakya.store import ChanakyaStore

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]{3,}")


class LongTermMemoryService:
    def __init__(self, store: ChanakyaStore, *, owner_id: str | None = None) -> None:
        self.store = store
        configured_owner = str(owner_id or get_long_term_memory_default_owner_id()).strip()
        self.owner_id = configured_owner or "default_user"

    def build_prompt_addendum(self, *, session_id: str, query: str) -> str | None:
        query_tokens = self._tokenize(query)
        active = self.store.list_memories(
            owner_id=self.owner_id,
            status="active",
            session_id=session_id,
            limit=100,
        )
        if not active:
            return None

        scored: list[tuple[float, dict[str, Any]]] = []
        for item in active:
            score = self._memory_score(item, query_tokens)
            if score <= 0:
                continue
            scored.append((score, item))

        if not scored:
            scored = [
                (self._fallback_memory_score(item), item)
                for item in active
                if str(item.get("type") or "") in {"preference", "instruction", "profile"}
            ]

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected: list[dict[str, Any]] = []
        used_chars = 0
        for _, item in scored:
            line = self._memory_line(item)
            if not line:
                continue
            if len(selected) >= get_long_term_memory_max_injected_items():
                break
            if used_chars + len(line) > get_long_term_memory_max_injected_chars():
                break
            selected.append(item)
            used_chars += len(line)

        if not selected:
            return None

        lines = [self._memory_line(item) for item in selected]
        self.store.create_memory_event(
            owner_id=self.owner_id,
            session_id=session_id,
            event_type="memory_retrieved",
            payload={
                "query": self._truncate(query, 300),
                "memory_ids": [str(item.get("id") or "") for item in selected],
                "count": len(selected),
            },
        )
        return "Relevant long-term memory:\n" + "\n".join(f"- {line}" for line in lines)

    @staticmethod
    def _memory_score(item: dict[str, Any], query_tokens: set[str]) -> float:
        if str(item.get("status") or "") != "active":
            return 0.0
        item_tokens = LongTermMemoryService._tokenize(
            f"{str(item.get('subject') or '')} {str(item.get('content') or '')}"
        )
        overlap = len(query_tokens.intersection(item_tokens)) if query_tokens else 0
        if overlap <= 0 and query_tokens:
            return 0.0
        importance = float(item.get("importance") or 0)
        confidence = float(item.get("confidence") or 0)
        return overlap * 3.0 + importance * 0.5 + confidence * 0.25

    @staticmethod
    def _fallback_memory_score(item: dict[str, Any]) -> float:
        return float(item.get("importance") or 0) * 0.5 + float(item.get("confidence") or 0) * 0.25

    @staticmethod
    def _memory_line(item: dict[str, Any]) -> str:
        content = str(item.get("content") or "").strip()
        if not content:
            return ""
        return LongTermMemoryService._truncate(content, 280)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(_TOKEN_PATTERN.findall((text or "").lower()))

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        value = (text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."


def run_memory_update_job(store: ChanakyaStore, *, session_id: str, request_id: str) -> None:
    run_memory_manager_update_job(store, session_id=session_id, request_id=request_id)
