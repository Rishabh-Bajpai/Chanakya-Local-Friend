from pathlib import Path

from chanakya.services import mcp_scheduler_launcher as launcher


def test_patch_fastmcp_compatibility_removes_version_argument(
    tmp_path: Path,
    monkeypatch,
) -> None:
    server_file = tmp_path / "server.py"
    server_file.write_text(
        """self.mcp = CustomFastMCP(
            config.server_name,
            version=config.server_version,
            dependencies=[
                \"croniter\",
                \"pydantic\",
                \"openai\",
                \"aiohttp\"
            ]
        )
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "SERVER_FILE", server_file)

    launcher._patch_fastmcp_compatibility()

    patched = server_file.read_text(encoding="utf-8")
    assert "version=config.server_version" not in patched
    assert "dependencies=[" in patched


def test_patch_relative_schedule_support_adds_normalizer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scheduler_file = tmp_path / "scheduler.py"
    scheduler_file.write_text(
        """import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

import croniter

from .task import Task, TaskStatus, TaskExecution
from .persistence import Database
from .executor import Executor

logger = logging.getLogger(__name__)


class Scheduler:
    async def add_task(self, task: Task) -> Task:
        now = datetime.utcnow()
        try:
            cron = croniter.croniter(task.schedule, now)
            task.next_run = cron.get_next(datetime)
        except Exception as e:
            raise ValueError(f"Invalid cron expression: {e}")

    async def update_task(self, task_id: str, **kwargs):
        if "schedule" in kwargs:
            now = datetime.utcnow()
            try:
                cron = croniter.croniter(task.schedule, now)
                task.next_run = cron.get_next(datetime)
            except Exception as e:
                raise ValueError(f"Invalid cron expression: {e}")
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "SCHEDULER_FILE", scheduler_file)

    launcher._patch_relative_schedule_support()

    patched = scheduler_file.read_text(encoding="utf-8")
    assert "from datetime import datetime, timedelta" in patched
    assert "def _schedule_for_due_time(due: datetime) -> str:" in patched
    assert "def _relative_due_time(amount: int, unit: str, now: datetime) -> datetime:" in patched
    assert "def _normalize_relative_schedule(task: Task, now: datetime)" in patched
    assert patched.count("_normalize_relative_schedule(task, now)") == 2
    assert 're.fullmatch(r"\\d{1,2}:\\d{2}", parts[1])' in patched
    assert 'hour_text, minute_text = parts[1].split(":", 1)' in patched
    assert (
        're.fullmatch(r"in\\s+(\\d+)\\s+(second|seconds|minute|minutes|hour|hours|day|days)"'
        in patched
    )
    assert (
        're.fullmatch(r"(\\d+)\\s+(second|seconds|minute|minutes|hour|hours|day|days)\\s+from\\s+now"'
        in patched
    )
    assert '"*" if part == "?" else part for part in parts' in patched


def test_patch_executor_shell_support_adds_shell_metacharacters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executor_file = tmp_path / "executor.py"
    executor_file.write_text(
        """        # If pipe or redirect is in command
        if '|' in command or '>' in command or '<' in command:
            use_shell = True
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "EXECUTOR_FILE", executor_file)

    launcher._patch_executor_shell_support()

    patched = executor_file.read_text(encoding="utf-8")
    assert "'&&' in command" in patched
    assert "'||' in command" in patched
    assert "';' in command" in patched
    assert "'&' in command" in patched


def test_patch_executor_shell_support_makes_sound_optional(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executor_file = tmp_path / "executor.py"
    executor_file.write_text(
        """                sound_cmd = 'paplay /usr/share/sounds/freedesktop/stereo/message.oga'
                
                # Chain commands together
                command = f'{notify_cmd} && {sound_cmd}'
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "EXECUTOR_FILE", executor_file)

    launcher._patch_executor_shell_support()

    patched = executor_file.read_text(encoding="utf-8")
    assert "optional_sound_cmd" in patched
    assert "command -v paplay" in patched
    assert "command = f'{notify_cmd}; {optional_sound_cmd}'" in patched
