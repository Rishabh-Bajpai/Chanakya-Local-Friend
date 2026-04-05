from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from chanakya.services.sandbox_workspace import resolve_shared_workspace

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 20000
PYTHON_IMAGE = os.getenv("CHANAKYA_SANDBOX_PYTHON_IMAGE", "python:3.11-alpine")
SHELL_IMAGE = os.getenv("CHANAKYA_SANDBOX_SHELL_IMAGE", "alpine:3.20")

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


def _build_runtime_base_args(
    *,
    runtime: RuntimeSelection,
    workspace: Path,
    timeout_seconds: int,
) -> list[str]:
    args = [
        runtime.binary,
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "1",
        "--memory",
        "512m",
        "--pids-limit",
        "256",
        "-v",
        f"{workspace}:/workspace",
        "-w",
        "/workspace",
        "-e",
        f"CHANAKYA_TIMEOUT_SECONDS={timeout_seconds}",
    ]
    if runtime.engine == "docker":
        args.extend(["--security-opt", "no-new-privileges", "--cap-drop", "ALL"])
    return args


def _run_in_sandbox(
    *,
    image: str,
    command: list[str],
    work_id: str | None,
    timeout_seconds: int,
) -> dict[str, object]:
    runtime = _select_runtime()
    workspace = resolve_shared_workspace(work_id)
    bounded_timeout = _bounded_timeout(timeout_seconds)
    cmd = [
        *_build_runtime_base_args(
            runtime=runtime,
            workspace=workspace,
            timeout_seconds=bounded_timeout,
        ),
        image,
        *command,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=bounded_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or "Execution timed out"
        merged = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        trimmed, truncated = _trim_output(merged)
        return {
            "ok": False,
            "exit_code": None,
            "output": trimmed,
            "truncated": truncated,
            "timed_out": True,
            "workspace": str(workspace),
            "runtime": runtime.engine,
            "image": image,
        }

    merged_output = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    trimmed_output, truncated = _trim_output(merged_output)
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "output": trimmed_output,
        "truncated": truncated,
        "timed_out": False,
        "workspace": str(workspace),
        "runtime": runtime.engine,
        "image": image,
    }


@mcp.tool()
def execute_python(
    code: str,
    work_id: str = "temp",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    filename: str = "snippet.py",
) -> dict[str, object]:
    """Execute Python code only inside an isolated shared sandbox workspace."""
    safe_name = Path(filename).name or "snippet.py"
    workspace = resolve_shared_workspace(work_id)
    script_path = workspace / safe_name
    with tempfile.NamedTemporaryFile("w", delete=False, dir=workspace, suffix=".py") as handle:
        handle.write(code)
        temp_name = Path(handle.name).name
    temp_path = workspace / temp_name
    temp_path.rename(script_path)
    script_path.chmod(0o644)
    return _run_in_sandbox(
        image=PYTHON_IMAGE,
        command=["python", safe_name],
        work_id=work_id,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
def execute_shell(
    command: str,
    work_id: str = "temp",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute a shell command only inside an isolated shared sandbox workspace."""
    return _run_in_sandbox(
        image=SHELL_IMAGE,
        command=["sh", "-lc", command],
        work_id=work_id,
        timeout_seconds=timeout_seconds,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
