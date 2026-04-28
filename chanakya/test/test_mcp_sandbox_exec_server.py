from __future__ import annotations

import subprocess
from pathlib import Path

import chanakya.services.mcp_sandbox_exec_server as sandbox_exec


def test_permission_error_annotation_includes_workspace_hint(tmp_path: Path) -> None:
    text = sandbox_exec._annotate_permission_error(
        "Permission denied: cannot write file",
        tmp_path,
    )

    assert "isolated container" in text
    assert str(tmp_path) in text


def test_runtime_args_mount_only_workspace() -> None:
    workspace = Path("/tmp/workspace")

    args = sandbox_exec._build_runtime_base_args(
        runtime=sandbox_exec.RuntimeSelection(binary="docker", engine="docker"),
        workspace=workspace,
        container_name="chanakya-sandbox-temp",
    )

    assert "--name" in args
    assert "chanakya-sandbox-temp" in args
    assert "--init" in args
    assert f"{workspace}:/workspace" in args
    assert "HOME=/workspace/.home" in args
    assert not any(arg.endswith(":ro") for arg in args)


def test_workspace_probe_creates_and_removes_file(tmp_path: Path) -> None:
    sandbox_exec._ensure_workspace_writable(tmp_path)

    assert tmp_path.exists()
    assert not (tmp_path / ".sandbox_write_probe").exists()


def test_run_in_sandbox_invalid_work_id_returns_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_exec,
        "_select_runtime",
        lambda: sandbox_exec.RuntimeSelection(binary="docker", engine="docker"),
    )

    result = sandbox_exec._run_in_sandbox(
        image=sandbox_exec.SANDBOX_IMAGE,
        command=["python", "snippet.py"],
        work_id="cwork_missing",
        timeout_seconds=30,
    )

    assert result["ok"] is False
    assert "valid existing work_id" in str(result["hint"])


def test_ensure_persistent_container_reuses_running_container(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox_exec, "_ensure_default_image", lambda runtime, image: None)
    monkeypatch.setattr(sandbox_exec, "_inspect_container_running", lambda runtime, name: True)

    container_name = sandbox_exec._ensure_persistent_container(
        runtime=sandbox_exec.RuntimeSelection(binary="docker", engine="docker"),
        workspace=tmp_path,
        work_id="temp",
        image=sandbox_exec.SANDBOX_IMAGE,
    )

    assert container_name == "chanakya-sandbox-temp"


def test_execute_shell_wraps_command_with_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_in_sandbox(
        *,
        image: str,
        command: list[str],
        work_id: str | None,
        timeout_seconds: int,
    ):
        captured["image"] = image
        captured["command"] = command
        captured["work_id"] = work_id
        captured["timeout_seconds"] = timeout_seconds
        return {"ok": True}

    monkeypatch.setattr(sandbox_exec, "_run_in_sandbox", fake_run_in_sandbox)

    result = sandbox_exec.execute_shell("npm test", work_id="temp", timeout_seconds=12)

    assert result == {"ok": True}
    assert captured["image"] == sandbox_exec.SANDBOX_IMAGE
    assert captured["command"] == ["sh", "-lc", "timeout 12s sh -lc 'npm test'"]


def test_run_in_sandbox_reports_container_name_and_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sandbox_exec,
        "_select_runtime",
        lambda: sandbox_exec.RuntimeSelection(binary="docker", engine="docker"),
    )
    monkeypatch.setattr(
        sandbox_exec,
        "resolve_shared_workspace",
        lambda work_id, allow_create_missing_classic=False: tmp_path,
    )
    monkeypatch.setattr(sandbox_exec, "_ensure_workspace_writable", lambda workspace: None)
    monkeypatch.setattr(
        sandbox_exec,
        "_ensure_persistent_container",
        lambda runtime, workspace, work_id, image: "chanakya-sandbox-temp",
    )
    monkeypatch.setattr(
        sandbox_exec,
        "_run_runtime_command",
        lambda command, timeout_seconds=None: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="hello\n",
            stderr="",
        ),
    )

    result = sandbox_exec._run_in_sandbox(
        image=sandbox_exec.SANDBOX_IMAGE,
        command=["python", "snippet.py"],
        work_id="temp",
        timeout_seconds=30,
    )

    assert result["ok"] is True
    assert result["container_name"] == "chanakya-sandbox-temp"
    assert result["cwd"] == "/workspace"
