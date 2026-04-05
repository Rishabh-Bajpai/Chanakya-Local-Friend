from __future__ import annotations

from pathlib import Path

import chanakya.services.mcp_sandbox_exec_server as sandbox_exec


def test_permission_error_annotation_includes_workspace_hint(tmp_path: Path) -> None:
    text = sandbox_exec._annotate_permission_error(
        "Permission denied: cannot write file",
        tmp_path,
    )

    assert "Host files are mounted read-only" in text
    assert str(tmp_path) in text


def test_runtime_args_include_read_only_host_mounts(monkeypatch) -> None:
    workspace = Path("/tmp/workspace")
    monkeypatch.setattr(
        sandbox_exec, "_get_host_read_mounts", lambda: [(Path("/repo"), "/host/repo")]
    )

    args = sandbox_exec._build_runtime_base_args(
        runtime=sandbox_exec.RuntimeSelection(binary="docker", engine="docker"),
        workspace=workspace,
        timeout_seconds=30,
    )

    assert "--user" in args
    assert f"{workspace}:/workspace" in args
    assert "/repo:/host/repo:ro" in args


def test_workspace_probe_creates_and_removes_file(tmp_path: Path) -> None:
    sandbox_exec._ensure_workspace_writable(tmp_path)

    assert tmp_path.exists()
    assert not (tmp_path / ".sandbox_write_probe").exists()
