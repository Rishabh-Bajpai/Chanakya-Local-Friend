from __future__ import annotations

from pathlib import Path

import pytest

import chanakya.services.sandbox_workspace as sandbox_workspace


def test_resolve_shared_workspace_uses_artifacts_for_empty_work_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    workspace = sandbox_workspace.resolve_shared_workspace(None)

    assert workspace == (tmp_path / "shared_workspace" / "artifacts")
    assert workspace.exists()
    assert oct(workspace.stat().st_mode & 0o777) == "0o775"


def test_delete_shared_workspace_removes_work_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    workspace = sandbox_workspace.resolve_shared_workspace("cwork_123")
    (workspace / "artifact.txt").write_text("hello", encoding="utf-8")

    result = sandbox_workspace.delete_shared_workspace("cwork_123")

    assert result == {
        "ok": True,
        "work_id": "cwork_123",
        "path": str(workspace),
        "deleted": True,
    }
    assert not workspace.exists()


def test_delete_shared_workspace_returns_failure_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    events: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        sandbox_workspace,
        "debug_log",
        lambda label, payload=None: events.append((label, payload or {})),
    )
    workspace = sandbox_workspace.resolve_shared_workspace("cwork_123")

    def _raise_rmtree(_: Path) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(sandbox_workspace.shutil, "rmtree", _raise_rmtree)

    result = sandbox_workspace.delete_shared_workspace("cwork_123")

    assert result == {
        "ok": False,
        "work_id": "cwork_123",
        "path": str(workspace),
        "error": "permission denied",
    }
    assert events[-1] == (
        "sandbox_workspace_delete_failed",
        {
            "work_id": "cwork_123",
            "path": str(workspace),
            "error_type": "OSError",
            "error": "permission denied",
        },
    )


def test_resolve_shared_workspace_can_skip_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    workspace = sandbox_workspace.resolve_shared_workspace("cwork_123", create=False)

    assert workspace == (tmp_path / "shared_workspace" / "cwork_123")
    assert not workspace.exists()


def test_resolve_shared_workspace_rejects_unknown_classic_workspace_when_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    with pytest.raises(FileNotFoundError):
        sandbox_workspace.resolve_shared_workspace(
            "cwork_123",
            allow_create_missing_classic=False,
        )


def test_resolve_shared_workspace_allows_non_classic_workspace_when_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    workspace = sandbox_workspace.resolve_shared_workspace(
        "work_123",
        allow_create_missing_classic=False,
    )

    assert workspace.exists()


def test_resolve_shared_workspace_logs_only_on_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    events: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        sandbox_workspace,
        "debug_log",
        lambda label, payload=None: events.append((label, payload or {})),
    )

    workspace = sandbox_workspace.resolve_shared_workspace("cwork_123")
    sandbox_workspace.resolve_shared_workspace("cwork_123")
    sandbox_workspace.resolve_shared_workspace("cwork_456", create=False)

    assert workspace.exists()
    assert events == [
        (
            "sandbox_workspace_created",
            {
                "work_id": "cwork_123",
                "path": str(workspace),
            },
        )
    ]


@pytest.mark.parametrize("work_id", ["../escape", "..", "bad/value", "bad\\value"])
def test_normalize_work_id_rejects_invalid_values(work_id: str) -> None:
    with pytest.raises(ValueError):
        sandbox_workspace.normalize_work_id(work_id)
