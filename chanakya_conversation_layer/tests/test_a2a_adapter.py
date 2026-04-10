from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_framework import Message

from conversation_layer.schemas import ChatRequest
from core_agent_app.db import create_session_factory
from core_agent_app.services.agent_session_context import (
    SQLAlchemyAgentSessionContextStore,
)
from core_agent_app.services.core_agent import A2ACoreAgentAdapter
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider


class FakeA2AResponse:
    def __init__(self, text: str, context_id: str | None = None) -> None:
        self.text = text
        self.value = text
        self.raw_representation = SimpleNamespace(context_id=context_id)


class FakeA2AAgent:
    def __init__(self, *args, **kwargs) -> None:
        self.calls = []

    def create_session(self, *, session_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def run(self, messages, session=None):
        message = messages[0]
        self.calls.append(
            {
                "text": message.text,
                "additional_properties": dict(
                    getattr(message, "additional_properties", {}) or {}
                ),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return FakeA2AResponse("remote a2a reply", context_id="ctx-123")


class FlakyFakeA2AAgent(FakeA2AAgent):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_first = True

    async def run(self, messages, session=None):
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("temporary failure")
        return await super().run(messages, session=session)


class EmptyResponseA2AAgent(FakeA2AAgent):
    async def run(self, messages, session=None):
        message = messages[0]
        self.calls.append(
            {
                "text": message.text,
                "additional_properties": dict(
                    getattr(message, "additional_properties", {}) or {}
                ),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return FakeA2AResponse("", context_id=None)


class RepairingContextA2AAgent(FakeA2AAgent):
    async def run(self, messages, session=None):
        message = messages[0]
        self.calls.append(
            {
                "text": message.text,
                "additional_properties": dict(
                    getattr(message, "additional_properties", {}) or {}
                ),
                "session_id": getattr(session, "session_id", None),
            }
        )
        if message.additional_properties.get("context_id") == "broken-ctx":
            raise RuntimeError("context failure")
        return FakeA2AResponse("repaired reply", context_id="ctx-repaired")


class TimeoutA2AAgent(FakeA2AAgent):
    async def run(self, messages, session=None):
        raise TimeoutError("request timeout")


class FailTwiceA2AAgent(FakeA2AAgent):
    async def run(self, messages, session=None):
        raise RuntimeError("double failure")


def _build_adapter(tmp_path, agent_factory):
    database_url = f"sqlite:///{tmp_path / 'a2a-adapter.db'}"
    session_factory = create_session_factory(database_url)
    history_provider = SQLAlchemyHistoryProvider(session_factory)
    context_store = SQLAlchemyAgentSessionContextStore(session_factory)
    return A2ACoreAgentAdapter(
        url="http://a2a.example.test",
        debug=False,
        history_provider=history_provider,
        session_context_store=context_store,
        a2a_agent_factory=agent_factory,
    )


def test_a2a_adapter_persists_and_reuses_remote_context_id(tmp_path):
    adapter = _build_adapter(tmp_path, FakeA2AAgent)

    first = adapter.respond(ChatRequest(session_id="s1", message="hello"))
    second = adapter.respond(ChatRequest(session_id="s1", message="follow up"))

    assert first.metadata["source"] == "agent_framework_a2a"
    assert first.metadata["remote_context_id"] == "ctx-123"
    assert second.metadata["remote_context_id"] == "ctx-123"
    assert adapter._agent.calls[0]["additional_properties"] == {}
    assert adapter._agent.calls[1]["additional_properties"]["context_id"] == "ctx-123"
    debug_state = adapter.get_debug_state("s1")
    assert debug_state["session_context"]["remote_context_id"] == "ctx-123"


def test_a2a_adapter_falls_back_to_seeded_history_when_first_run_fails(tmp_path):
    adapter = _build_adapter(tmp_path, FlakyFakeA2AAgent)

    asyncio.run(
        adapter.history_provider.save_messages(
            "s1",
            [
                    Message("user", ["Earlier question"]),
                    Message("assistant", ["Earlier answer"]),
            ],
        )
    )
    response = adapter.respond(ChatRequest(session_id="s1", message="new question"))

    assert response.response == "remote a2a reply"
    assert len(adapter._agent.calls) == 1
    assert (
        "Continue this conversation using the transcript excerpt below."
        in adapter._agent.calls[0]["text"]
    )
    assert response.metadata["a2a_fallback_used"] is True
    assert response.metadata["a2a_failure_reason"] == "transport_failure"
    assert response.metadata["a2a_continuity_mode"] == "seeded_history"


def test_a2a_adapter_repairs_broken_remote_context_with_seeded_fallback(tmp_path):
    adapter = _build_adapter(tmp_path, RepairingContextA2AAgent)
    adapter.session_context_store.save(
        "s1",
        backend="a2a",
        remote_context_id="broken-ctx",
        remote_agent_url="http://a2a.example.test",
        target_key="default",
    )

    response = adapter.respond(ChatRequest(session_id="s1", message="new question"))

    assert response.response == "repaired reply"
    assert response.metadata["remote_context_id"] == "ctx-repaired"
    assert response.metadata["a2a_fallback_used"] is True
    assert response.metadata["a2a_repaired_continuity"] is True
    assert response.metadata["a2a_continuity_mode"] == "seeded_history"
    assert (
        adapter._agent.calls[0]["additional_properties"]["context_id"] == "broken-ctx"
    )
    assert adapter._agent.calls[1]["additional_properties"] == {}


def test_a2a_adapter_classifies_empty_response_as_malformed(tmp_path):
    adapter = _build_adapter(tmp_path, EmptyResponseA2AAgent)
    adapter.debug = True

    response = adapter.respond(ChatRequest(session_id="s1", message="hello"))

    assert response.metadata["source"] == "agent_framework_a2a_error"
    assert response.metadata["a2a_failure_reason"] == "malformed_response"
    assert response.metadata["a2a_continuity_mode"] == "failed"


def test_a2a_adapter_classifies_timeouts_in_debug_mode(tmp_path):
    adapter = _build_adapter(tmp_path, TimeoutA2AAgent)
    adapter.debug = True

    response = adapter.respond(ChatRequest(session_id="s1", message="hello"))

    assert response.metadata["source"] == "agent_framework_a2a_error"
    assert response.metadata["a2a_failure_reason"] == "timeout"
    debug_state = adapter.get_debug_state("s1")
    assert debug_state["last_attempt"]["failure_reason"] == "timeout"
    assert debug_state["capability_limitations"]["remote_memory"] == (
        "context_id only when exposed"
    )


def test_a2a_adapter_repeated_timeouts_do_not_persist_remote_context(tmp_path):
    adapter = _build_adapter(tmp_path, TimeoutA2AAgent)
    adapter.debug = True

    first = adapter.respond(ChatRequest(session_id="s1", message="hello"))
    second = adapter.respond(ChatRequest(session_id="s1", message="follow up"))
    session_context = adapter.session_context_store.get("s1", target_key="default")

    assert first.metadata["a2a_failure_reason"] == "timeout"
    assert second.metadata["a2a_failure_reason"] == "timeout"
    assert session_context["remote_context_id"] is None
    assert session_context["remote_agent_url"] is None


def test_a2a_adapter_returns_failed_error_when_fallback_also_fails(tmp_path):
    adapter = _build_adapter(tmp_path, FailTwiceA2AAgent)
    adapter.debug = True
    adapter.session_context_store.save(
        "s1",
        backend="a2a",
        remote_context_id="ctx-old",
        remote_agent_url="http://a2a.example.test",
        target_key="default",
    )

    response = adapter.respond(ChatRequest(session_id="s1", message="hello"))
    debug_state = adapter.get_debug_state("s1")
    session_context = adapter.session_context_store.get("s1", target_key="default")

    assert response.metadata["source"] == "agent_framework_a2a_error"
    assert response.metadata["a2a_continuity_mode"] == "failed"
    assert response.metadata["a2a_failure_reason"] == "transport_failure"
    assert debug_state["last_attempt"]["continuity_mode"] == "failed"
    assert session_context["remote_context_id"] == "ctx-old"


def test_a2a_adapter_scopes_remote_context_by_target(tmp_path):
    default_adapter = _build_adapter(tmp_path, FakeA2AAgent)
    other_adapter = _build_adapter(tmp_path, FakeA2AAgent)
    other_adapter.target_key = "other"
    other_adapter.target_label = "Other A2A"

    first = default_adapter.respond(ChatRequest(session_id="s1", message="hello"))
    second = other_adapter.respond(ChatRequest(session_id="s1", message="hello again"))

    default_context = default_adapter.session_context_store.get(
        "s1", target_key="default"
    )
    other_context = other_adapter.session_context_store.get("s1", target_key="other")

    assert first.metadata["a2a_target"] == "default"
    assert second.metadata["a2a_target"] == "other"
    assert default_context["remote_context_id"] == "ctx-123"
    assert other_context["remote_context_id"] == "ctx-123"


def test_a2a_adapter_seeded_history_strategy_skips_remote_context_attempt(tmp_path):
    adapter = _build_adapter(tmp_path, FakeA2AAgent)
    adapter.continuity_strategy = "seeded_history"

    asyncio.run(
        adapter.history_provider.save_messages(
            "s1",
            [
                    Message("user", ["Earlier question"]),
                    Message("assistant", ["Earlier answer"]),
            ],
        )
    )
    response = adapter.respond(ChatRequest(session_id="s1", message="follow up"))
    session_context = adapter.session_context_store.get("s1", target_key="default")

    assert response.metadata["a2a_continuity_mode"] == "seeded_history"
    assert response.metadata["a2a_fallback_used"] is False
    assert (
        "Continue this conversation using the transcript excerpt below."
        in adapter._agent.calls[0]["text"]
    )
    assert session_context["remote_context_id"] is None


def test_a2a_adapter_seeded_history_strategy_uses_fresh_local_session_each_turn(
    tmp_path,
):
    calls = []

    class RecordingSeededAgent(FakeA2AAgent):
        async def run(self, messages, session=None):
            message = messages[0]
            calls.append(
                {
                    "text": message.text,
                    "session_id": getattr(session, "session_id", None),
                }
            )
            return FakeA2AResponse("remote a2a reply", context_id="ctx-123")

    adapter = _build_adapter(tmp_path, RecordingSeededAgent)
    adapter.continuity_strategy = "seeded_history"

    adapter.respond(ChatRequest(session_id="s1", message="first"))
    adapter.respond(ChatRequest(session_id="s1", message="second"))

    assert calls[0]["session_id"] != calls[1]["session_id"]
