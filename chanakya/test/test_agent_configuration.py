from __future__ import annotations

import json
from pathlib import Path

import chanakya.app as app_module
from flask import Flask
from pytest import MonkeyPatch
from chanakya.app import create_app
from chanakya.db import build_engine, build_session_factory
from chanakya.domain import TASK_STATUS_DONE, now_iso
from chanakya.model import ChatMessageModel, TemporaryAgentModel, WorkAgentSessionModel
from chanakya.services import tool_loader
from chanakya.store import ChanakyaStore


class _ManagerStub:
    def should_delegate(self, message: str) -> bool:
        return False


def _build_test_app(tmp_path: Path, monkeypatch: MonkeyPatch) -> Flask:
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
    monkeypatch.setattr(app_module, "MAFRuntime", lambda profile, session_factory: object())
    monkeypatch.setattr(
        app_module, "AgentManager", lambda store, session_factory, manager_profile: _ManagerStub()
    )
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
