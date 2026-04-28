from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from chanakya.services.mcp_feedback import build_recovery_payload
from chanakya.services.sandbox_workspace import normalize_work_id, resolve_shared_workspace
from chanakya.services.sandbox_workspace import CLASSIC_ARTIFACT_WORKSPACE_ID

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_CHARS = 20000
CONTAINER_NAME_PREFIX = "chanakya-sandbox-"
SANDBOX_IMAGE = (
    os.getenv("CHANAKYA_SANDBOX_IMAGE")
    or os.getenv("CHANAKYA_SANDBOX_SHELL_IMAGE")
    or os.getenv("CHANAKYA_SANDBOX_PYTHON_IMAGE")
    or "chanakya-sandbox:latest"
)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE_DOCKERFILE = REPO_ROOT / "docker" / "chanakya-sandbox.Dockerfile"

mcp = FastMCP("Chanakya Sandbox Executor", json_response=True)


@dataclass(slots=True)
class RuntimeSelection:
    binary: str
    engine: str


def _select_runtime() -> RuntimeSelection:
    docker_path = shutil.which("docker")
    if docker_path:
        return RuntimeSelection(binary=docker_path, engine="docker")
    podman_path = shutil.which("podman")
    if podman_path:
        return RuntimeSelection(binary=podman_path, engine="podman")
    raise RuntimeError("Neither docker nor podman is installed")


def _bounded_timeout(timeout_seconds: int) -> int:
    if timeout_seconds <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return min(timeout_seconds, MAX_TIMEOUT_SECONDS)


def _trim_output(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + "\n...[truncated]", True


def _annotate_permission_error(text: str, workspace: Path) -> str:
    lowered = text.lower()
    if "permission denied" not in lowered and "read-only file system" not in lowered:
        return text
    hint = (
        "Permission hint: The agent is running inside an isolated container. "
        f"Use /workspace (mapped to {workspace}) for project files and writable output."
    )
    return f"{text}\n\n{hint}" if text else hint


def _container_name(work_id: str | None) -> str:
    return f"{CONTAINER_NAME_PREFIX}{normalize_work_id(work_id)}"


def _work_id_from_container_name(container_name: str) -> str | None:
    if not container_name.startswith(CONTAINER_NAME_PREFIX):
        return None
    suffix = container_name[len(CONTAINER_NAME_PREFIX) :].strip()
    return suffix or None


def _ensure_workspace_writable(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o775)
    probe = workspace / ".sandbox_write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _build_runtime_base_args(
    *,
    runtime: RuntimeSelection,
    workspace: Path,
    container_name: str,
) -> list[str]:
    args = [
        runtime.binary,
        "run",
        "-d",
        "--name",
        container_name,
        "--init",
        "--cpus",
        "1",
        "--memory",
        "512m",
        "--pids-limit",
        "256",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{workspace}:/workspace",
        "-w",
        "/workspace",
        "-e",
        "HOME=/workspace/.home",
    ]
    if runtime.engine == "docker":
        args.extend(["--security-opt", "no-new-privileges", "--cap-drop", "ALL"])
    return args


def _run_runtime_command(
    *,
    command: list[str],
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    effective_timeout = None if timeout_seconds is None else max(1, timeout_seconds)
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=effective_timeout,
        check=False,
    )


def _ensure_default_image(runtime: RuntimeSelection, image: str) -> None:
    inspect_result = _run_runtime_command(
        command=[runtime.binary, "image", "inspect", image],
        timeout_seconds=30,
    )
    if inspect_result.returncode == 0:
        return
    if image != SANDBOX_IMAGE:
        raise RuntimeError(f"Sandbox image not found: {image}")
    if not DEFAULT_IMAGE_DOCKERFILE.exists():
        raise RuntimeError(f"Sandbox Dockerfile not found: {DEFAULT_IMAGE_DOCKERFILE}")
    build_result = _run_runtime_command(
        command=[
            runtime.binary,
            "build",
            "-t",
            image,
            "-f",
            str(DEFAULT_IMAGE_DOCKERFILE),
            str(REPO_ROOT),
        ],
        timeout_seconds=1800,
    )
    if build_result.returncode != 0:
        output = "\n".join(
            part for part in (build_result.stdout.strip(), build_result.stderr.strip()) if part
        )
        raise RuntimeError(output or f"Failed to build sandbox image: {image}")


def _inspect_container_running(runtime: RuntimeSelection, container_name: str) -> bool | None:
    result = _run_runtime_command(
        command=[runtime.binary, "inspect", "-f", "{{.State.Running}}", container_name],
        timeout_seconds=30,
    )
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _remove_container(runtime: RuntimeSelection, container_name: str) -> None:
    _run_runtime_command(
        command=[runtime.binary, "rm", "-f", container_name],
        timeout_seconds=30,
    )


def _list_work_container_names(runtime: RuntimeSelection) -> list[str]:
    result = _run_runtime_command(
        command=[runtime.binary, "ps", "-a", "--format", "{{.Names}}"],
        timeout_seconds=30,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise RuntimeError(output or "Failed to list sandbox containers")
    names = []
    for line in (result.stdout or "").splitlines():
        name = line.strip()
        if name.startswith(CONTAINER_NAME_PREFIX):
            names.append(name)
    return names


def stop_container(work_id: str | None) -> dict[str, object]:
    container_name = _container_name(work_id)
    try:
        runtime = _select_runtime()
    except RuntimeError:
        return {
            "ok": True,
            "found": False,
            "removed": False,
            "container_name": container_name,
            "runtime": None,
        }
    running = _inspect_container_running(runtime, container_name)
    if running is None:
        return {
            "ok": True,
            "found": False,
            "removed": False,
            "container_name": container_name,
            "runtime": runtime.engine,
        }
    result = _run_runtime_command(
        command=[runtime.binary, "rm", "-f", container_name],
        timeout_seconds=30,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return {
        "ok": result.returncode == 0,
        "found": True,
        "removed": result.returncode == 0,
        "container_name": container_name,
        "runtime": runtime.engine,
        "output": output,
        "error": None if result.returncode == 0 else (output or "Failed to remove container"),
    }


def stop_all_work_containers() -> dict[str, object]:
    try:
        runtime = _select_runtime()
    except RuntimeError:
        return {
            "ok": True,
            "runtime": None,
            "stopped": [],
            "failed": [],
        }
    try:
        names = _list_work_container_names(runtime)
    except RuntimeError as exc:
        return {
            "ok": False,
            "runtime": runtime.engine,
            "stopped": [],
            "failed": [{"container_name": None, "error": str(exc)}],
        }
    stopped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for container_name in names:
        result = _run_runtime_command(
            command=[runtime.binary, "rm", "-f", container_name],
            timeout_seconds=30,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        payload = {
            "container_name": container_name,
            "work_id": _work_id_from_container_name(container_name),
            "output": output,
        }
        if result.returncode == 0:
            stopped.append(payload)
        else:
            failed.append({**payload, "error": output or "Failed to remove container"})
    return {
        "ok": not failed,
        "runtime": runtime.engine,
        "stopped": stopped,
        "failed": failed,
    }


def prune_stale_work_containers(
    valid_work_ids: set[str],
    *,
    remove_running: bool = False,
) -> dict[str, object]:
    try:
        runtime = _select_runtime()
    except RuntimeError:
        return {
            "ok": True,
            "runtime": None,
            "removed": [],
            "failed": [],
        }
    try:
        names = _list_work_container_names(runtime)
    except RuntimeError as exc:
        return {
            "ok": False,
            "runtime": runtime.engine,
            "removed": [],
            "failed": [{"container_name": None, "error": str(exc)}],
        }
    removed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for container_name in names:
        work_id = _work_id_from_container_name(container_name)
        if work_id is None:
            continue
        if work_id in valid_work_ids:
            continue
        inspect_result = _run_runtime_command(
            command=[
                runtime.binary,
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
            ],
            timeout_seconds=30,
        )
        inspect_output = "\n".join(
            part for part in (inspect_result.stdout.strip(), inspect_result.stderr.strip()) if part
        )
        inspect_payload = {
            "container_name": container_name,
            "work_id": work_id,
            "output": inspect_output,
        }
        if inspect_result.returncode != 0:
            failed.append(
                {**inspect_payload, "error": inspect_output or "Failed to inspect container state"}
            )
            continue
        is_running = inspect_result.stdout.strip().lower() == "true"
        if is_running and not remove_running:
            continue
        result = _run_runtime_command(
            command=[runtime.binary, "rm", "-f", container_name],
            timeout_seconds=30,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        payload = {
            "container_name": container_name,
            "work_id": work_id,
            "output": output,
        }
        if result.returncode == 0:
            removed.append(payload)
        else:
            failed.append({**payload, "error": output or "Failed to remove container"})
    return {
        "ok": not failed,
        "runtime": runtime.engine,
        "removed": removed,
        "failed": failed,
    }


def _ensure_persistent_container(
    *,
    runtime: RuntimeSelection,
    workspace: Path,
    work_id: str | None,
    image: str,
) -> str:
    _ensure_default_image(runtime, image)
    container_name = _container_name(work_id)
    running = _inspect_container_running(runtime, container_name)
    if running is True:
        return container_name
    if running is False:
        _remove_container(runtime, container_name)
    start_result = _run_runtime_command(
        command=[
            *_build_runtime_base_args(
                runtime=runtime,
                workspace=workspace,
                container_name=container_name,
            ),
            image,
            "sh",
            "-lc",
            "mkdir -p /workspace/.home && trap 'exit 0' TERM INT; while true; do sleep 3600; done",
        ],
        timeout_seconds=60,
    )
    if start_result.returncode != 0:
        output = "\n".join(
            part for part in (start_result.stdout.strip(), start_result.stderr.strip()) if part
        )
        raise RuntimeError(output or f"Failed to start sandbox container: {container_name}")
    return container_name


def _run_in_sandbox(
    *,
    image: str,
    command: list[str],
    work_id: str | None,
    timeout_seconds: int,
) -> dict[str, object]:
    runtime = _select_runtime()
    try:
        workspace = resolve_shared_workspace(work_id, allow_create_missing_classic=False)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return build_recovery_payload(
            error=str(exc),
            hint=f"Retry with a valid existing work_id or use {CLASSIC_ARTIFACT_WORKSPACE_ID} for classic-chat artifact work.",
            exit_code=None,
            output=str(exc),
            truncated=False,
            timed_out=False,
            workspace="",
            runtime=runtime.engine,
            image=image,
        )
    try:
        _ensure_workspace_writable(workspace)
        container_name = _ensure_persistent_container(
            runtime=runtime,
            workspace=workspace,
            work_id=work_id,
            image=image,
        )
    except (PermissionError, RuntimeError) as exc:
        message = _annotate_permission_error(str(exc), workspace)
        return build_recovery_payload(
            error=message,
            hint=(
                "Use files under /workspace and retry. If the container failed to start, "
                "inspect the runtime and image configuration."
            ),
            exit_code=None,
            output=message,
            truncated=False,
            timed_out=False,
            workspace=str(workspace),
            runtime=runtime.engine,
            image=image,
        )
    bounded_timeout = _bounded_timeout(timeout_seconds)
    cmd = [runtime.binary, "exec", "-w", "/workspace", container_name, *command]
    try:
        result = _run_runtime_command(
            command=cmd,
            timeout_seconds=bounded_timeout + 5,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or "Execution timed out"
        merged = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        merged = _annotate_permission_error(merged, workspace)
        trimmed, truncated = _trim_output(merged)
        return build_recovery_payload(
            error=trimmed,
            hint="Retry with simpler code, a longer timeout_seconds, or a background process.",
            exit_code=None,
            output=trimmed,
            truncated=truncated,
            timed_out=True,
            workspace=str(workspace),
            runtime=runtime.engine,
            image=image,
        )

    merged_output = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    merged_output = _annotate_permission_error(merged_output, workspace)
    trimmed_output, truncated = _trim_output(merged_output)
    timed_out = result.returncode == 124
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "output": trimmed_output,
        "truncated": truncated,
        "timed_out": timed_out,
        "workspace": str(workspace),
        "runtime": runtime.engine,
        "image": image,
        "container_name": container_name,
        "cwd": "/workspace",
    }


@mcp.tool()
def execute_python(
    code: str,
    work_id: str = CLASSIC_ARTIFACT_WORKSPACE_ID,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    filename: str = "snippet.py",
) -> dict[str, object]:
    """Execute Python code inside the persistent per-work sandbox container."""
    safe_name = Path(filename).name or "snippet.py"
    try:
        workspace = resolve_shared_workspace(work_id, allow_create_missing_classic=False)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        runtime = _select_runtime()
        return build_recovery_payload(
            error=str(exc),
            hint=f"Retry with a valid existing work_id or use {CLASSIC_ARTIFACT_WORKSPACE_ID} for classic-chat artifact work.",
            exit_code=None,
            output=str(exc),
            truncated=False,
            timed_out=False,
            workspace="",
            runtime=runtime.engine,
            image=SANDBOX_IMAGE,
        )
    script_path = workspace / safe_name
    with tempfile.NamedTemporaryFile("w", delete=False, dir=workspace, suffix=".py") as handle:
        handle.write(code)
        temp_name = Path(handle.name).name
    temp_path = workspace / temp_name
    temp_path.rename(script_path)
    script_path.chmod(0o644)
    return _run_in_sandbox(
        image=SANDBOX_IMAGE,
        command=["python", safe_name],
        work_id=work_id,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def execute_shell(
    command: str,
    work_id: str = CLASSIC_ARTIFACT_WORKSPACE_ID,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute any shell command inside the persistent per-work sandbox container."""
    bounded_timeout = _bounded_timeout(timeout_seconds)
    quoted_command = shlex.quote(command)
    return _run_in_sandbox(
        image=SANDBOX_IMAGE,
        command=[
            "sh",
            "-lc",
            f"timeout {bounded_timeout}s sh -lc {quoted_command}",
        ],
        work_id=work_id,
        timeout_seconds=timeout_seconds,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
