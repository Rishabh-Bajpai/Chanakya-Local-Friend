from __future__ import annotations

import json
from pathlib import Path

from flask import Flask
from pytest import MonkeyPatch

import chanakya.app as app_module
from chanakya.agent.runtime import RunResult
from chanakya.app import create_app
from chanakya.services import tool_loader
from chanakya.services.ntfy import NtfyPublishResult


class _RuntimeStub:
    def __init__(self, profile) -> None:
        self.profile = profile

    def runtime_metadata(self, model_id: str | None = None) -> dict[str, str | None]:
        return {
            "model": model_id or "test-model",
            "endpoint": "http://test-endpoint",
        }

    def run(
        self,
        session_id: str,
        message: str,
        request_id: str | None = None,
        model_id: str | None = None,
    ):
        return RunResult(
            text="Finished the task successfully with the requested summary.",
            tool_traces=[],
            availability=[],
            response_mode="tool_assisted",
        )


def _seed_agents(seed_dir: Path) -> None:
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


def _build_test_app(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    runtime_factory=None,
) -> Flask:
    _seed_agents(tmp_path / "chanakya" / "seeds")
    database_path = tmp_path / "chanakya-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setattr(app_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tool_loader, "initialize_all_tools", lambda: None)
    monkeypatch.setattr(app_module, "get_tools_availability", lambda: [])
    monkeypatch.setattr(tool_loader, "get_tools_availability", lambda: [])
    monkeypatch.setattr(
        app_module,
        "MAFRuntime",
        runtime_factory or (lambda profile, session_factory: _RuntimeStub(profile)),
    )
    monkeypatch.setattr(
        app_module, "AgentManager", lambda store, session_factory, manager_profile: None
    )
    return create_app()


def test_ntfy_settings_api_persists_configuration(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    get_response = client.get("/api/notifications/ntfy")
    assert get_response.status_code == 200
    assert get_response.get_json()["enabled"] is False

    put_response = client.put(
        "/api/notifications/ntfy",
        json={
            "enabled": True,
            "include_message_preview": True,
            "server_url": "https://ntfy.sh",
            "topic": "chanakya-123abc456def",
        },
    )
    assert put_response.status_code == 200
    payload = put_response.get_json()
    assert payload["enabled"] is True
    assert payload["topic"] == "chanakya-123abc456def"
    assert payload["deep_link"] == "ntfy://ntfy.sh/chanakya-123abc456def"

    get_updated = client.get("/api/notifications/ntfy")
    assert get_updated.status_code == 200
    assert get_updated.get_json()["topic"] == "chanakya-123abc456def"


def test_ntfy_settings_api_delete_clears_saved_topic(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.put(
        "/api/notifications/ntfy",
        json={
            "enabled": True,
            "include_message_preview": True,
            "server_url": "https://ntfy.sh",
            "topic": "chanakya-123abc456def",
        },
    )

    response = client.delete("/api/notifications/ntfy")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["enabled"] is False
    assert payload["topic"] == ""
    assert payload["deep_link"] == ""


def test_ntfy_settings_api_rejects_invalid_topic(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.put(
        "/api/notifications/ntfy",
        json={
            "enabled": True,
            "include_message_preview": True,
            "server_url": "https://ntfy.sh",
            "topic": "bad topic",
        },
    )

    assert response.status_code == 400
    assert "topic" in response.get_json()["error"]


def test_ntfy_test_endpoint_publishes_message(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    published: list[dict[str, str]] = []

    def _publish(
        self,
        *,
        server_url: str,
        topic: str,
        message: str,
        title: str,
        priority: str,
        tags: list[str],
        click_url=None,
    ):
        published.append(
            {
                "server_url": server_url,
                "topic": topic,
                "message": message,
                "title": title,
                "priority": priority,
            }
        )
        return NtfyPublishResult(ok=True, status=200, body="ok")

    monkeypatch.setattr("chanakya.services.ntfy.NtfyClient.publish", _publish)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.put(
        "/api/notifications/ntfy",
        json={
            "enabled": True,
            "include_message_preview": True,
            "server_url": "https://ntfy.sh",
            "topic": "chanakya-123abc456def",
        },
    )

    response = client.post("/api/notifications/ntfy/test")

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0]["topic"] == "chanakya-123abc456def"
    assert "working" in published[0]["message"]


def test_ntfy_qr_endpoint_renders_local_svg(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get(
        "/api/notifications/ntfy/qr.svg?server_url=https://ntfy.sh&topic=chanakya-123abc456def"
    )

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    body = response.get_data(as_text=True)
    assert "<svg" in body
    assert "qrserver" not in body


def test_chat_request_sends_ntfy_notification_with_summary(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    published: list[dict[str, str]] = []

    def _publish(
        self,
        *,
        server_url: str,
        topic: str,
        message: str,
        title: str,
        priority: str,
        tags: list[str],
        click_url=None,
    ):
        published.append(
            {
                "server_url": server_url,
                "topic": topic,
                "message": message,
                "title": title,
                "priority": priority,
            }
        )
        return NtfyPublishResult(ok=True, status=200, body="ok")

    monkeypatch.setattr("chanakya.services.ntfy.NtfyClient.publish", _publish)
    app = _build_test_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.put(
        "/api/notifications/ntfy",
        json={
            "enabled": True,
            "include_message_preview": True,
            "server_url": "https://ntfy.sh",
            "topic": "chanakya-123abc456def",
        },
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "please finish this task",
        },
    )

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0]["title"] == "Chanakya: Request completed"
    assert "Finished the task successfully" in published[0]["message"]
