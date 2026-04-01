from __future__ import annotations

import json

import chanakya.app as app_module
from chanakya.app import create_app
from chanakya.services import tool_loader


class _ManagerStub:
    def should_delegate(self, message: str) -> bool:
        return False


def _build_test_app(tmp_path, monkeypatch):
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
                    "heartbeat_file_path": None,
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
                    "heartbeat_file_path": None,
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


def test_agent_create_and_update_api_persists_configuration(tmp_path, monkeypatch) -> None:
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
            "heartbeat_file_path": "chanakya_data/heartbeats/developer-alpha.md",
            "is_active": True,
        },
    )

    assert create_response.status_code == 201
    created = create_response.get_json()
    assert created["role"] == "developer"
    assert created["workspace"] == "alpha-workspace"
    assert created["heartbeat_enabled"] is True

    heartbeat_file = tmp_path / "chanakya_data/heartbeats/developer-alpha.md"
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
            "heartbeat_file_path": "chanakya_data/heartbeats/developer-alpha.md",
            "is_active": False,
        },
    )

    assert update_response.status_code == 200
    updated = update_response.get_json()
    assert updated["system_prompt"] == "You are the updated implementation agent."
    assert updated["workspace"] == "updated-workspace"
    assert updated["heartbeat_enabled"] is False
    assert updated["is_active"] is False


def test_agent_create_api_rejects_invalid_payload(tmp_path, monkeypatch) -> None:
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


def test_tools_availability_api_returns_payload(tmp_path, monkeypatch) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get("/api/tools/availability")

    assert response.status_code == 200
    assert "tools" in response.get_json()
