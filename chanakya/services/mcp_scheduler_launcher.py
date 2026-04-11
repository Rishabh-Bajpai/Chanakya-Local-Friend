from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from filelock import FileLock


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "chanakya_data" / "external_tools" / "scheduler_mcp"
CHECKOUT_DIR = DATA_ROOT / "checkout"
VENV_DIR = DATA_ROOT / ".venv"
LOCK_PATH = DATA_ROOT / ".bootstrap.lock"
UPSTREAM_REPO = "https://github.com/PhialsBasement/scheduler-mcp.git"
UPSTREAM_COMMIT = os.getenv(
    "CHANAKYA_SCHEDULER_MCP_COMMIT",
    "5a2015a6cd9ebbef0feb1a382e762b2c5783a904",
)
SERVER_FILE = CHECKOUT_DIR / "mcp_scheduler" / "server.py"
SCHEDULER_FILE = CHECKOUT_DIR / "mcp_scheduler" / "scheduler.py"
EXECUTOR_FILE = CHECKOUT_DIR / "mcp_scheduler" / "executor.py"
REVISION_FILE = DATA_ROOT / ".upstream_commit"


def _python_in_venv() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def _read_bootstrapped_revision() -> str:
    if not REVISION_FILE.exists():
        return ""
    return REVISION_FILE.read_text(encoding="utf-8").strip()


def _write_bootstrapped_revision(commit: str) -> None:
    REVISION_FILE.write_text(f"{commit}\n", encoding="utf-8")


def _ensure_checkout_at_pinned_commit() -> bool:
    checkout_existed = CHECKOUT_DIR.exists()
    if not checkout_existed:
        print("Bootstrapping scheduler-mcp checkout...", file=sys.stderr)
        _run(["git", "clone", "--depth", "1", UPSTREAM_REPO, str(CHECKOUT_DIR)])
    _run(["git", "fetch", "--depth", "1", "origin", UPSTREAM_COMMIT], cwd=CHECKOUT_DIR)
    _run(["git", "checkout", "--detach", UPSTREAM_COMMIT], cwd=CHECKOUT_DIR)
    return (not checkout_existed) or _read_bootstrapped_revision() != UPSTREAM_COMMIT


def _patch_fastmcp_compatibility() -> None:
    if not SERVER_FILE.exists():
        return
    original = SERVER_FILE.read_text(encoding="utf-8")
    patched = "\n".join(
        line for line in original.splitlines() if "version=config.server_version" not in line
    )
    if original.endswith("\n"):
        patched = f"{patched}\n"
    if patched != original:
        SERVER_FILE.write_text(patched, encoding="utf-8")


def _patch_relative_schedule_support() -> None:
    if not SCHEDULER_FILE.exists():
        return
    original = SCHEDULER_FILE.read_text(encoding="utf-8")
    patched = original
    import_block = "import asyncio\nimport logging\nfrom datetime import datetime\nfrom typing import Dict, List, Optional\n"
    replacement_import_block = (
        "import asyncio\n"
        "import logging\n"
        "import re\n"
        "from datetime import datetime, timedelta\n"
        "from typing import Dict, List, Optional\n"
    )
    if import_block in patched and "from datetime import datetime, timedelta" not in patched:
        patched = patched.replace(import_block, replacement_import_block, 1)
    marker = "logger = logging.getLogger(__name__)\n\n\nclass Scheduler:"
    old_helper = """logger = logging.getLogger(__name__)\n\n\ndef _normalize_relative_schedule(task: Task, now: datetime) -> None:\n    match = re.fullmatch(r"P(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)", task.schedule.strip().upper())\n    if not match:\n        return\n    hours = int(match.group(1) or 0)\n    minutes = int(match.group(2) or 0)\n    seconds = int(match.group(3) or 0)\n    if hours == 0 and minutes == 0 and seconds == 0:\n        raise ValueError("Relative reminder duration must be greater than zero")\n    due = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)\n    if due.second or due.microsecond:\n        due = due.replace(second=0, microsecond=0) + timedelta(minutes=1)\n    task.schedule = f"{due.minute} {due.hour} {due.day} {due.month} *"\n    task.do_only_once = True\n\n\nclass Scheduler:"""
    current_helper = """logger = logging.getLogger(__name__)\n\n\ndef _normalize_relative_schedule(task: Task, now: datetime) -> None:\n    schedule_text = task.schedule.strip()\n    match = re.fullmatch(r"P(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)", schedule_text.upper())\n    if match:\n        hours = int(match.group(1) or 0)\n        minutes = int(match.group(2) or 0)\n        seconds = int(match.group(3) or 0)\n        if hours == 0 and minutes == 0 and seconds == 0:\n            raise ValueError("Relative reminder duration must be greater than zero")\n        due = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)\n        if due.second or due.microsecond:\n            due = due.replace(second=0, microsecond=0) + timedelta(minutes=1)\n        task.schedule = f"{due.minute} {due.hour} {due.day} {due.month} *"\n        task.do_only_once = True\n        return\n    parts = schedule_text.split()\n    if len(parts) == 5 and re.fullmatch(r"\\d{1,2}:\\d{2}", parts[1]):\n        hour_text, minute_text = parts[1].split(":", 1)\n        task.schedule = f"{parts[0]} {minute_text} {hour_text} {parts[2]} {parts[3]} {parts[4]}"\n\n\nclass Scheduler:"""
    upgraded_helper = """logger = logging.getLogger(__name__)\n\n\ndef _schedule_for_due_time(due: datetime) -> str:\n    if due.second or due.microsecond:\n        due = due.replace(second=0, microsecond=0) + timedelta(minutes=1)\n    return f"{due.minute} {due.hour} {due.day} {due.month} *"\n\n\ndef _normalize_relative_schedule(task: Task, now: datetime) -> None:\n    schedule_text = task.schedule.strip()\n    match = re.fullmatch(r"P(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)", schedule_text.upper())\n    if match:\n        hours = int(match.group(1) or 0)\n        minutes = int(match.group(2) or 0)\n        seconds = int(match.group(3) or 0)\n        if hours == 0 and minutes == 0 and seconds == 0:\n            raise ValueError("Relative reminder duration must be greater than zero")\n        due = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)\n        task.schedule = _schedule_for_due_time(due)\n        task.do_only_once = True\n        return\n    natural_match = re.fullmatch(r"in\\s+(\\d+)\\s+(second|seconds|minute|minutes|hour|hours|day|days)", schedule_text.lower())\n    if natural_match:\n        amount = int(natural_match.group(1))\n        unit = natural_match.group(2)\n        if amount <= 0:\n            raise ValueError("Relative reminder duration must be greater than zero")\n        if unit.startswith("second"):\n            due = now + timedelta(seconds=amount)\n        elif unit.startswith("minute"):\n            due = now + timedelta(minutes=amount)\n        elif unit.startswith("hour"):\n            due = now + timedelta(hours=amount)\n        else:\n            due = now + timedelta(days=amount)\n        task.schedule = _schedule_for_due_time(due)\n        task.do_only_once = True\n        return\n    parts = schedule_text.split()\n    if len(parts) == 5 and re.fullmatch(r"\\d{1,2}:\\d{2}", parts[1]):\n        hour_text, minute_text = parts[1].split(":", 1)\n        task.schedule = f"{parts[0]} {minute_text} {hour_text} {parts[2]} {parts[3]} {parts[4]}"\n        return\n    if len(parts) in {6, 7} and "?" in parts:\n        task.schedule = " ".join("*" if part == "?" else part for part in parts)\n\n\nclass Scheduler:"""
    helper = """logger = logging.getLogger(__name__)\n\n\ndef _schedule_for_due_time(due: datetime) -> str:\n    if due.second or due.microsecond:\n        due = due.replace(second=0, microsecond=0) + timedelta(minutes=1)\n    return f"{due.minute} {due.hour} {due.day} {due.month} *"\n\n\ndef _relative_due_time(amount: int, unit: str, now: datetime) -> datetime:\n    if amount <= 0:\n        raise ValueError("Relative reminder duration must be greater than zero")\n    if unit.startswith("second"):\n        return now + timedelta(seconds=amount)\n    if unit.startswith("minute"):\n        return now + timedelta(minutes=amount)\n    if unit.startswith("hour"):\n        return now + timedelta(hours=amount)\n    return now + timedelta(days=amount)\n\n\ndef _normalize_relative_schedule(task: Task, now: datetime) -> None:\n    schedule_text = task.schedule.strip()\n    match = re.fullmatch(r"P(?:T(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?)", schedule_text.upper())\n    if match:\n        hours = int(match.group(1) or 0)\n        minutes = int(match.group(2) or 0)\n        seconds = int(match.group(3) or 0)\n        if hours == 0 and minutes == 0 and seconds == 0:\n            raise ValueError("Relative reminder duration must be greater than zero")\n        due = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)\n        task.schedule = _schedule_for_due_time(due)\n        task.do_only_once = True\n        return\n    lowered = schedule_text.lower()\n    natural_match = re.fullmatch(r"in\\s+(\\d+)\\s+(second|seconds|minute|minutes|hour|hours|day|days)", lowered)\n    if natural_match:\n        amount = int(natural_match.group(1))\n        unit = natural_match.group(2)\n        due = _relative_due_time(amount, unit, now)\n        task.schedule = _schedule_for_due_time(due)\n        task.do_only_once = True\n        return\n    from_now_match = re.fullmatch(r"(\\d+)\\s+(second|seconds|minute|minutes|hour|hours|day|days)\\s+from\\s+now", lowered)\n    if from_now_match:\n        amount = int(from_now_match.group(1))\n        unit = from_now_match.group(2)\n        due = _relative_due_time(amount, unit, now)\n        task.schedule = _schedule_for_due_time(due)\n        task.do_only_once = True\n        return\n    parts = schedule_text.split()\n    if len(parts) == 5 and re.fullmatch(r"\\d{1,2}:\\d{2}", parts[1]):\n        hour_text, minute_text = parts[1].split(":", 1)\n        task.schedule = f"{parts[0]} {minute_text} {hour_text} {parts[2]} {parts[3]} {parts[4]}"\n        return\n    if len(parts) in {6, 7} and "?" in parts:\n        task.schedule = " ".join("*" if part == "?" else part for part in parts)\n\n\nclass Scheduler:"""
    if old_helper in patched:
        patched = patched.replace(old_helper, helper, 1)
    if current_helper in patched:
        patched = patched.replace(current_helper, helper, 1)
    if upgraded_helper in patched:
        patched = patched.replace(upgraded_helper, helper, 1)
    if "_schedule_for_due_time" not in patched:
        helper_start = patched.find("logger = logging.getLogger(__name__)")
        helper_end = patched.find("\n\nclass Scheduler:")
        if helper_start != -1 and helper_end != -1:
            patched = f"{patched[:helper_start]}{helper}{patched[helper_end + len(chr(10) + chr(10) + 'class Scheduler:') :]}"
    if (
        marker in patched
        and "def _normalize_relative_schedule(task: Task, now: datetime)" not in patched
    ):
        patched = patched.replace(marker, helper, 1)
    add_task_block = """        now = datetime.utcnow()\n        try:\n            cron = croniter.croniter(task.schedule, now)\n"""
    add_task_replacement = """        now = datetime.utcnow()\n        _normalize_relative_schedule(task, now)\n        try:\n            cron = croniter.croniter(task.schedule, now)\n"""
    if add_task_block in patched and "_normalize_relative_schedule(task, now)" not in patched:
        patched = patched.replace(add_task_block, add_task_replacement, 1)
    update_task_block = """        if \"schedule\" in kwargs:\n            now = datetime.utcnow()\n            try:\n                cron = croniter.croniter(task.schedule, now)\n"""
    update_task_replacement = """        if \"schedule\" in kwargs:\n            now = datetime.utcnow()\n            _normalize_relative_schedule(task, now)\n            try:\n                cron = croniter.croniter(task.schedule, now)\n"""
    if (
        update_task_block in patched
        and patched.count("_normalize_relative_schedule(task, now)") < 2
    ):
        patched = patched.replace(update_task_block, update_task_replacement, 1)
    if patched != original:
        SCHEDULER_FILE.write_text(patched, encoding="utf-8")


def _patch_executor_shell_support() -> None:
    if not EXECUTOR_FILE.exists():
        return
    original = EXECUTOR_FILE.read_text(encoding="utf-8")
    patched = original
    old_block = """        # If pipe or redirect is in command, use shell mode
        if '|' in command or '>' in command or '<' in command:
            use_shell = True
"""
    new_block = """        # If shell metacharacters are in command
        if (
            '|' in command
            or '>' in command
            or '<' in command
            or '&&' in command
            or '||' in command
            or ';' in command
            or '&' in command
        ):
            use_shell = True
"""
    if old_block in patched:
        patched = patched.replace(old_block, new_block, 1)
    elif "'&&' in command" not in patched:
        legacy_line = "        if '|' in command or '>' in command or '<' in command:\n            use_shell = True\n"
        patched = patched.replace(legacy_line, new_block, 1)
    old_sound_line = """                sound_cmd = 'paplay /usr/share/sounds/freedesktop/stereo/message.oga'\n                \n                # Chain commands together\n                command = f'{notify_cmd} && {sound_cmd}'\n"""
    new_sound_line = """                sound_cmd = 'paplay /usr/share/sounds/freedesktop/stereo/message.oga'\n                optional_sound_cmd = (\n                    'if command -v paplay >/dev/null 2>&1; '\n                    f'then {sound_cmd}; '\n                    'fi'\n                )\n                \n                # Show the notification even when optional sound support is unavailable\n                command = f'{notify_cmd}; {optional_sound_cmd}'\n"""
    if old_sound_line in patched:
        patched = patched.replace(old_sound_line, new_sound_line, 1)
    if patched != original:
        EXECUTOR_FILE.write_text(patched, encoding="utf-8")


def _bootstrap_scheduler_server() -> tuple[Path, Path]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with FileLock(str(LOCK_PATH)):
        checkout_changed = _ensure_checkout_at_pinned_commit()
        _patch_fastmcp_compatibility()
        _patch_relative_schedule_support()
        _patch_executor_shell_support()
        python_path = _python_in_venv()
        if checkout_changed and VENV_DIR.exists():
            _run(
                [str(python_path), "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=CHECKOUT_DIR,
            )
        elif not python_path.exists():
            print("Creating scheduler-mcp virtualenv...", file=sys.stderr)
            _run([sys.executable, "-m", "venv", str(VENV_DIR)])
            _run(
                [str(python_path), "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=CHECKOUT_DIR,
            )
        _write_bootstrapped_revision(UPSTREAM_COMMIT)
    return CHECKOUT_DIR / "main.py", python_path


def main() -> None:
    entrypoint, python_path = _bootstrap_scheduler_server()
    runtime_dir = REPO_ROOT / "chanakya_data" / "scheduler"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("MCP_SCHEDULER_TRANSPORT", "stdio")
    env.setdefault("MCP_SCHEDULER_DB_PATH", str(runtime_dir / "scheduler.db"))
    env.setdefault("MCP_SCHEDULER_LOG_FILE", str(runtime_dir / "scheduler.log"))
    os.execvpe(
        str(python_path),
        [str(python_path), str(entrypoint), "--transport", "stdio"],
        env,
    )


if __name__ == "__main__":
    main()
