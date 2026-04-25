from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from conversation_layer.schemas import DeliveryMessage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ResponseScopedWorkingMemory:
    session_id: str
    topic_state: str = "idle"
    topic_label: str = ""
    current_user_message: str = ""
    latest_user_message: str = ""
    latest_user_intent: str = ""
    topic_continuity_confidence: float = 0.0
    latest_core_response: str = ""
    planned_messages: list[DeliveryMessage] = field(default_factory=list)
    delivered_messages: list[DeliveryMessage] = field(default_factory=list)
    pending_messages: list[dict[str, Any]] = field(default_factory=list)
    delivered_summary: str = ""
    remaining_summary: str = ""
    core_agent_called: bool = False
    interrupted: bool = False
    manual_pause_requested: bool = False
    queue_cleared_reason: str | None = None
    cancelled_pending_count: int = 0
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "topic_state": self.topic_state,
            "topic_label": self.topic_label,
            "current_user_message": self.current_user_message,
            "latest_user_message": self.latest_user_message,
            "latest_user_intent": self.latest_user_intent,
            "topic_continuity_confidence": self.topic_continuity_confidence,
            "latest_core_response": self.latest_core_response,
            "planned_messages": [item.to_dict() for item in self.planned_messages],
            "delivered_messages": [item.to_dict() for item in self.delivered_messages],
            "pending_messages": list(self.pending_messages),
            "delivered_summary": self.delivered_summary,
            "remaining_summary": self.remaining_summary,
            "core_agent_called": self.core_agent_called,
            "interrupted": self.interrupted,
            "manual_pause_requested": self.manual_pause_requested,
            "queue_cleared_reason": self.queue_cleared_reason,
            "cancelled_pending_count": self.cancelled_pending_count,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ResponseScopedWorkingMemory:
        data = payload or {}
        return cls(
            session_id=str(data.get("session_id") or ""),
            topic_state=str(data.get("topic_state") or "idle"),
            topic_label=str(data.get("topic_label") or ""),
            current_user_message=str(data.get("current_user_message") or ""),
            latest_user_message=str(data.get("latest_user_message") or ""),
            latest_user_intent=str(data.get("latest_user_intent") or ""),
            topic_continuity_confidence=float(
                data.get("topic_continuity_confidence") or 0.0
            ),
            latest_core_response=str(data.get("latest_core_response") or ""),
            planned_messages=_coerce_delivery_messages(
                data.get("planned_messages") or []
            ),
            delivered_messages=_coerce_delivery_messages(
                data.get("delivered_messages") or []
            ),
            pending_messages=_coerce_pending_messages(
                data.get("pending_messages") or []
            ),
            delivered_summary=str(data.get("delivered_summary") or ""),
            remaining_summary=str(data.get("remaining_summary") or ""),
            core_agent_called=bool(data.get("core_agent_called", False)),
            interrupted=bool(data.get("interrupted", False)),
            manual_pause_requested=bool(data.get("manual_pause_requested", False)),
            queue_cleared_reason=data.get("queue_cleared_reason"),
            cancelled_pending_count=int(data.get("cancelled_pending_count") or 0),
            updated_at=str(data.get("updated_at") or _utc_now_iso()),
        )


def _coerce_delivery_messages(items: list[Any]) -> list[DeliveryMessage]:
    messages: list[DeliveryMessage] = []
    for item in items:
        if isinstance(item, DeliveryMessage):
            messages.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        messages.append(
            DeliveryMessage(text=text, delay_ms=int(item.get("delay_ms") or 0))
        )
    return messages


def _coerce_pending_messages(items: list[Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        pending.append(
            {
                "text": text,
                "delay_ms": int(item.get("delay_ms") or 0),
                "available_at": item.get("available_at"),
            }
        )
    return pending


class ResponseStateStore(Protocol):
    def get(self, session_id: str) -> ResponseScopedWorkingMemory: ...

    def save(
        self, session_id: str, memory: ResponseScopedWorkingMemory
    ) -> ResponseScopedWorkingMemory: ...

    def clear(self, session_id: str) -> None: ...

    def mark_manual_pause(self, session_id: str) -> ResponseScopedWorkingMemory: ...

    def list_debug_view(self, session_id: str) -> dict[str, Any]: ...


class InMemoryResponseStateStore:
    def __init__(self) -> None:
        self._states: dict[str, ResponseScopedWorkingMemory] = {}

    def get(self, session_id: str) -> ResponseScopedWorkingMemory:
        return self._states.get(session_id) or ResponseScopedWorkingMemory(
            session_id=session_id
        )

    def save(
        self, session_id: str, memory: ResponseScopedWorkingMemory
    ) -> ResponseScopedWorkingMemory:
        memory.updated_at = _utc_now_iso()
        self._states[session_id] = memory
        return memory

    def clear(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def mark_manual_pause(self, session_id: str) -> ResponseScopedWorkingMemory:
        memory = self.get(session_id)
        memory.manual_pause_requested = True
        memory.updated_at = _utc_now_iso()
        self._states[session_id] = memory
        return memory

    def list_debug_view(self, session_id: str) -> dict[str, Any]:
        return self.get(session_id).to_dict()


class RedisResponseStateStore:
    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str = "conversation:working-memory:",
        ttl_seconds: int = 86400,
        client: Any | None = None,
    ) -> None:
        self._redis = client or self._build_client(redis_url)
        self._key_prefix = key_prefix
        self._ttl_seconds = max(int(ttl_seconds), 0)

    def get(self, session_id: str) -> ResponseScopedWorkingMemory:
        raw = self._redis.get(self._key(session_id))
        if not raw:
            return ResponseScopedWorkingMemory(session_id=session_id)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ResponseScopedWorkingMemory(session_id=session_id)
        memory = ResponseScopedWorkingMemory.from_dict(data)
        if not memory.session_id:
            memory.session_id = session_id
        return memory

    def save(
        self, session_id: str, memory: ResponseScopedWorkingMemory
    ) -> ResponseScopedWorkingMemory:
        memory.updated_at = _utc_now_iso()
        payload = json.dumps(memory.to_dict(), ensure_ascii=True)
        if self._ttl_seconds > 0:
            self._redis.set(self._key(session_id), payload, ex=self._ttl_seconds)
        else:
            self._redis.set(self._key(session_id), payload)
        return memory

    def clear(self, session_id: str) -> None:
        self._redis.delete(self._key(session_id))

    def mark_manual_pause(self, session_id: str) -> ResponseScopedWorkingMemory:
        memory = self.get(session_id)
        memory.manual_pause_requested = True
        return self.save(session_id, memory)

    def list_debug_view(self, session_id: str) -> dict[str, Any]:
        return self.get(session_id).to_dict()

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _build_client(self, redis_url: str) -> Any:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "RedisResponseStateStore requires the 'redis' package to be installed"
            ) from exc
        return redis.from_url(redis_url, decode_responses=True)
