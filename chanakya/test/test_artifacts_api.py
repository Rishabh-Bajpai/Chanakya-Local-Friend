from __future__ import annotations

from pathlib import Path

from chanakya import app as app_module
from chanakya.services import tool_loader
from chanakya.services.sandbox_workspace import delete_shared_workspace, resolve_shared_workspace


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


def test_artifact_list_and_download_endpoints(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = tmp_path / "artifacts.db"

    monkeypatch.setattr(app_module, "load_local_env", lambda: None)
    monkeypatch.setattr(app_module, "get_data_dir", lambda: data_dir)
    monkeypatch.setattr(app_module, "get_database_url", lambda: f"sqlite:///{database_path}")
    monkeypatch.setattr(tool_loader, "initialize_all_tools", lambda: None)
    monkeypatch.setattr(app_module, "MAFRuntime", _RuntimeStub)
    monkeypatch.setattr(app_module, "AgentManager", _ManagerStub)
    monkeypatch.setattr(app_module, "NtfyNotificationDispatcher", _NotificationStub)

    app = app_module.create_app()
    store = app.extensions["chanakya_store"]

    request_id = "req_artifact_api"
    session_id = "session_artifact_api"
    workspace = resolve_shared_workspace(request_id, create=True)
    artifact_file = workspace / "palindrome.py"
    artifact_file.write_text("print('palindrome')\n", encoding="utf-8")
    store.ensure_session(session_id, title="Artifact API")
    artifact = store.create_artifact(
        artifact_id="artifact_test",
        request_id=request_id,
        session_id=session_id,
        work_id=None,
        name="palindrome.py",
        path="palindrome.py",
        mime_type="text/x-python",
        kind="code",
        size_bytes=artifact_file.stat().st_size,
        source_agent_id="agent_chanakya",
        source_agent_name="Chanakya",
    )

    try:
        client = app.test_client()

        list_response = client.get(f"/api/requests/{request_id}/artifacts")
        assert list_response.status_code == 200
        list_payload = list_response.get_json()
        assert list_payload["artifacts"][0]["id"] == artifact["id"]

        detail_response = client.get(f"/api/artifacts/{artifact['id']}")
        assert detail_response.status_code == 200
        detail_payload = detail_response.get_json()
        assert detail_payload["name"] == "palindrome.py"

        download_response = client.get(f"/api/artifacts/{artifact['id']}/download")
        assert download_response.status_code == 200
        assert download_response.data == b"print('palindrome')\n"
        disposition = str(download_response.headers.get("Content-Disposition") or "")
        assert "palindrome.py" in disposition
    finally:
        delete_shared_workspace(request_id)
