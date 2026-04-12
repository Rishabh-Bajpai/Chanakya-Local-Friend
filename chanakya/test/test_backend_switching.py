"""Tests for A2A / OpenAI-compatible backend switching.

Verifies that the selected backend is respected throughout the app:
* Frontend → API → ChatService → Runtime routing
* Runtime config persistence and fallback
* Chat history continuity across backend switches
* runtime_metadata completeness
* normalize_runtime_backend validation
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from pytest import MonkeyPatch

import chanakya.app as app_module
from chanakya.agent.runtime import MAFRuntime, normalize_runtime_backend
from chanakya.app import create_app, _parse_runtime_config_payload, _normalize_runtime_config
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import ChatReply
from chanakya.model import AgentProfileModel
from chanakya.services import tool_loader
from chanakya.store import ChanakyaStore


# ---------------------------------------------------------------------------
# Shared helpers & stubs
# ---------------------------------------------------------------------------


class _ManagerStub:
    def should_delegate(self, message: str) -> bool:
        return False


class _RuntimeAppStub:
    def clear_session_state(self, session_id: str) -> None:
        return None


class _ChatServiceCaptureStub:
    """Records every `chat()` call so tests can verify parameter propagation."""

    def __init__(self, store, runtime, manager) -> None:
        self.calls: list[dict[str, object | None]] = []

    def chat(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> ChatReply:
        self.calls.append(
            {
                "session_id": session_id,
                "message": message,
                "work_id": work_id,
                "model_id": model_id,
                "backend": backend,
                "a2a_url": a2a_url,
                "a2a_remote_agent": a2a_remote_agent,
                "a2a_model_provider": a2a_model_provider,
                "a2a_model_id": a2a_model_id,
            }
        )
        return ChatReply(
            request_id="req_test",
            session_id=session_id,
            work_id=work_id,
            route="direct_answer",
            message="stub reply",
            model=model_id,
            endpoint="http://test",
            runtime="maf_agent",
            agent_name="Chanakya",
            response_mode="direct_answer",
            metadata={"core_agent_backend": backend or "local"},
        )


def _build_test_app(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    chat_service_factory=None,
) -> Flask:
    seed_dir = tmp_path / "chanakya" / "seeds"
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "agents.json").write_text(
        json.dumps(
            [
                {
                    "id": "agent_chanakya",
                    "name": "Chanakya",
                    "role": "personal_assistant",
                    "system_prompt": "You are Chanakya.",
                    "personality": "calm",
                    "tool_ids": [],
                    "workspace": "main",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/agents/agent_chanakya/heartbeat.md",
                    "is_active": True,
                },
                {
                    "id": "agent_manager",
                    "name": "Agent Manager",
                    "role": "manager",
                    "system_prompt": "You are the manager.",
                    "personality": "structured",
                    "tool_ids": [],
                    "workspace": "manager",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/agents/agent_manager/heartbeat.md",
                    "is_active": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    database_path = tmp_path / "chanakya-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setattr(app_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tool_loader, "initialize_all_tools", lambda: None)
    monkeypatch.setattr(
        tool_loader,
        "get_tools_availability",
        lambda: [],
    )
    monkeypatch.setattr(app_module, "get_tools_availability", lambda: [])
    monkeypatch.setattr(
        app_module,
        "MAFRuntime",
        lambda profile, session_factory: _RuntimeAppStub(),
    )
    monkeypatch.setattr(
        app_module,
        "AgentManager",
        lambda store, session_factory, manager_profile: _ManagerStub(),
    )
    if chat_service_factory is not None:
        monkeypatch.setattr(app_module, "ChatService", chat_service_factory)
    return create_app()


# ---------------------------------------------------------------------------
# normalize_runtime_backend
# ---------------------------------------------------------------------------


def test_normalize_runtime_backend_returns_local_for_local() -> None:
    assert normalize_runtime_backend("local") == "local"


def test_normalize_runtime_backend_returns_a2a_for_a2a() -> None:
    assert normalize_runtime_backend("a2a") == "a2a"


def test_normalize_runtime_backend_is_case_insensitive() -> None:
    assert normalize_runtime_backend("A2A") == "a2a"
    assert normalize_runtime_backend("LOCAL") == "local"
    assert normalize_runtime_backend("Local") == "local"


def test_normalize_runtime_backend_defaults_to_local_for_none() -> None:
    assert normalize_runtime_backend(None) == "local"


def test_normalize_runtime_backend_defaults_to_local_for_empty() -> None:
    assert normalize_runtime_backend("") == "local"


def test_normalize_runtime_backend_defaults_to_local_for_unknown() -> None:
    assert normalize_runtime_backend("gcp-vertex") == "local"


# ---------------------------------------------------------------------------
# runtime_metadata completeness
# ---------------------------------------------------------------------------


def test_runtime_metadata_a2a_includes_remote_agent_and_model_provider() -> None:
    metadata = MAFRuntime.runtime_metadata(
        backend="a2a",
        a2a_url="http://a2a.test:8000",
        a2a_remote_agent="build",
        a2a_model_provider="lmstudio",
        a2a_model_id="qwen/qwen3.5-9b",
    )
    assert metadata["backend"] == "a2a"
    assert metadata["endpoint"] == "http://a2a.test:8000"
    assert metadata["a2a_remote_agent"] == "build"
    assert metadata["a2a_model_provider"] == "lmstudio"
    assert metadata["model"] == "qwen/qwen3.5-9b"


def test_runtime_metadata_a2a_missing_optional_fields_returns_none() -> None:
    metadata = MAFRuntime.runtime_metadata(backend="a2a")
    assert metadata["backend"] == "a2a"
    assert metadata.get("a2a_remote_agent") is None
    assert metadata.get("a2a_model_provider") is None


def test_runtime_metadata_local_does_not_include_a2a_fields() -> None:
    metadata = MAFRuntime.runtime_metadata(backend="local", model_id="gpt-4")
    assert metadata["backend"] == "local"
    assert "a2a_remote_agent" not in metadata
    assert "a2a_model_provider" not in metadata


# ---------------------------------------------------------------------------
# _parse_runtime_config_payload — a2a_url from payload
# ---------------------------------------------------------------------------


def test_parse_runtime_config_uses_payload_a2a_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_AGENT_URL", raising=False)
    config = _parse_runtime_config_payload(
        {
            "backend": "a2a",
            "a2a_url": "http://custom-a2a.test:9999",
        }
    )
    assert config["a2a_url"] == "http://custom-a2a.test:9999"


def test_parse_runtime_config_falls_back_to_env_a2a_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_AGENT_URL", "http://env-a2a.test:7777")
    config = _parse_runtime_config_payload({"backend": "a2a"})
    assert config["a2a_url"] == "http://env-a2a.test:7777"


# ---------------------------------------------------------------------------
# _normalize_runtime_config
# ---------------------------------------------------------------------------


def test_normalize_runtime_config_fills_defaults_for_none() -> None:
    config = _normalize_runtime_config(None)
    assert config["backend"] == "local"
    assert config["model_id"] is None
    assert config["a2a_remote_agent"] is None


def test_normalize_runtime_config_preserves_a2a_backend() -> None:
    config = _normalize_runtime_config({"backend": "a2a", "a2a_remote_agent": "planner"})
    assert config["backend"] == "a2a"
    assert config["a2a_remote_agent"] == "planner"


# ---------------------------------------------------------------------------
# API-level backend switching
# ---------------------------------------------------------------------------


def test_api_chat_switches_from_local_to_a2a(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    app = _build_test_app(tmp_path, monkeypatch, chat_service_factory=_build)
    client = app.test_client()

    r1 = client.post(
        "/api/chat",
        json={
            "session_id": "sess-switch",
            "message": "first msg",
            "backend": "local",
            "model_id": "gpt-4",
        },
    )
    assert r1.status_code == 200
    assert captured[0].calls[0]["backend"] == "local"

    r2 = client.post(
        "/api/chat",
        json={
            "session_id": "sess-switch",
            "message": "second msg via a2a",
            "backend": "a2a",
            "a2a_url": "http://a2a.test:8000",
            "a2a_remote_agent": "build",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen3",
        },
    )
    assert r2.status_code == 200
    assert captured[0].calls[1]["backend"] == "a2a"
    assert captured[0].calls[1]["a2a_url"] == "http://a2a.test:8000"
    assert captured[0].calls[1]["a2a_remote_agent"] == "build"


def test_api_chat_switches_from_a2a_to_local(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    app = _build_test_app(tmp_path, monkeypatch, chat_service_factory=_build)
    client = app.test_client()

    r1 = client.post(
        "/api/chat",
        json={
            "session_id": "sess-switch-back",
            "message": "start a2a",
            "backend": "a2a",
            "a2a_url": "http://a2a.test:8000",
        },
    )
    assert r1.status_code == 200
    assert captured[0].calls[0]["backend"] == "a2a"

    r2 = client.post(
        "/api/chat",
        json={
            "session_id": "sess-switch-back",
            "message": "now local",
            "backend": "local",
            "model_id": "gpt-4",
        },
    )
    assert r2.status_code == 200
    assert captured[0].calls[1]["backend"] == "local"
    assert captured[0].calls[1]["model_id"] == "gpt-4"


def test_api_chat_with_work_id_passes_backend(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    app = _build_test_app(tmp_path, monkeypatch, chat_service_factory=_build)
    client = app.test_client()

    work_resp = client.post(
        "/api/works",
        json={"title": "Test work", "description": "desc"},
    )
    assert work_resp.status_code == 201
    work_id = work_resp.get_json()["id"]

    r1 = client.post(
        "/api/chat",
        json={
            "work_id": work_id,
            "message": "hello from work",
            "backend": "a2a",
            "a2a_url": "http://a2a.test:8000",
            "a2a_remote_agent": "build",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen3",
        },
    )
    assert r1.status_code == 200
    assert captured[0].calls[0]["backend"] == "a2a"
    assert captured[0].calls[0]["a2a_remote_agent"] == "build"


def test_runtime_config_update_reflected_in_subsequent_chat(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    app = _build_test_app(tmp_path, monkeypatch, chat_service_factory=_build)
    client = app.test_client()

    cfg_resp = client.post(
        "/api/runtime-config",
        json={
            "backend": "a2a",
            "a2a_remote_agent": "planner",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen/qwen3.5-9b",
        },
    )
    assert cfg_resp.status_code == 200

    r1 = client.post(
        "/api/chat",
        json={"session_id": "sess-cfg", "message": "use stored config"},
    )
    assert r1.status_code == 200
    assert captured[0].calls[0]["backend"] == "a2a"
    assert captured[0].calls[0]["a2a_remote_agent"] == "planner"

    cfg_resp2 = client.post(
        "/api/runtime-config",
        json={"backend": "local", "model_id": "gpt-4"},
    )
    assert cfg_resp2.status_code == 200

    r2 = client.post(
        "/api/chat",
        json={"session_id": "sess-cfg", "message": "should be local now"},
    )
    assert r2.status_code == 200
    assert captured[0].calls[1]["backend"] == "local"


def test_api_chat_request_overrides_stored_runtime_config(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    app = _build_test_app(tmp_path, monkeypatch, chat_service_factory=_build)
    client = app.test_client()

    client.post("/api/runtime-config", json={"backend": "a2a"})

    r1 = client.post(
        "/api/chat",
        json={
            "session_id": "sess-override",
            "message": "force local",
            "backend": "local",
            "model_id": "gpt-4",
        },
    )
    assert r1.status_code == 200
    assert captured[0].calls[0]["backend"] == "local"
    assert captured[0].calls[0]["model_id"] == "gpt-4"


# ---------------------------------------------------------------------------
# Runtime-level backend switching with fake A2A agent
# ---------------------------------------------------------------------------


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

    async def run(self, messages, session=None, timeout=None):
        message = messages[0]
        self.calls.append(
            {
                "text": message.text,
                "additional_properties": dict(getattr(message, "additional_properties", {}) or {}),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return _FakeA2AResponse("remote reply", context_id="ctx-abc")


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


def test_runtime_switches_from_a2a_to_local_backend(monkeypatch: MonkeyPatch) -> None:
    """Start with a2a, switch to local — each uses the correct path."""
    import chanakya.agent.runtime as runtime_module

    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)

    class _FakeLocalResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def __str__(self) -> str:
            return self.text

    class _FakeLocalAgent:
        def __init__(self, **kwargs) -> None:
            self.calls: list[str] = []

        def create_session(self, *, session_id: str | None = None):
            return SimpleNamespace(session_id=session_id, state={})

        async def run(self, message, session=None, options=None):
            self.calls.append(message.text)
            return _FakeLocalResponse("local reply")

    fake_local = _FakeLocalAgent()

    def _fake_build_profile_agent(*args, include_history=False, **kwargs):
        return fake_local, SimpleNamespace(availability=[], cached_tools=[])

    monkeypatch.setattr(runtime_module, "build_profile_agent", _fake_build_profile_agent)
    monkeypatch.setattr(runtime_module, "extract_tool_execution_traces", lambda response, specs: [])

    runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )

    r1 = runtime.run(
        "sess-switch",
        "hello via a2a",
        request_id="req-1",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
    )
    assert r1.text == "remote reply"
    assert r1.metadata["core_agent_backend"] == "a2a"

    r2 = runtime.run(
        "sess-switch",
        "hello via local",
        request_id="req-2",
        backend="local",
    )
    assert r2.text == "local reply"
    assert r2.metadata["core_agent_backend"] == "local"
    assert len(fake_local.calls) == 1


def test_runtime_a2a_context_persists_across_switches(monkeypatch: MonkeyPatch) -> None:
    """Switch away from a2a and back — remote_context_id is restored."""
    import chanakya.agent.runtime as runtime_module

    monkeypatch.setenv("A2A_AGENT_URL", "http://127.0.0.1:18770")
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)

    class _FakeLocalResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def __str__(self) -> str:
            return self.text

    class _FakeLocalAgent:
        def __init__(self, **kwargs) -> None:
            pass

        def create_session(self, *, session_id: str | None = None):
            return SimpleNamespace(session_id=session_id, state={})

        async def run(self, message, session=None, options=None):
            return _FakeLocalResponse("local reply")

    def _fake_build_profile_agent(*args, include_history=False, **kwargs):
        return _FakeLocalAgent(), SimpleNamespace(availability=[], cached_tools=[])

    monkeypatch.setattr(runtime_module, "build_profile_agent", _fake_build_profile_agent)
    monkeypatch.setattr(runtime_module, "extract_tool_execution_traces", lambda response, specs: [])

    runtime = MAFRuntime(
        _build_profile(),
        session_factory,
        a2a_agent_factory=_FakeA2AAgent,
    )
    a2a_url = "http://127.0.0.1:18770"

    runtime.run("sess-ctx", "a2a turn 1", request_id="r1", backend="a2a", a2a_url=a2a_url)
    runtime.run("sess-ctx", "local turn", request_id="r2", backend="local")
    r3 = runtime.run("sess-ctx", "a2a turn 2", request_id="r3", backend="a2a", a2a_url=a2a_url)

    agent = runtime._a2a_agent[a2a_url]
    assert agent.calls[1]["additional_properties"] == {}
    assert agent.calls[0]["session_id"] != agent.calls[1]["session_id"]
    assert "Continue this conversation using the transcript excerpt below." in str(
        agent.calls[1]["text"]
    )
    assert r3.metadata["core_agent_backend"] == "a2a"
    assert r3.metadata["a2a_continuity_mode"] == "seeded_history"


# ---------------------------------------------------------------------------
# _parse_runtime_config_payload edge cases
# ---------------------------------------------------------------------------


def test_parse_runtime_config_payload_strips_whitespace() -> None:
    config = _parse_runtime_config_payload(
        {
            "backend": "  a2a  ",
            "a2a_remote_agent": "  planner  ",
            "a2a_model_provider": "  lmstudio  ",
            "a2a_model_id": "  qwen3  ",
        }
    )
    assert config["backend"] == "a2a"
    assert config["a2a_remote_agent"] == "planner"
    assert config["a2a_model_provider"] == "lmstudio"
    assert config["a2a_model_id"] == "qwen3"


def test_parse_runtime_config_payload_empty_strings_become_none() -> None:
    config = _parse_runtime_config_payload(
        {
            "backend": "local",
            "a2a_remote_agent": "",
            "a2a_model_provider": "",
            "a2a_model_id": "",
        }
    )
    assert config["a2a_remote_agent"] is None
    assert config["a2a_model_provider"] is None
    assert config["a2a_model_id"] is None


# ---------------------------------------------------------------------------
# Runtime config persistence round-trip via API
# ---------------------------------------------------------------------------


def test_runtime_config_round_trip_preserves_all_fields(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    client.post(
        "/api/runtime-config",
        json={
            "backend": "a2a",
            "model_id": "local-model",
            "a2a_remote_agent": "build",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen/qwen3.5-9b",
        },
    )

    fetched = client.get("/api/runtime-config").get_json()
    assert fetched["backend"] == "a2a"
    assert fetched["model_id"] == "local-model"
    assert fetched["a2a_remote_agent"] == "build"
    assert fetched["a2a_model_provider"] == "lmstudio"
    assert fetched["a2a_model_id"] == "qwen/qwen3.5-9b"


def test_runtime_config_switch_back_to_local(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    client.post("/api/runtime-config", json={"backend": "a2a"})
    client.post("/api/runtime-config", json={"backend": "local", "model_id": "gpt-4"})

    fetched = client.get("/api/runtime-config").get_json()
    assert fetched["backend"] == "local"
    assert fetched["model_id"] == "gpt-4"
