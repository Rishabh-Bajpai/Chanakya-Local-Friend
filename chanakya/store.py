from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from chanakya.models import AgentProfile, now_iso


class ChanakyaStore:
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
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    request_id TEXT,
                    route TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    personality TEXT NOT NULL,
                    tool_ids TEXT NOT NULL,
                    workspace TEXT,
                    heartbeat_enabled INTEGER NOT NULL,
                    heartbeat_interval_seconds INTEGER NOT NULL,
                    heartbeat_file_path TEXT,
                    is_active INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def create_session(self, session_id: str, title: str) -> None:
        timestamp = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, title, timestamp, timestamp),
            )

    def ensure_session(self, session_id: str, title: str = "New chat") -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO chat_sessions (id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, title, now_iso(), now_iso()),
                )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        request_id: str | None = None,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ensure_session(session_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    session_id, role, content, request_id, route, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    request_id,
                    route,
                    json.dumps(metadata or {}),
                    now_iso(),
                ),
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now_iso(), session_id),
            )

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, request_id, route, metadata, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
                "request_id": row["request_id"],
                "route": row["route"],
                "metadata": dict(json.loads(str(row["metadata"]))),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_events (event_type, payload, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload), now_iso()),
            )

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, payload, created_at
                FROM app_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events = [
            {
                "id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "payload": dict(json.loads(str(row["payload"]))),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
        events.reverse()
        return events

    def upsert_agent_profile(self, profile: AgentProfile) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_profiles (
                    id, name, role, system_prompt, personality, tool_ids, workspace,
                    heartbeat_enabled, heartbeat_interval_seconds, heartbeat_file_path,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    role = excluded.role,
                    system_prompt = excluded.system_prompt,
                    personality = excluded.personality,
                    tool_ids = excluded.tool_ids,
                    workspace = excluded.workspace,
                    heartbeat_enabled = excluded.heartbeat_enabled,
                    heartbeat_interval_seconds = excluded.heartbeat_interval_seconds,
                    heartbeat_file_path = excluded.heartbeat_file_path,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.id,
                    profile.name,
                    profile.role,
                    profile.system_prompt,
                    profile.personality,
                    json.dumps(profile.tool_ids),
                    profile.workspace,
                    int(profile.heartbeat_enabled),
                    profile.heartbeat_interval_seconds,
                    profile.heartbeat_file_path,
                    int(profile.is_active),
                    profile.created_at,
                    profile.updated_at,
                ),
            )

    def list_agent_profiles(self) -> list[AgentProfile]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_profiles ORDER BY name ASC").fetchall()
        return [self._row_to_agent_profile(row) for row in rows]

    def get_agent_profile(self, agent_id: str) -> AgentProfile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?",
                (agent_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Agent profile not found: {agent_id}")
        return self._row_to_agent_profile(row)

    @staticmethod
    def _row_to_agent_profile(row: sqlite3.Row) -> AgentProfile:
        return AgentProfile(
            id=str(row["id"]),
            name=str(row["name"]),
            role=str(row["role"]),
            system_prompt=str(row["system_prompt"]),
            personality=str(row["personality"]),
            tool_ids=list(json.loads(str(row["tool_ids"]))),
            workspace=row["workspace"],
            heartbeat_enabled=bool(row["heartbeat_enabled"]),
            heartbeat_interval_seconds=int(row["heartbeat_interval_seconds"]),
            heartbeat_file_path=row["heartbeat_file_path"],
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
