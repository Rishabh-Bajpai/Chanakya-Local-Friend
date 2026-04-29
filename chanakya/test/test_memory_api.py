from __future__ import annotations

from pathlib import Path

from chanakya import app as app_module
from chanakya.services import tool_loader


class _RuntimeStub:
    def __init__(self, *args, **kwargs) -> None:
        self.profile = type("Profile", (), {"name": "Chanakya"})()

    def clear_session_state(self, session_id: str) -> None:
        return None


class _ManagerStub:
    def __init__(self, *args, **kwargs) -> None:
        return None


class _NotificationStub:
    def __init__(self, *args, **kwargs) -> None:
        return None


def test_memory_debug_endpoints(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = tmp_path / "memory.db"

    monkeypatch.setattr(app_module, "load_local_env", lambda: None)
    monkeypatch.setattr(app_module, "get_data_dir", lambda: data_dir)
    monkeypatch.setattr(app_module, "get_database_url", lambda: f"sqlite:///{database_path}")
    monkeypatch.setattr(tool_loader, "initialize_all_tools", lambda: None)
    monkeypatch.setattr(app_module, "MAFRuntime", _RuntimeStub)
    monkeypatch.setattr(app_module, "AgentManager", _ManagerStub)
    monkeypatch.setattr(app_module, "NtfyNotificationDispatcher", _NotificationStub)

    app = app_module.create_app()
    store = app.extensions["chanakya_store"]
    store.ensure_session("session_memory_api", title="Memory API")
    store.create_memory(
        memory_id="memory_1",
        owner_id="default_user",
        session_id="session_memory_api",
        scope="shared",
        type="project",
        subject="project context",
        content="User app uses Microsoft Agent Framework with MCP tools.",
        importance=4,
        confidence=0.9,
    )
    store.create_memory_event(
        owner_id="default_user",
        session_id="session_memory_api",
        request_id="req_memory_api",
        memory_id="memory_1",
        event_type="memory_added",
        payload={"subject": "project context"},
    )
    store.create_memory_event(
        owner_id="default_user",
        session_id="session_memory_api",
        request_id="req_memory_api",
        event_type="memory_retrieved",
        payload={"memory_ids": ["memory_1"], "count": 1},
    )

    client = app.test_client()

    memory_response = client.get("/api/memory?session_id=session_memory_api")
    assert memory_response.status_code == 200
    memory_payload = memory_response.get_json()
    assert memory_payload["count"] == 1
    assert memory_payload["counts_by_type"]["project"] == 1
    assert memory_payload["memories"][0]["id"] == "memory_1"

    events_response = client.get("/api/memory/events?session_id=session_memory_api")
    assert events_response.status_code == 200
    events_payload = events_response.get_json()
    assert events_payload["count"] == 2
    assert events_payload["counts_by_type"]["memory_added"] == 1
    assert events_payload["counts_by_type"]["memory_retrieved"] == 1
    assert events_payload["events"][0]["memory_id"] == "memory_1"

    session_response = client.get("/api/sessions/session_memory_api/memory")
    assert session_response.status_code == 200
    session_payload = session_response.get_json()
    assert session_payload["memory_count"] == 1
    assert session_payload["event_count"] == 2
    assert session_payload["memories"][0]["subject"] == "project context"
    assert session_payload["counts_by_type"]["project"] == 1
    assert session_payload["event_counts_by_type"]["memory_retrieved"] == 1
    assert session_payload["latest_retrieval"]["event_type"] == "memory_retrieved"
