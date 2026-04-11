from __future__ import annotations

from types import SimpleNamespace

import chanakya.agent.runtime as runtime_module
from chanakya.agent.runtime import MAFRuntime
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.model import AgentProfileModel, ChatMessageModel, ChatSessionModel


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


class _FakeLocalResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


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


def test_runtime_restores_persisted_remote_context_after_recreation(monkeypatch) -> None:
    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)

    first_runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )
    first_runtime.run(
        "session-persisted",
        "hello",
        request_id="req-4",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )

    second_runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )
    second_runtime.run(
        "session-persisted",
        "follow up",
        request_id="req-5",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )

    second_agent = second_runtime._a2a_agent["http://127.0.0.1:18770"]
    assert second_agent.calls[0]["additional_properties"] == {"context_id": "ctx-123"}


def test_runtime_clear_session_state_removes_persisted_context(monkeypatch) -> None:
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
        "session-clear",
        "hello",
        request_id="req-6",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )
    runtime.clear_session_state("session-clear")

    context = runtime.session_context_store.get(
        "session-clear",
        target_key=runtime._a2a_target_key("http://127.0.0.1:18770"),
    )
    assert context["remote_context_id"] is None


def test_runtime_local_retries_with_seeded_history_when_air_rejects_history_messages(
    monkeypatch,
) -> None:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    runtime = MAFRuntime(_build_profile(), session_factory)

    with session_factory() as session:
        session.add(
            ChatSessionModel(
                id="session-local-fallback",
                title="New chat",
                created_at="2026-04-10T00:00:00+00:00",
                updated_at="2026-04-10T00:00:00+00:00",
            )
        )
        session.add_all(
            [
                ChatMessageModel(
                    session_id="session-local-fallback",
                    role="user",
                    content="How do I deploy this?",
                    request_id="req-old-1",
                    route=None,
                    metadata_json={},
                    created_at="2026-04-10T00:00:00+00:00",
                ),
                ChatMessageModel(
                    session_id="session-local-fallback",
                    role="assistant",
                    content="Use the deploy script in scripts/.",
                    request_id="req-old-2",
                    route=None,
                    metadata_json={},
                    created_at="2026-04-10T00:00:01+00:00",
                ),
            ]
        )
        session.commit()

    calls: list[dict[str, object]] = []

    class _FakeLocalAgent:
        def __init__(self, *, include_history: bool) -> None:
            self.include_history = include_history

        def create_session(self, *, session_id: str | None = None):
            return SimpleNamespace(session_id=session_id, state={})

        async def run(self, message, session=None, options=None):
            calls.append(
                {
                    "include_history": self.include_history,
                    "text": message.text,
                    "session_id": getattr(session, "session_id", None),
                    "request_id": getattr(session, "state", {}).get("request_id"),
                }
            )
            if self.include_history:
                raise RuntimeError(
                    "Error code: 400 - {'error': 'Error rendering prompt with jinja template: \"No user query found in messages.\".'}"
                )
            return _FakeLocalResponse("local reply")

    def _fake_build_profile_agent(*args, include_history=False, **kwargs):
        return _FakeLocalAgent(include_history=bool(include_history)), SimpleNamespace(
            availability=[]
        )

    monkeypatch.setattr(runtime_module, "build_profile_agent", _fake_build_profile_agent)
    monkeypatch.setattr(runtime_module, "extract_tool_execution_traces", lambda response, specs: [])

    result = runtime.run(
        "session-local-fallback",
        "Can you turn that into exact steps?",
        request_id="req-local-1",
        backend="local",
    )

    assert result.text == "local reply"
    assert result.metadata["local_seeded_history_fallback"] is True
    assert [call["include_history"] for call in calls] == [True, False]
    assert str(calls[1]["text"]).startswith(
        "Continue this conversation using the transcript excerpt below."
    )
    assert "User: How do I deploy this?" in str(calls[1]["text"])
    assert "Assistant: Use the deploy script in scripts/." in str(calls[1]["text"])
    assert "User: Can you turn that into exact steps?" in str(calls[1]["text"])
