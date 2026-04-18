from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core_agent_app.db import AgentSessionContextRecord


class SQLAlchemyAgentSessionContextStore:
    def __init__(self, db_session_factory: sessionmaker[Session]) -> None:
        self.db_session_factory = db_session_factory

    def _storage_session_id(self, session_id: str, target_key: str | None) -> str:
        if target_key:
            return f"{session_id}::target::{target_key}"
        return session_id

    def _base_payload(
        self,
        *,
        session_id: str,
        target_key: str | None,
    ) -> dict:
        return {
            "session_id": session_id,
            "target_key": target_key,
            "backend": None,
            "remote_context_id": None,
            "remote_agent_url": None,
        }

    def get(self, session_id: str, *, target_key: str | None = None) -> dict:
        storage_session_id = self._storage_session_id(session_id, target_key)
        with self.db_session_factory() as db:
            record = db.execute(
                select(AgentSessionContextRecord).where(
                    AgentSessionContextRecord.session_id == storage_session_id
                )
            ).scalar_one_or_none()
            if record is None:
                return self._base_payload(session_id=session_id, target_key=target_key)
            return {
                "session_id": session_id,
                "target_key": target_key,
                "backend": record.backend,
                "remote_context_id": record.remote_context_id,
                "remote_agent_url": record.remote_agent_url,
                "updated_at": record.updated_at.isoformat(),
            }

    def save(
        self,
        session_id: str,
        *,
        backend: str,
        remote_context_id: str | None,
        remote_agent_url: str | None,
        target_key: str | None = None,
    ) -> dict:
        storage_session_id = self._storage_session_id(session_id, target_key)
        with self.db_session_factory() as db:
            record = db.execute(
                select(AgentSessionContextRecord).where(
                    AgentSessionContextRecord.session_id == storage_session_id
                )
            ).scalar_one_or_none()
            if record is None:
                record = AgentSessionContextRecord(
                    session_id=storage_session_id,
                    backend=backend,
                    remote_context_id=remote_context_id,
                    remote_agent_url=remote_agent_url,
                )
                db.add(record)
            else:
                record.backend = backend
                record.remote_context_id = remote_context_id
                record.remote_agent_url = remote_agent_url
                record.updated_at = datetime.now(UTC)
            db.commit()
        return self.get(session_id, target_key=target_key)

    def delete(self, session_id: str, *, target_key: str | None = None) -> None:
        with self.db_session_factory() as db:
            if target_key is None:
                records = (
                    db.execute(
                        select(AgentSessionContextRecord).where(
                            AgentSessionContextRecord.session_id == session_id
                        )
                    )
                    .scalars()
                    .all()
                )
                prefixed_records = (
                    db.execute(
                        select(AgentSessionContextRecord).where(
                            AgentSessionContextRecord.session_id.like(
                                f"{session_id}::target::%"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for record in [*records, *prefixed_records]:
                    db.delete(record)
                db.commit()
                return

            record = db.execute(
                select(AgentSessionContextRecord).where(
                    AgentSessionContextRecord.session_id
                    == self._storage_session_id(session_id, target_key)
                )
            ).scalar_one_or_none()
            if record is not None:
                db.delete(record)
                db.commit()
