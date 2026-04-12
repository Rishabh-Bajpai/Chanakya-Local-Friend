from __future__ import annotations

from pathlib import Path

import chanakya.services.mcp_basic_tools_server as basic_tools


def test_resolve_path_stays_under_classic_chat_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(basic_tools, "get_data_dir", lambda: tmp_path)

    workspace_root = basic_tools.get_classic_chat_workspace_root()
    resolved = basic_tools._resolve_path("outputs/result.txt")

    assert workspace_root == tmp_path / "classic_chat_workspace"
    assert resolved == workspace_root / "outputs" / "result.txt"
    assert workspace_root.exists()


def test_resolve_path_rejects_escape_from_classic_chat_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(basic_tools, "get_data_dir", lambda: tmp_path)

    try:
        basic_tools._resolve_path("../outside.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected path escape to be rejected")


def test_run_git_reports_classic_chat_workspace_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(basic_tools, "get_data_dir", lambda: tmp_path)

    result = basic_tools._run_git(["status", "--short"])

    assert result["workspace_root"] == str(tmp_path / "classic_chat_workspace")
