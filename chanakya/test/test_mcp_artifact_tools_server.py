from __future__ import annotations

from pathlib import Path

from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.services import sandbox_workspace
from chanakya.services.mcp_artifact_tools_server import (
    _create_artifact,
    _delete_artifact,
    _locate_artifact,
    _update_artifact,
)
from chanakya.store import ChanakyaStore


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    return ChanakyaStore(build_session_factory(engine))


def test_create_artifact_writes_file_and_registers_record(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    store = _build_store()
    store.ensure_session("session_artifact_tool", title="Artifact Tool")

    result = _create_artifact(
        store,
        session_id="session_artifact_tool",
        request_id="req_artifact_tool",
        name="hello.py",
        content="print('hello')\n",
        kind="code",
        title="Hello Script",
        summary="Small Python script",
        source_agent_id="agent_chanakya",
        source_agent_name="Chanakya",
    )

    assert result["ok"] is True
    artifact = result["artifact"]
    assert artifact["name"] == "hello.py"
    assert artifact["title"] == "Hello Script"
    assert artifact["summary"] == "Small Python script"
    assert artifact["kind"] == "code"
    artifact_root = sandbox_workspace.get_artifact_storage_root(create=False)
    artifact_file = artifact_root / artifact["path"]
    assert artifact_file.read_text(encoding="utf-8") == "print('hello')\n"


def test_update_artifact_rewrites_content_and_tracks_latest_request(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    store = _build_store()
    store.ensure_session("session_artifact_tool", title="Artifact Tool")

    created = _create_artifact(
        store,
        session_id="session_artifact_tool",
        request_id="req_first",
        name="draft.txt",
        content="alpha\n",
        kind="text",
        title="Draft",
    )
    artifact_id = str(created["artifact"]["id"])

    updated = _update_artifact(
        store,
        artifact_id=artifact_id,
        content="beta\n",
        request_id="req_second",
        name="final.txt",
        title="Final Draft",
        summary="Updated content",
    )

    assert updated["ok"] is True
    artifact = updated["artifact"]
    assert artifact["name"] == "final.txt"
    assert artifact["title"] == "Final Draft"
    assert artifact["summary"] == "Updated content"
    assert artifact["latest_request_id"] == "req_second"
    listed = store.list_artifacts_for_request("req_second")
    assert [item["id"] for item in listed] == [artifact_id]
    artifact_root = sandbox_workspace.get_artifact_storage_root(create=False)
    artifact_file = artifact_root / artifact["path"]
    assert artifact_file.read_text(encoding="utf-8") == "beta\n"


def test_wrong_artifact_id_returns_constructive_feedback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    store = _build_store()
    store.ensure_session("session_artifact_tool", title="Artifact Tool")

    created = _create_artifact(
        store,
        session_id="session_artifact_tool",
        request_id="req_scope",
        name="draft.txt",
        content="alpha\n",
        kind="text",
        title="Draft",
    )

    result = _update_artifact(
        store,
        artifact_id="artifact_missing",
        content="beta\n",
        session_id="session_artifact_tool",
    )

    assert result["ok"] is False
    assert "Wrong artifact ID" in result["error"]
    assert result["available_artifacts"][0]["id"] == created["artifact"]["id"]
    assert "list_artifacts" in result["hint"]


def test_locate_and_delete_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    store = _build_store()
    store.ensure_session("session_artifact_tool", title="Artifact Tool")

    created = _create_artifact(
        store,
        session_id="session_artifact_tool",
        request_id="req_scope",
        name="draft.txt",
        content="alpha\n",
        kind="text",
        title="Draft",
    )
    artifact_id = str(created["artifact"]["id"])

    located = _locate_artifact(store, artifact_id=artifact_id)
    assert located["ok"] is True
    assert located["artifact"]["download_url"].endswith("/download")
    assert located["absolute_path"].endswith(f"/{created['artifact']['path']}")

    deleted = _delete_artifact(store, artifact_id=artifact_id)
    assert deleted["ok"] is True
    assert store.list_artifacts_for_request("req_scope") == []
