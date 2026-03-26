from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from chanakya_mvp.models import ALLOWED_TRANSITIONS, Task, TaskStatus, now_iso


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    parent_task_id TEXT,
                    result TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason TEXT,
                    timestamp TEXT NOT NULL
                );
                """
            )

    def create_task(self, task: Task) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, description, owner, status, dependencies, parent_task_id,
                    result, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.description,
                    task.owner,
                    task.status.value,
                    json.dumps(task.dependencies),
                    task.parent_task_id,
                    task.result,
                    json.dumps(task.metadata),
                    task.created_at,
                    task.updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_transitions (task_id, from_status, to_status, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task.id, None, task.status.value, "task_created", now_iso()),
            )

    def get_task(self, task_id: str) -> Task:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Task not found: {task_id}")
        return self._row_to_task(row)

    def list_tasks(self) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_children(self, parent_task_id: str) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC",
                (parent_task_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_task_status(self, task_id: str, new_status: TaskStatus, reason: str) -> Task:
        task = self.get_task(task_id)
        allowed = ALLOWED_TRANSITIONS[task.status]
        if new_status not in allowed and task.status != new_status:
            raise ValueError(
                f"Invalid state transition for {task_id}: {task.status.value} -> {new_status.value}"
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (new_status.value, now_iso(), task_id),
            )
            conn.execute(
                """
                INSERT INTO task_transitions (task_id, from_status, to_status, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, task.status.value, new_status.value, reason, now_iso()),
            )
        return self.get_task(task_id)

    def update_task_result(
        self, task_id: str, result: str, metadata: dict[str, Any] | None = None
    ) -> Task:
        task = self.get_task(task_id)
        next_metadata = task.metadata.copy()
        if metadata:
            next_metadata.update(metadata)
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET result = ?, metadata = ?, updated_at = ? WHERE id = ?",
                (result, json.dumps(next_metadata), now_iso(), task_id),
            )
        return self.get_task(task_id)

    def get_state_history(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT from_status, to_status, reason, timestamp
                FROM task_transitions
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=str(row["id"]),
            description=str(row["description"]),
            owner=str(row["owner"]),
            status=TaskStatus(str(row["status"])),
            dependencies=list(json.loads(str(row["dependencies"]))),
            parent_task_id=row["parent_task_id"],
            result=row["result"],
            metadata=dict(json.loads(str(row["metadata"]))),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
