from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

from flask import Flask
from pytest import MonkeyPatch

import chanakya.app as app_module
from chanakya.agent_manager import AgentManager, ManagerRunResult, WORKFLOW_INFORMATION
from chanakya.app import create_app
from chanakya.db import build_engine, build_session_factory
from chanakya.domain import ChatReply, TASK_STATUS_DONE, now_iso
from chanakya.model import ChatMessageModel, TemporaryAgentModel, WorkAgentSessionModel
from chanakya.services import tool_loader
from chanakya.store import ChanakyaStore


class _ManagerStub:
    def should_delegate(self, message: str) -> bool:
        return False


class _RuntimeAppStub:
    def clear_session_state(self, session_id: str) -> None:
        return None


def _build_test_app(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    runtime_factory=None,
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
        lambda: [
            {
                "tool_id": "mcp_fetch",
                "status": "available",
                "tool_name": "mcp_fetch",
                "server_name": "fetch",
            }
        ],
    )
    monkeypatch.setattr(
        app_module,
        "get_tools_availability",
        lambda: [
            {
                "tool_id": "mcp_fetch",
                "status": "available",
                "tool_name": "mcp_fetch",
                "server_name": "fetch",
            }
        ],
    )
    monkeypatch.setattr(
        app_module,
        "MAFRuntime",
        runtime_factory or (lambda profile, session_factory: _RuntimeAppStub()),
    )
    monkeypatch.setattr(
        app_module, "AgentManager", lambda store, session_factory, manager_profile: _ManagerStub()
    )
    return create_app()


class _ChatServiceCaptureStub:
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
        conversation_tone_instruction: str | None = None,
        tts_instruction: str | None = None,
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
                "conversation_tone_instruction": conversation_tone_instruction,
                "tts_instruction": tts_instruction,
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


class _RuntimeDelegationStub:
    def __init__(self, profile) -> None:
        self.profile = profile

    def runtime_metadata(
        self,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> dict[str, str | None]:
        return {
            "model": a2a_model_id or model_id,
            "endpoint": a2a_url or "http://test",
            "runtime": "maf_agent",
            "backend": backend or "local",
            "a2a_remote_agent": a2a_remote_agent,
            "a2a_model_provider": a2a_model_provider,
            "a2a_model_id": a2a_model_id,
        }

    def clear_session_state(self, session_id: str) -> None:
        return None


def _build_work_memory_app(tmp_path: Path, monkeypatch: MonkeyPatch) -> Flask:
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
                {
                    "id": "agent_developer",
                    "name": "Developer",
                    "role": "developer",
                    "system_prompt": "You are the developer.",
                    "personality": "focused",
                    "tool_ids": [],
                    "workspace": "dev",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/agents/agent_developer/heartbeat.md",
                    "is_active": True,
                },
                {
                    "id": "agent_tester",
                    "name": "Tester",
                    "role": "tester",
                    "system_prompt": "You are the tester.",
                    "personality": "careful",
                    "tool_ids": [],
                    "workspace": "qa",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/agents/agent_tester/heartbeat.md",
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
    monkeypatch.setattr(tool_loader, "get_tools_availability", lambda: [])
    monkeypatch.setattr(app_module, "get_tools_availability", lambda: [])
    monkeypatch.setattr(
        app_module,
        "MAFRuntime",
        lambda profile, session_factory: _RuntimeDelegationStub(profile),
    )

    class _WorkMemoryManager(AgentManager):
        def execute(self, *, session_id: str, request_id: str, root_task_id: str, message: str):
            worker_id = "agent_developer" if message.startswith("dev:") else "agent_tester"
            profile = self.store.get_agent_profile(worker_id)
            text = self._run_profile_prompt(profile, message).strip()
            return ManagerRunResult(
                text=text,
                workflow_type=WORKFLOW_INFORMATION,
                child_task_ids=[root_task_id],
                manager_agent_id=self.manager_profile.id,
                worker_agent_ids=[worker_id],
                task_status=TASK_STATUS_DONE,
                result_json={"workflow_type": WORKFLOW_INFORMATION, "worker_id": worker_id},
            )

    monkeypatch.setattr(app_module, "AgentManager", _WorkMemoryManager)
    return create_app()


def test_agent_create_and_update_api_persists_configuration(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    create_response = client.post(
        "/api/agents",
        json={
            "name": "Developer Alpha",
            "role": "developer",
            "system_prompt": "You are a strong implementation agent.",
            "personality": "methodical, exact",
            "tool_ids": [],
            "workspace": "alpha-workspace",
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 90,
            "heartbeat_file_path": "chanakya_data/agents/agent_developer_alpha/heartbeat.md",
            "is_active": True,
        },
    )

    assert create_response.status_code == 201
    created = create_response.get_json()
    assert created["role"] == "developer"
    assert created["workspace"] == "alpha-workspace"
    assert created["heartbeat_enabled"] is True

    heartbeat_file = tmp_path / "chanakya_data/agents/agent_developer_alpha/heartbeat.md"
    assert heartbeat_file.exists()

    update_response = client.put(
        f"/api/agents/{created['id']}",
        json={
            "name": "Developer Alpha",
            "role": "developer",
            "system_prompt": "You are the updated implementation agent.",
            "personality": "fast, careful",
            "tool_ids": [],
            "workspace": "updated-workspace",
            "heartbeat_enabled": False,
            "heartbeat_interval_seconds": 120,
            "heartbeat_file_path": "chanakya_data/agents/agent_developer_alpha/heartbeat.md",
            "is_active": False,
        },
    )

    assert update_response.status_code == 200
    updated = update_response.get_json()
    assert updated["system_prompt"] == "You are the updated implementation agent."
    assert updated["workspace"] == "updated-workspace"
    assert updated["heartbeat_enabled"] is False
    assert updated["is_active"] is False


def test_api_chat_passes_backend_choice_to_chat_service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build_chat_service(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    monkeypatch.setattr(app_module, "ChatService", _build_chat_service)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post(
        "/api/chat",
        json={
            "session_id": "session_backend",
            "message": "hello via a2a",
            "backend": "a2a",
            "model_id": "ignored-for-a2a",
            "a2a_url": "http://127.0.0.1:18770",
            "a2a_remote_agent": "build",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen/qwen3.5-9b",
        },
    )

    assert response.status_code == 200
    assert captured[0].calls[0]["backend"] == "a2a"
    assert captured[0].calls[0]["model_id"] == "ignored-for-a2a"
    assert captured[0].calls[0]["a2a_url"] == "http://127.0.0.1:18770"
    assert captured[0].calls[0]["a2a_remote_agent"] == "build"
    assert captured[0].calls[0]["a2a_model_provider"] == "lmstudio"
    assert captured[0].calls[0]["a2a_model_id"] == "qwen/qwen3.5-9b"
    payload = response.get_json()
    assert payload["metadata"]["core_agent_backend"] == "a2a"


def test_api_runtime_config_persists_shared_settings(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    update_response = client.post(
        "/api/runtime-config",
        json={
            "backend": "a2a",
            "model_id": "ignored-local-model",
            "a2a_remote_agent": "build",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen/qwen3.5-9b",
            "conversation_tone_instruction": "Dry and direct.",
            "tts_instruction": "Use crisp spoken phrasing.",
        },
    )

    assert update_response.status_code == 200
    updated = update_response.get_json()
    assert updated["backend"] == "a2a"
    assert updated["a2a_remote_agent"] == "build"
    assert updated["a2a_model_provider"] == "lmstudio"
    assert updated["a2a_model_id"] == "qwen/qwen3.5-9b"
    assert updated["conversation_tone_instruction"] == "Dry and direct."
    assert updated["tts_instruction"] == "Use crisp spoken phrasing."
    assert updated["a2a_url"] == app_module.get_a2a_agent_url()

    get_response = client.get("/api/runtime-config")
    assert get_response.status_code == 200
    fetched = get_response.get_json()
    assert fetched["backend"] == "a2a"
    assert fetched["a2a_remote_agent"] == "build"
    assert fetched["conversation_tone_instruction"] == "Dry and direct."
    assert fetched["tts_instruction"] == "Use crisp spoken phrasing."


def test_api_chat_uses_applied_runtime_config_when_request_omits_it(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build_chat_service(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    monkeypatch.setattr(app_module, "ChatService", _build_chat_service)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    config_response = client.post(
        "/api/runtime-config",
        json={
            "backend": "a2a",
            "model_id": "local-model",
            "a2a_remote_agent": "planner",
            "a2a_model_provider": "lmstudio",
            "a2a_model_id": "qwen/qwen3.5-9b",
            "conversation_tone_instruction": "Dry and precise.",
            "tts_instruction": "Keep it easy to speak.",
        },
    )
    assert config_response.status_code == 200

    response = client.post(
        "/api/chat",
        json={
            "session_id": "session_runtime_default",
            "message": "use shared config",
        },
    )

    assert response.status_code == 200
    assert captured[0].calls[0]["backend"] == "a2a"
    assert captured[0].calls[0]["model_id"] == "local-model"
    assert captured[0].calls[0]["a2a_url"] is None
    assert captured[0].calls[0]["a2a_remote_agent"] == "planner"
    assert captured[0].calls[0]["a2a_model_provider"] == "lmstudio"
    assert captured[0].calls[0]["a2a_model_id"] == "qwen/qwen3.5-9b"
    assert captured[0].calls[0]["conversation_tone_instruction"] == "Dry and precise."
    assert captured[0].calls[0]["tts_instruction"] == "Keep it easy to speak."


def test_api_chat_request_overrides_stored_conversation_preferences(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: list[_ChatServiceCaptureStub] = []

    def _build_chat_service(store, runtime, manager):
        stub = _ChatServiceCaptureStub(store, runtime, manager)
        captured.append(stub)
        return stub

    monkeypatch.setattr(app_module, "ChatService", _build_chat_service)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    client.post(
        "/api/runtime-config",
        json={
            "backend": "local",
            "conversation_tone_instruction": "Stored tone.",
            "tts_instruction": "Stored tts.",
        },
    )

    response = client.post(
        "/api/chat",
        json={
            "session_id": "session_runtime_override",
            "message": "override preferences",
            "conversation_tone_instruction": "Request tone.",
            "tts_instruction": "Request tts.",
        },
    )

    assert response.status_code == 200
    assert captured[0].calls[0]["conversation_tone_instruction"] == "Request tone."
    assert captured[0].calls[0]["tts_instruction"] == "Request tts."


def test_api_a2a_options_returns_discovered_agents_and_models(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_module,
        "discover_a2a_options",
        lambda url: {
            "opencode_url": "http://127.0.0.1:18496",
            "remote_agents": ["build", "planner"],
            "providers": ["lmstudio"],
            "models": [
                {
                    "provider": "lmstudio",
                    "id": "qwen/qwen3.5-9b",
                    "label": "Qwen 3.5 9B",
                }
            ],
        },
    )
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get("/api/a2a/options?url=http://127.0.0.1:18770")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["remote_agents"] == ["build", "planner"]
    assert payload["providers"] == ["lmstudio"]
    assert payload["models"][0]["id"] == "qwen/qwen3.5-9b"


def test_agent_create_api_uses_unique_ids_for_duplicate_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    first = client.post(
        "/api/agents",
        json={
            "name": "Developer Alpha",
            "role": "developer",
            "system_prompt": "You are a strong implementation agent.",
            "personality": "methodical",
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": False,
            "heartbeat_interval_seconds": 90,
            "heartbeat_file_path": None,
            "is_active": True,
        },
    )
    second = client.post(
        "/api/agents",
        json={
            "name": "Developer Alpha",
            "role": "developer",
            "system_prompt": "You are another implementation agent.",
            "personality": "careful",
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": False,
            "heartbeat_interval_seconds": 90,
            "heartbeat_file_path": None,
            "is_active": True,
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_payload = first.get_json()
    second_payload = second.get_json()
    assert first_payload["id"] != second_payload["id"]


def test_agent_create_api_rejects_invalid_payload(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post(
        "/api/agents",
        json={
            "name": "",
            "role": "developer",
            "system_prompt": "",
            "tool_ids": "bad",
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 0,
        },
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_agent_create_api_rejects_invalid_boolean_and_heartbeat_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    bad_bool = client.post(
        "/api/agents",
        json={
            "name": "Bad Bool",
            "role": "developer",
            "system_prompt": "Prompt",
            "personality": None,
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": "false",
            "heartbeat_interval_seconds": 30,
            "heartbeat_file_path": None,
            "is_active": True,
        },
    )
    bad_path = client.post(
        "/api/agents",
        json={
            "name": "Bad Path",
            "role": "developer",
            "system_prompt": "Prompt",
            "personality": None,
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 30,
            "heartbeat_file_path": "../escape.md",
            "is_active": True,
        },
    )
    sneaky_path = client.post(
        "/api/agents",
        json={
            "name": "Sneaky Path",
            "role": "developer",
            "system_prompt": "Prompt",
            "personality": None,
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 30,
            "heartbeat_file_path": "chanakya_data/agents/./../escape.md",
            "is_active": True,
        },
    )

    assert bad_bool.status_code == 400
    assert bad_bool.get_json()["error"] == "heartbeat_enabled must be a boolean"
    assert bad_path.status_code == 400
    assert "heartbeat_file_path" in bad_path.get_json()["error"]
    assert sneaky_path.status_code == 400
    assert "heartbeat_file_path" in sneaky_path.get_json()["error"]


def test_subagents_api_returns_persisted_temporary_agents(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    database_path = tmp_path / "chanakya-test.db"
    engine = build_engine(f"sqlite:///{database_path}")
    session_factory = build_session_factory(engine)
    store = ChanakyaStore(session_factory)
    store.create_request(
        request_id="req_1",
        session_id="session_1",
        user_message="Test subagent listing",
        status="completed",
        route="test",
        root_task_id="task_parent",
    )
    store.create_task(
        task_id="task_parent",
        request_id="req_1",
        parent_task_id=None,
        title="Parent task",
        summary=None,
        status=TASK_STATUS_DONE,
        owner_agent_id="agent_developer",
        task_type="developer_execution",
    )
    store.create_temporary_agent(
        TemporaryAgentModel(
            id="tagent_1",
            request_id="req_1",
            session_id="session_1",
            parent_agent_id="agent_developer",
            parent_task_id="task_parent",
            creator_role="developer",
            name="Developer :: facts",
            role="research_helper",
            purpose="Inspect likely touchpoints.",
            system_prompt="Return likely touchpoints.",
            tool_ids_json=[],
            workspace="alpha-workspace",
            status="cleaned",
            cleanup_reason="completed",
            metadata_json={"expected_output": "touchpoints"},
            created_at="2026-04-03T00:00:00+00:00",
            updated_at="2026-04-03T00:00:00+00:00",
            activated_at="2026-04-03T00:00:01+00:00",
            cleaned_up_at="2026-04-03T00:00:02+00:00",
        )
    )

    response = client.get("/api/subagents?session_id=session_1")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["subagents"]) == 1
    assert payload["subagents"][0]["parent_agent_id"] == "agent_developer"
    assert payload["subagents"][0]["status"] == "cleaned"


def test_agent_create_api_accepts_null_optional_fields(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post(
        "/api/agents",
        json={
            "name": "Null Friendly",
            "role": "researcher",
            "system_prompt": "Prompt",
            "personality": None,
            "tool_ids": [],
            "workspace": None,
            "heartbeat_enabled": False,
            "heartbeat_interval_seconds": 45,
            "heartbeat_file_path": None,
            "is_active": True,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["workspace"] is None
    assert payload["heartbeat_file_path"] == "chanakya_data/agents/agent_null_friendly/heartbeat.md"
    assert payload["personality"] == ""


def test_tools_availability_api_returns_payload(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get("/api/tools/availability")

    assert response.status_code == 200
    assert "tools" in response.get_json()


def test_session_pause_api_uses_chat_service_public_method(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    class _ChatServiceStub:
        paused_session_ids: list[str] = []

        def __init__(self, store: object, runtime: object, manager: object | None = None) -> None:
            self._conversation_layer = None

        def request_manual_pause(self, session_id: str) -> dict[str, object]:
            self.paused_session_ids.append(session_id)
            return {"session_id": session_id, "status": "paused"}

    monkeypatch.setattr(app_module, "ChatService", _ChatServiceStub)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.post("/api/sessions/session_123/pause")

    assert response.status_code == 200
    assert response.get_json() == {
        "session_id": "session_123",
        "working_memory": {"session_id": "session_123", "status": "paused"},
    }
    assert _ChatServiceStub.paused_session_ids == ["session_123"]


def test_startup_sync_adds_default_tools_to_seeded_agents(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get("/api/agents")

    assert response.status_code == 200
    agents = {item["id"]: item for item in response.get_json()["agents"]}
    assert set(agents["agent_chanakya"]["tool_ids"]) >= {
        "mcp_websearch",
        "mcp_fetch",
        "mcp_calculator",
    }
    assert set(agents["agent_manager"]["tool_ids"]) >= {
        "mcp_websearch",
        "mcp_fetch",
        "mcp_calculator",
    }


def test_work_create_list_and_history_apis(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    created = client.post(
        "/api/works",
        json={"title": "Global Warming Report", "description": "2026 draft"},
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    work_id = created_payload["id"]
    assert created_payload["agent_session_count"] == 2

    listed = client.get("/api/works")
    assert listed.status_code == 200
    listed_ids = [item["id"] for item in listed.get_json()["works"]]
    assert work_id in listed_ids

    sessions_response = client.get(f"/api/works/{work_id}/sessions")
    assert sessions_response.status_code == 200
    sessions_payload = sessions_response.get_json()
    sessions = sessions_payload["sessions"]
    assert len(sessions) == 2
    chanakya_mapping = next(item for item in sessions if item["agent_id"] == "agent_chanakya")

    database_path = tmp_path / "chanakya-test.db"
    engine = build_engine(f"sqlite:///{database_path}")
    session_factory = build_session_factory(engine)
    with session_factory() as db_session:
        db_session.add(
            ChatMessageModel(
                session_id=chanakya_mapping["session_id"],
                role="assistant",
                content="Initial report draft ready.",
                request_id="req_work_1",
                route="delegated_manager",
                metadata_json={"work_test": True},
                created_at=now_iso(),
            )
        )
        db_session.commit()

    history_response = client.get(f"/api/works/{work_id}/history")
    assert history_response.status_code == 200
    history_payload = history_response.get_json()
    assert history_payload["work"]["id"] == work_id
    histories = history_payload["agent_histories"]
    assert "task_flow" in history_payload
    assert "tasks" in history_payload
    assert "requests" in history_payload
    assert "limits" in history_payload
    chanakya_history = next(item for item in histories if item["agent_id"] == "agent_chanakya")
    assert any(
        msg["content"] == "Initial report draft ready." for msg in chanakya_history["messages"]
    )


def test_work_session_mapping_is_unique_per_agent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _build_test_app(tmp_path, monkeypatch)

    database_path = tmp_path / "chanakya-test.db"
    engine = build_engine(f"sqlite:///{database_path}")
    session_factory = build_session_factory(engine)
    store = ChanakyaStore(session_factory)

    store.create_work(work_id="work_test_unique", title="Unique Mapping", description=None)
    first_session = store.ensure_work_agent_session(
        work_id="work_test_unique",
        agent_id="agent_chanakya",
        session_id="session_first",
        session_title="Unique Mapping - Chanakya",
    )
    second_session = store.ensure_work_agent_session(
        work_id="work_test_unique",
        agent_id="agent_chanakya",
        session_id="session_second",
        session_title="Unique Mapping - Chanakya",
    )

    mappings = store.list_work_agent_sessions("work_test_unique")
    assert len([item for item in mappings if item["agent_id"] == "agent_chanakya"]) == 1
    assert first_session == second_session == "session_first"

    with session_factory() as db_session:
        rows = db_session.query(WorkAgentSessionModel).filter_by(work_id="work_test_unique").all()
    assert len(rows) == 1


def test_work_delete_api_removes_work_history(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    created = client.post(
        "/api/works",
        json={"title": "Disposable Work", "description": "delete me"},
    )
    assert created.status_code == 201
    work_id = created.get_json()["id"]

    deleted = client.delete(f"/api/works/{work_id}")
    assert deleted.status_code == 200
    deleted_payload = deleted.get_json()
    assert deleted_payload["deleted"] is True
    assert deleted_payload["work_id"] == work_id

    listed = client.get("/api/works")
    assert listed.status_code == 200
    listed_ids = [item["id"] for item in listed.get_json()["works"]]
    assert work_id not in listed_ids

    history_response = client.get(f"/api/works/{work_id}/history")
    assert history_response.status_code == 404


def test_work_delete_clears_runtime_session_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    cleared: list[str] = []

    class _RuntimeCaptureStub:
        def clear_session_state(self, session_id: str) -> None:
            cleared.append(session_id)

    app = _build_test_app(
        tmp_path,
        monkeypatch,
        runtime_factory=lambda profile, session_factory: _RuntimeCaptureStub(),
    )
    client = app.test_client()

    created = client.post(
        "/api/works",
        json={"title": "Disposable Work", "description": "delete me"},
    )
    work_id = created.get_json()["id"]

    deleted = client.delete(f"/api/works/{work_id}")

    assert deleted.status_code == 200
    assert len(cleared) == 2


def test_work_api_preserves_per_agent_memory_for_local_backend(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def __str__(self) -> str:
            return self.text

    class _FakeAgent:
        def __init__(self, *, include_history: bool) -> None:
            self.include_history = include_history

        def create_session(self, *, session_id: str | None = None):
            return type("Session", (), {"session_id": session_id, "state": {}})()

        async def run(self, message, session=None, options=None):
            if self.include_history:
                raise RuntimeError(
                    "Error code: 400 - {'error': 'Error rendering prompt with jinja template: \"No user query found in messages.\".'}"
                )
            return _FakeResponse(str(message.text))

    def _fake_build_profile_agent(*args, include_history=False, **kwargs):
        return _FakeAgent(include_history=bool(include_history)), object()

    monkeypatch.setattr("chanakya.agent_manager.build_profile_agent", _fake_build_profile_agent)
    app = _build_work_memory_app(tmp_path, monkeypatch)
    client = app.test_client()

    work_id = client.post(
        "/api/works",
        json={"title": "Memory Work", "description": "local"},
    ).get_json()["id"]

    assert (
        client.post(
            "/api/chat",
            json={
                "work_id": work_id,
                "message": "dev: implement login hardening",
                "backend": "local",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/chat",
            json={"work_id": work_id, "message": "dev: refine login hardening", "backend": "local"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/chat",
            json={
                "work_id": work_id,
                "message": "test: validate login hardening",
                "backend": "local",
            },
        ).status_code
        == 200
    )

    history_payload = client.get(f"/api/works/{work_id}/history").get_json()
    histories = history_payload["agent_histories"]
    developer_history = next(item for item in histories if item["agent_id"] == "agent_developer")
    tester_history = next(item for item in histories if item["agent_id"] == "agent_tester")

    developer_messages = [message["content"] for message in developer_history["messages"]]
    tester_messages = [message["content"] for message in tester_history["messages"]]
    assert developer_messages[0] == "dev: implement login hardening"
    assert developer_messages[2] == "dev: refine login hardening"
    assert "dev: implement login hardening" in developer_messages[3]
    assert "dev: refine login hardening" in developer_messages[3]
    assert "test: validate login hardening" not in developer_messages[3]
    assert tester_messages[0] == "test: validate login hardening"
    assert "dev: implement login hardening" not in tester_messages[1]
    assert "dev: refine login hardening" not in tester_messages[1]


def test_work_api_preserves_per_agent_memory_for_a2a_backend(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    class _FakeA2AResponse:
        def __init__(self, text: str) -> None:
            self.text = text
            self.value = text
            self.raw_representation = SimpleNamespace(context_id=None)

    class _FakeA2AAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def create_session(self, *, session_id: str | None = None):
            return SimpleNamespace(session_id=session_id)

        async def run(self, messages, session=None):
            return _FakeA2AResponse(str(messages[0].text))

    fake_module = ModuleType("agent_framework_a2a")
    fake_module.A2AAgent = _FakeA2AAgent
    monkeypatch.setitem(__import__("sys").modules, "agent_framework_a2a", fake_module)
    app = _build_work_memory_app(tmp_path, monkeypatch)
    client = app.test_client()

    work_id = client.post(
        "/api/works",
        json={"title": "Memory Work", "description": "a2a"},
    ).get_json()["id"]

    assert (
        client.post(
            "/api/chat",
            json={
                "work_id": work_id,
                "message": "dev: implement login hardening",
                "backend": "a2a",
                "a2a_url": "http://a2a.test:8000",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/chat",
            json={
                "work_id": work_id,
                "message": "dev: refine login hardening",
                "backend": "a2a",
                "a2a_url": "http://a2a.test:8000",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/chat",
            json={
                "work_id": work_id,
                "message": "test: validate login hardening",
                "backend": "a2a",
                "a2a_url": "http://a2a.test:8000",
            },
        ).status_code
        == 200
    )

    history_payload = client.get(f"/api/works/{work_id}/history").get_json()
    histories = history_payload["agent_histories"]
    developer_history = next(item for item in histories if item["agent_id"] == "agent_developer")
    tester_history = next(item for item in histories if item["agent_id"] == "agent_tester")

    developer_messages = [message["content"] for message in developer_history["messages"]]
    tester_messages = [message["content"] for message in tester_history["messages"]]
    assert developer_messages[0] == "dev: implement login hardening"
    assert developer_messages[2] == "dev: refine login hardening"
    assert "dev: implement login hardening" in developer_messages[3]
    assert "dev: refine login hardening" in developer_messages[3]
    assert "test: validate login hardening" not in developer_messages[3]
    assert tester_messages[0] == "test: validate login hardening"
    assert "dev: implement login hardening" not in tester_messages[1]
    assert "dev: refine login hardening" not in tester_messages[1]
