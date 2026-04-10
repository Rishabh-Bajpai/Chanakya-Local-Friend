from __future__ import annotations

from types import SimpleNamespace

from chanakya.agent.runtime import MAFRuntime
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.model import AgentProfileModel


class _FakeA2AResponse:
    def __init__(self, text: str, context_id: str | None = None) -> None:
        self.text = text
        self.value = text
        self.raw_representation = SimpleNamespace(context_id=context_id)


class _FakeA2AAgent:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[dict[str, object]] = []

    def create_session(self, *, session_id: str | None = None):
        return SimpleNamespace(session_id=session_id)

    async def run(self, messages, session=None):
        message = messages[0]
        self.calls.append(
            {
                "text": message.text,
                "additional_properties": dict(getattr(message, "additional_properties", {}) or {}),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return _FakeA2AResponse("remote reply", context_id="ctx-123")


def _build_profile() -> AgentProfileModel:
    return AgentProfileModel(
        id="agent_chanakya",
        name="Chanakya",
        role="assistant",
        system_prompt="You are Chanakya.",
        personality="",
        tool_ids_json=[],
        workspace=None,
        heartbeat_enabled=False,
        heartbeat_interval_seconds=300,
        heartbeat_file_path=None,
        is_active=True,
        created_at="2026-04-10T00:00:00+00:00",
        updated_at="2026-04-10T00:00:00+00:00",
    )


def test_runtime_reuses_a2a_remote_context_across_turns(monkeypatch) -> None:
    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )

    first = runtime.run(
        "session-a2a",
        "hello",
        request_id="req-1",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )
    second = runtime.run(
        "session-a2a",
        "follow up",
        request_id="req-2",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )

    assert first.text == "remote reply"
    assert second.text == "remote reply"
    agent = runtime._a2a_agent["http://127.0.0.1:18770"]
    assert agent.calls[0]["additional_properties"] == {}
    assert agent.calls[1]["additional_properties"] == {"context_id": "ctx-123"}
    assert first.metadata["core_agent_backend"] == "a2a"
    assert second.metadata["remote_context_id"] == "ctx-123"


def test_runtime_metadata_reports_a2a_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")

    metadata = MAFRuntime.runtime_metadata(backend="a2a")

    assert metadata["backend"] == "a2a"
    assert metadata["endpoint"] == "http://127.0.0.1:18770"
    assert metadata["model"] is None


def test_runtime_includes_opencode_a2a_header_when_agent_and_model_are_selected(
    monkeypatch,
) -> None:
    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )

    runtime.run(
        "session-a2a-header",
        "ship it",
        request_id="req-3",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
        a2a_remote_agent="build",
        a2a_model_provider="lmstudio",
        a2a_model_id="qwen/qwen3.5-9b",
    )

    sent_text = runtime._a2a_agent["http://127.0.0.1:18770"].calls[0]["text"]
    assert str(sent_text).startswith(
        "[[opencode-options:agent=build;model_provider=lmstudio;model_id=qwen/qwen3.5-9b]]"
    )


def test_runtime_metadata_prefers_request_scoped_a2a_config() -> None:
    metadata = MAFRuntime.runtime_metadata(
        backend="a2a",
        a2a_url="http://a2a.example.test",
        a2a_model_id="demo-model",
    )

    assert metadata["endpoint"] == "http://a2a.example.test"
    assert metadata["model"] == "demo-model"
