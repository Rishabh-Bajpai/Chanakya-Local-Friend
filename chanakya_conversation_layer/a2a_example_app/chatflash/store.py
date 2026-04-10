from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import MessageRecord, SessionRecord


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    remote_context_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                """
            )

    def list_sessions(self) -> list[SessionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def create_session(self, agent_id: str, title: str = "New conversation") -> SessionRecord:
        session_id = uuid.uuid4().hex
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, agent_id, remote_context_id, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?)",
                (session_id, title, agent_id, now, now),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return self._session_from_row(row)

    def update_session_agent(self, session_id: str, agent_id: str) -> SessionRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET agent_id = ?, remote_context_id = NULL, updated_at = ? WHERE id = ?",
                (agent_id, now, session_id),
            )
        return self.get_session(session_id)

    def update_session_title(self, session_id: str, title: str) -> SessionRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, session_id),
            )
        return self.get_session(session_id)

    def set_remote_context(self, session_id: str, remote_context_id: str | None) -> SessionRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET remote_context_id = ?, updated_at = ? WHERE id = ?",
                (remote_context_id, now, session_id),
            )
        return self.get_session(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> MessageRecord:
        message_id = uuid.uuid4().hex
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return self.get_messages(session_id)[-1]

    def get_messages(self, session_id: str) -> list[MessageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def maybe_set_title_from_message(self, session_id: str, text: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session.title != "New conversation":
            return session
        title = " ".join(text.strip().split())[:60] or "New conversation"
        return self.update_session_title(session_id, title)

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            agent_id=row["agent_id"],
            remote_context_id=row["remote_context_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )
