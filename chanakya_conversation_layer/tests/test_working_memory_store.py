from __future__ import annotations

import json

from conversation_layer.schemas import DeliveryMessage
from conversation_layer.services.working_memory import (
    RedisResponseStateStore,
    ResponseScopedWorkingMemory,
)


class FakeRedisClient:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = ex

    def delete(self, key: str) -> None:
        self.data.pop(key, None)
        self.expiry.pop(key, None)


def test_redis_state_store_round_trip_and_pause_flag():
    client = FakeRedisClient()
    store = RedisResponseStateStore(
        redis_url="redis://unused",
        key_prefix="wm:",
        ttl_seconds=90,
        client=client,
    )
    memory = ResponseScopedWorkingMemory(
        session_id="s1",
        topic_state="active",
        latest_user_message="hello",
        planned_messages=[DeliveryMessage(text="First", delay_ms=0)],
        delivered_messages=[DeliveryMessage(text="First", delay_ms=0)],
        pending_messages=[
            {
                "text": "Second",
                "delay_ms": 5000,
                "available_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    )

    saved = store.save("s1", memory)
    paused = store.mark_manual_pause("s1")
    loaded = store.get("s1")

    assert saved.updated_at
    assert paused.manual_pause_requested is True
    assert loaded.session_id == "s1"
    assert loaded.manual_pause_requested is True
    assert loaded.pending_messages[0]["text"] == "Second"
    assert client.expiry["wm:s1"] == 90


def test_redis_state_store_returns_empty_memory_for_invalid_json():
    client = FakeRedisClient()
    client.data["wm:s1"] = "not-json"
    store = RedisResponseStateStore(
        redis_url="redis://unused",
        key_prefix="wm:",
        client=client,
    )

    loaded = store.get("s1")

    assert loaded.session_id == "s1"
    assert loaded.topic_state == "idle"


def test_redis_state_store_serializes_ascii_json():
    client = FakeRedisClient()
    store = RedisResponseStateStore(
        redis_url="redis://unused",
        key_prefix="wm:",
        client=client,
    )
    memory = ResponseScopedWorkingMemory(session_id="s1", latest_user_message="hello")

    store.save("s1", memory)

    payload = json.loads(client.data["wm:s1"])
    assert payload["session_id"] == "s1"
    assert payload["latest_user_message"] == "hello"
