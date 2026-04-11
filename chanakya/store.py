from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from chanakya.db import session_scope
from chanakya.domain import TASK_STATUS_FAILED, now_iso
from chanakya.model import (
    AgentProfileModel,
    AgentSessionContextModel,
    AppEventModel,
    ChatMessageModel,
    ChatSessionModel,
    ClassicActiveWorkModel,
    RequestModel,
    TaskEventModel,
    TaskModel,
    TemporaryAgentModel,
    ToolInvocationModel,
    WorkAgentSessionModel,
    WorkModel,
)


class ChatRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_session(self, session_id: str, title: str) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            session.add(
                ChatSessionModel(
                    id=session_id,
                    title=title,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()

    def ensure_session(self, session_id: str, title: str = "New chat") -> None:
        with session_scope(self.Session) as session:
            existing = session.get(ChatSessionModel, session_id)
            if existing is None:
                timestamp = now_iso()
                session.add(
                    ChatSessionModel(
                        id=session_id,
                        title=title,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                session.commit()

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
        with session_scope(self.Session) as session:
            session.add(
                ChatMessageModel(
                    session_id=session_id,
                    role=role,
                    content=content,
                    request_id=request_id,
                    route=route,
                    metadata_json=metadata or {},
                    created_at=now_iso(),
                )
            )
            chat_session = session.get(ChatSessionModel, session_id)
            if chat_session is not None:
                chat_session.updated_at = now_iso()
            session.commit()

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.id.asc())
            ).all()
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "request_id": row.request_id,
                "route": row.route,
                "metadata": row.metadata_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    def rewrite_latest_assistant_message(
        self,
        session_id: str,
        *,
        content: str,
        request_id: str | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .where(ChatMessageModel.role == "assistant")
                .order_by(ChatMessageModel.id.desc())
            ).all()
            target = None
            if request_id is not None:
                for row in rows:
                    if row.request_id == request_id:
                        target = row
                        break
            if target is None and rows:
                target = rows[0]
            if target is None:
                return
            target.content = content
            if metadata_update:
                target.metadata_json = {**(target.metadata_json or {}), **metadata_update}
            chat_session = session.get(ChatSessionModel, session_id)
            if chat_session is not None:
                chat_session.updated_at = now_iso()
            session.commit()


class EventRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with session_scope(self.Session) as session:
            session.add(
                AppEventModel(
                    event_type=event_type,
                    payload_json=payload,
                    created_at=now_iso(),
                )
            )
            session.commit()

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(AppEventModel).order_by(AppEventModel.id.desc()).limit(limit)
            ).all()
        events = [
            {
                "id": row.id,
                "event_type": row.event_type,
                "payload": row.payload_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        events.reverse()
        return events

    def create_task_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            session.add(
                TaskEventModel(
                    session_id=session_id,
                    request_id=request_id,
                    task_id=task_id,
                    event_type=event_type,
                    payload_json=payload,
                    created_at=now_iso(),
                )
            )
            session.commit()

    def list_task_events(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = select(TaskEventModel).order_by(TaskEventModel.id.desc()).limit(limit)
            if session_id is not None:
                stmt = stmt.where(TaskEventModel.session_id == session_id)
            if request_id is not None:
                stmt = stmt.where(TaskEventModel.request_id == request_id)
            if task_id is not None:
                stmt = stmt.where(TaskEventModel.task_id == task_id)
            rows = session.scalars(stmt).all()

        records = [
            {
                "id": row.id,
                "session_id": row.session_id,
                "request_id": row.request_id,
                "task_id": row.task_id,
                "event_type": row.event_type,
                "payload": row.payload_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        records.reverse()
        return records


class AgentSessionContextRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    @staticmethod
    def _storage_session_id(session_id: str, target_key: str | None = None) -> str:
        if target_key:
            return f"{session_id}::target::{target_key}"
        return session_id

    def get(self, session_id: str, *, target_key: str | None = None) -> dict[str, Any]:
        storage_session_id = self._storage_session_id(session_id, target_key)
        with session_scope(self.Session) as session:
            row = session.scalar(
                select(AgentSessionContextModel).where(
                    AgentSessionContextModel.session_id == storage_session_id
                )
            )
        if row is None:
            return {
                "session_id": session_id,
                "target_key": target_key,
                "backend": None,
                "remote_context_id": None,
                "remote_agent_url": None,
            }
        return {
            "session_id": session_id,
            "target_key": target_key,
            "backend": row.backend,
            "remote_context_id": row.remote_context_id,
            "remote_agent_url": row.remote_agent_url,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def save(
        self,
        session_id: str,
        *,
        backend: str,
        remote_context_id: str | None,
        remote_agent_url: str | None,
        target_key: str | None = None,
    ) -> dict[str, Any]:
        storage_session_id = self._storage_session_id(session_id, target_key)
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            row = session.scalar(
                select(AgentSessionContextModel).where(
                    AgentSessionContextModel.session_id == storage_session_id
                )
            )
            if row is None:
                session.add(
                    AgentSessionContextModel(
                        session_id=storage_session_id,
                        backend=backend,
                        remote_context_id=remote_context_id,
                        remote_agent_url=remote_agent_url,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            else:
                row.backend = backend
                row.remote_context_id = remote_context_id
                row.remote_agent_url = remote_agent_url
                row.updated_at = timestamp
            session.commit()
        return self.get(session_id, target_key=target_key)

    def delete(self, session_id: str, *, target_key: str | None = None) -> None:
        with session_scope(self.Session) as session:
            if target_key is None:
                session.execute(
                    delete(AgentSessionContextModel).where(
                        AgentSessionContextModel.session_id == session_id
                    )
                )
                session.execute(
                    delete(AgentSessionContextModel).where(
                        AgentSessionContextModel.session_id.like(f"{session_id}::target::%")
                    )
                )
            else:
                storage_session_id = self._storage_session_id(session_id, target_key)
                session.execute(
                    delete(AgentSessionContextModel).where(
                        AgentSessionContextModel.session_id == storage_session_id
                    )
                )
            session.commit()


class RequestRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_request(
        self,
        *,
        request_id: str,
        session_id: str,
        user_message: str,
        status: str,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            session.add(
                RequestModel(
                    id=request_id,
                    session_id=session_id,
                    user_message=user_message,
                    route=route,
                    status=status,
                    root_task_id=root_task_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()

    def update_request(
        self,
        request_id: str,
        *,
        status: str | None = None,
        route: str | None = None,
        root_task_id: str | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            row = session.get(RequestModel, request_id)
            if row is None:
                return
            if status is not None:
                row.status = status
            if route is not None:
                row.route = route
            if root_task_id is not None:
                row.root_task_id = root_task_id
            row.updated_at = now_iso()
            session.commit()

    def get_request(self, request_id: str) -> RequestModel:
        with session_scope(self.Session) as session:
            row = session.get(RequestModel, request_id)
        if row is None:
            raise KeyError(f"Request not found: {request_id}")
        return row

    def list_requests(
        self, *, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = select(RequestModel).order_by(RequestModel.created_at.desc()).limit(limit)
            if session_id is not None:
                stmt = stmt.where(RequestModel.session_id == session_id)
            rows = session.scalars(stmt).all()
        records = [
            {
                "id": row.id,
                "session_id": row.session_id,
                "user_message": row.user_message,
                "route": row.route,
                "status": row.status,
                "root_task_id": row.root_task_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
        records.reverse()
        return records


class TaskRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_task(
        self,
        *,
        task_id: str,
        request_id: str,
        parent_task_id: str | None,
        title: str,
        summary: str | None,
        status: str,
        owner_agent_id: str | None,
        task_type: str,
        dependencies: list[str] | None = None,
        input_json: dict[str, Any] | None = None,
    ) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            session.add(
                TaskModel(
                    id=task_id,
                    request_id=request_id,
                    parent_task_id=parent_task_id,
                    title=title,
                    summary=summary,
                    status=status,
                    owner_agent_id=owner_agent_id,
                    task_type=task_type,
                    dependencies_json=dependencies or [],
                    input_json=input_json or {},
                    result_json={},
                    error_text=None,
                    created_at=timestamp,
                    updated_at=timestamp,
                    started_at=None,
                    finished_at=None,
                )
            )
            session.commit()

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        owner_agent_id: str | None = None,
        dependencies: list[str] | None = None,
        input_json: dict[str, Any] | None = None,
        result_json: dict[str, Any] | None = None,
        error_text: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            row = session.get(TaskModel, task_id)
            if row is None:
                return
            if status is not None:
                row.status = status
            if summary is not None:
                row.summary = summary
            if owner_agent_id is not None:
                row.owner_agent_id = owner_agent_id
            if dependencies is not None:
                row.dependencies_json = dependencies
            if input_json is not None:
                row.input_json = input_json
            if result_json is not None:
                row.result_json = result_json
            if error_text is not None:
                row.error_text = error_text
            elif status is not None and status != TASK_STATUS_FAILED:
                row.error_text = None
            if started_at is not None:
                row.started_at = started_at
            if finished_at is not None:
                row.finished_at = finished_at
            row.updated_at = now_iso()
            session.commit()

    def get_task(self, task_id: str) -> TaskModel:
        with session_scope(self.Session) as session:
            row = session.get(TaskModel, task_id)
        if row is None:
            raise KeyError(f"Task not found: {task_id}")
        return row

    def list_tasks(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        root_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = select(TaskModel, RequestModel.session_id).join(
                RequestModel,
                TaskModel.request_id == RequestModel.id,
            )
            if session_id is not None:
                stmt = stmt.where(RequestModel.session_id == session_id)
            if request_id is not None:
                stmt = stmt.where(TaskModel.request_id == request_id)
            if root_only:
                stmt = stmt.where(TaskModel.parent_task_id.is_(None))
            stmt = stmt.order_by(TaskModel.created_at.desc()).limit(limit)
            rows = session.execute(stmt).all()

        records = [
            {
                "id": task.id,
                "request_id": task.request_id,
                "session_id": linked_session_id,
                "parent_task_id": task.parent_task_id,
                "title": task.title,
                "summary": task.summary,
                "status": task.status,
                "owner_agent_id": task.owner_agent_id,
                "task_type": task.task_type,
                "dependencies": task.dependencies_json,
                "input": task.input_json,
                "result": task.result_json,
                "error": task.error_text,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "is_root": task.parent_task_id is None,
            }
            for task, linked_session_id in rows
        ]
        records.reverse()
        return records

    def list_children(
        self,
        parent_task_id: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = select(TaskModel, RequestModel.session_id).join(
                RequestModel,
                TaskModel.request_id == RequestModel.id,
            )
            stmt = stmt.where(TaskModel.parent_task_id == parent_task_id)
            if session_id is not None:
                stmt = stmt.where(RequestModel.session_id == session_id)
            if request_id is not None:
                stmt = stmt.where(TaskModel.request_id == request_id)
            stmt = stmt.order_by(TaskModel.created_at.asc())
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).all()
        return [
            {
                "id": task.id,
                "request_id": task.request_id,
                "session_id": linked_session_id,
                "parent_task_id": task.parent_task_id,
                "title": task.title,
                "summary": task.summary,
                "status": task.status,
                "owner_agent_id": task.owner_agent_id,
                "task_type": task.task_type,
                "dependencies": task.dependencies_json,
                "input": task.input_json,
                "result": task.result_json,
                "error": task.error_text,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "is_root": False,
            }
            for task, linked_session_id in rows
        ]


class ToolInvocationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_tool_invocation(
        self,
        *,
        invocation_id: str,
        request_id: str,
        session_id: str,
        agent_id: str | None,
        agent_name: str,
        tool_id: str,
        tool_name: str,
        server_name: str,
        status: str,
        input_json: dict[str, Any] | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            session.add(
                ToolInvocationModel(
                    invocation_id=invocation_id,
                    request_id=request_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    server_name=server_name,
                    status=status,
                    input_json=input_json or {},
                    output_text=None,
                    error_text=None,
                    started_at=now_iso(),
                    finished_at=None,
                )
            )
            session.commit()

    def finish_tool_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        output_text: str | None = None,
        error_text: str | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            row = session.scalars(
                select(ToolInvocationModel).where(
                    ToolInvocationModel.invocation_id == invocation_id
                )
            ).first()
            if row is None:
                return
            row.status = status
            row.output_text = output_text
            row.error_text = error_text
            row.finished_at = now_iso()
            session.commit()

    def list_tool_invocations(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = select(ToolInvocationModel).order_by(ToolInvocationModel.id.desc()).limit(limit)
            if session_id is not None:
                stmt = stmt.where(ToolInvocationModel.session_id == session_id)
            if request_id is not None:
                stmt = stmt.where(ToolInvocationModel.request_id == request_id)
            rows = session.scalars(stmt).all()

        records = [
            {
                "id": row.id,
                "invocation_id": row.invocation_id,
                "request_id": row.request_id,
                "session_id": row.session_id,
                "agent_id": row.agent_id,
                "agent_name": row.agent_name,
                "tool_id": row.tool_id,
                "tool_name": row.tool_name,
                "server_name": row.server_name,
                "status": row.status,
                "input": row.input_json,
                "output": row.output_text,
                "error": row.error_text,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ]
        records.reverse()
        return records


class AgentProfileRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def has_agent_profile(self, agent_id: str) -> bool:
        with session_scope(self.Session) as session:
            return session.get(AgentProfileModel, agent_id) is not None

    def upsert_agent_profile(self, profile: AgentProfileModel) -> None:
        with session_scope(self.Session) as session:
            row = session.get(AgentProfileModel, profile.id)
            if row is None:
                session.add(profile)
            else:
                row.name = profile.name
                row.role = profile.role
                row.system_prompt = profile.system_prompt
                row.personality = profile.personality
                row.tool_ids_json = profile.tool_ids_json
                row.workspace = profile.workspace
                row.heartbeat_enabled = profile.heartbeat_enabled
                row.heartbeat_interval_seconds = profile.heartbeat_interval_seconds
                row.heartbeat_file_path = profile.heartbeat_file_path
                row.is_active = profile.is_active
                row.updated_at = profile.updated_at
            session.commit()

    def create_agent_profile(self, profile: AgentProfileModel) -> None:
        with session_scope(self.Session) as session:
            session.add(profile)
            session.commit()

    def update_agent_profile(
        self,
        agent_id: str,
        *,
        name: str,
        role: str,
        system_prompt: str,
        personality: str,
        tool_ids: list[str],
        workspace: str | None,
        heartbeat_enabled: bool,
        heartbeat_interval_seconds: int,
        heartbeat_file_path: str | None,
        is_active: bool,
    ) -> AgentProfileModel:
        with session_scope(self.Session) as session:
            row = session.get(AgentProfileModel, agent_id)
            if row is None:
                raise KeyError(f"Agent profile not found: {agent_id}")
            row.name = name
            row.role = role
            row.system_prompt = system_prompt
            row.personality = personality
            row.tool_ids_json = tool_ids
            row.workspace = workspace
            row.heartbeat_enabled = heartbeat_enabled
            row.heartbeat_interval_seconds = heartbeat_interval_seconds
            row.heartbeat_file_path = heartbeat_file_path
            row.is_active = is_active
            row.updated_at = now_iso()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def list_agent_profiles(self) -> list[AgentProfileModel]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(AgentProfileModel).order_by(AgentProfileModel.name.asc())
            ).all()
        return cast(list[AgentProfileModel], rows)

    def get_agent_profile(self, agent_id: str) -> AgentProfileModel:
        with session_scope(self.Session) as session:
            row = session.get(AgentProfileModel, agent_id)
        if row is None:
            raise KeyError(f"Agent profile not found: {agent_id}")
        return row

    def find_active_agents_by_role(self, role: str) -> list[AgentProfileModel]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(AgentProfileModel)
                .where(AgentProfileModel.role == role)
                .where(AgentProfileModel.is_active.is_(True))
                .order_by(AgentProfileModel.name.asc())
            ).all()
        return cast(list[AgentProfileModel], rows)


class WorkRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_work(
        self,
        *,
        work_id: str,
        title: str,
        description: str | None = None,
        status: str = "active",
    ) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            session.add(
                WorkModel(
                    id=work_id,
                    title=title,
                    description=description,
                    status=status,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()

    def list_works(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(WorkModel).order_by(WorkModel.updated_at.desc()).limit(limit)
            ).all()
        records = [
            {
                "id": row.id,
                "title": row.title,
                "description": row.description,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
        records.reverse()
        return records

    def get_work(self, work_id: str) -> WorkModel:
        with session_scope(self.Session) as session:
            row = session.get(WorkModel, work_id)
        if row is None:
            raise KeyError(f"Work not found: {work_id}")
        return row

    def delete_work(self, work_id: str) -> None:
        with session_scope(self.Session) as session:
            row = session.get(WorkModel, work_id)
            if row is None:
                raise KeyError(f"Work not found: {work_id}")
            session.delete(row)
            session.commit()


class WorkAgentSessionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def ensure_work_agent_session(
        self,
        *,
        work_id: str,
        agent_id: str,
        session_id: str,
        session_title: str,
    ) -> str:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            existing = session.scalars(
                select(WorkAgentSessionModel)
                .where(WorkAgentSessionModel.work_id == work_id)
                .where(WorkAgentSessionModel.agent_id == agent_id)
                .limit(1)
            ).first()
            if existing is not None:
                existing.updated_at = timestamp
                session.commit()
                return existing.session_id

            chat_session = session.get(ChatSessionModel, session_id)
            if chat_session is None:
                session.add(
                    ChatSessionModel(
                        id=session_id,
                        title=session_title,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            session.add(
                WorkAgentSessionModel(
                    work_id=work_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()
            return session_id

    def list_work_agent_sessions(self, work_id: str) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            rows = session.execute(
                select(
                    WorkAgentSessionModel,
                    AgentProfileModel.name,
                    AgentProfileModel.role,
                    ChatSessionModel.updated_at,
                )
                .join(
                    AgentProfileModel,
                    WorkAgentSessionModel.agent_id == AgentProfileModel.id,
                    isouter=True,
                )
                .join(
                    ChatSessionModel,
                    WorkAgentSessionModel.session_id == ChatSessionModel.id,
                    isouter=True,
                )
                .where(WorkAgentSessionModel.work_id == work_id)
                .order_by(WorkAgentSessionModel.agent_id.asc())
            ).all()
        return [
            {
                "id": mapping.id,
                "work_id": mapping.work_id,
                "agent_id": mapping.agent_id,
                "agent_name": agent_name,
                "agent_role": agent_role,
                "session_id": mapping.session_id,
                "session_updated_at": session_updated_at,
                "created_at": mapping.created_at,
                "updated_at": mapping.updated_at,
            }
            for mapping, agent_name, agent_role, session_updated_at in rows
        ]

    def find_work_id_by_session(self, *, agent_id: str, session_id: str) -> str | None:
        with session_scope(self.Session) as session:
            row = session.scalars(
                select(WorkAgentSessionModel)
                .where(WorkAgentSessionModel.agent_id == agent_id)
                .where(WorkAgentSessionModel.session_id == session_id)
                .limit(1)
            ).first()
        return None if row is None else row.work_id

    def list_session_ids_for_work(self, work_id: str) -> list[str]:
        with session_scope(self.Session) as session:
            rows = session.scalars(
                select(WorkAgentSessionModel.session_id)
                .where(WorkAgentSessionModel.work_id == work_id)
                .order_by(WorkAgentSessionModel.id.asc())
            ).all()
        return list(rows)

    def delete_work_sessions(self, work_id: str) -> None:
        with session_scope(self.Session) as session:
            session.execute(
                delete(WorkAgentSessionModel).where(WorkAgentSessionModel.work_id == work_id)
            )
            session.commit()


class ClassicActiveWorkRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def get_active_work(self, chat_session_id: str) -> dict[str, Any] | None:
        with session_scope(self.Session) as session:
            row = session.get(ClassicActiveWorkModel, chat_session_id)
        if row is None:
            return None
        return {
            "chat_session_id": row.chat_session_id,
            "work_id": row.work_id,
            "work_session_id": row.work_session_id,
            "root_request_id": row.root_request_id,
            "title": row.title,
            "summary": row.summary,
            "workflow_type": row.workflow_type,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def set_active_work(
        self,
        *,
        chat_session_id: str,
        work_id: str,
        work_session_id: str,
        root_request_id: str | None,
        title: str,
        summary: str | None,
        workflow_type: str | None,
    ) -> None:
        timestamp = now_iso()
        with session_scope(self.Session) as session:
            row = session.get(ClassicActiveWorkModel, chat_session_id)
            if row is None:
                session.add(
                    ClassicActiveWorkModel(
                        chat_session_id=chat_session_id,
                        work_id=work_id,
                        work_session_id=work_session_id,
                        root_request_id=root_request_id,
                        title=title,
                        summary=summary,
                        workflow_type=workflow_type,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            else:
                row.work_id = work_id
                row.work_session_id = work_session_id
                row.root_request_id = root_request_id
                row.title = title
                row.summary = summary
                row.workflow_type = workflow_type
                row.updated_at = timestamp
            session.commit()

    def clear_active_work(self, chat_session_id: str) -> None:
        with session_scope(self.Session) as session:
            row = session.get(ClassicActiveWorkModel, chat_session_id)
            if row is None:
                return
            session.delete(row)
            session.commit()


class TemporaryAgentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory

    def create_temporary_agent(self, record: TemporaryAgentModel) -> None:
        with session_scope(self.Session) as session:
            session.add(record)
            session.commit()

    def update_temporary_agent(
        self,
        temporary_agent_id: str,
        *,
        status: str | None = None,
        cleanup_reason: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        activated_at: str | None = None,
        cleaned_up_at: str | None = None,
    ) -> None:
        with session_scope(self.Session) as session:
            row = session.get(TemporaryAgentModel, temporary_agent_id)
            if row is None:
                return
            if status is not None:
                row.status = status
            if cleanup_reason is not None:
                row.cleanup_reason = cleanup_reason
            if metadata_json is not None:
                row.metadata_json = metadata_json
            if activated_at is not None:
                row.activated_at = activated_at
            if cleaned_up_at is not None:
                row.cleaned_up_at = cleaned_up_at
            row.updated_at = now_iso()
            session.commit()

    def list_temporary_agents(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        parent_task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with session_scope(self.Session) as session:
            stmt = (
                select(TemporaryAgentModel)
                .order_by(TemporaryAgentModel.created_at.desc())
                .limit(limit)
            )
            if session_id is not None:
                stmt = stmt.where(TemporaryAgentModel.session_id == session_id)
            if request_id is not None:
                stmt = stmt.where(TemporaryAgentModel.request_id == request_id)
            if parent_task_id is not None:
                stmt = stmt.where(TemporaryAgentModel.parent_task_id == parent_task_id)
            rows = session.scalars(stmt).all()
        records = [row.to_public_dict() for row in rows]
        records.reverse()
        return records

    def get_temporary_agent(self, temporary_agent_id: str) -> TemporaryAgentModel:
        with session_scope(self.Session) as session:
            row = session.get(TemporaryAgentModel, temporary_agent_id)
        if row is None:
            raise KeyError(f"Temporary agent not found: {temporary_agent_id}")
        return row


class ChanakyaStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.Session = session_factory
        self.chat = ChatRepository(session_factory)
        self.works = WorkRepository(session_factory)
        self.work_agent_sessions = WorkAgentSessionRepository(session_factory)
        self.classic_active_works = ClassicActiveWorkRepository(session_factory)
        self.requests = RequestRepository(session_factory)
        self.tasks = TaskRepository(session_factory)
        self.events = EventRepository(session_factory)
        self.session_contexts = AgentSessionContextRepository(session_factory)
        self.tools = ToolInvocationRepository(session_factory)
        self.agents = AgentProfileRepository(session_factory)
        self.temporary_agents = TemporaryAgentRepository(session_factory)

    def create_work(
        self,
        *,
        work_id: str,
        title: str,
        description: str | None = None,
        status: str = "active",
    ) -> None:
        self.works.create_work(
            work_id=work_id,
            title=title,
            description=description,
            status=status,
        )

    def list_works(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.works.list_works(limit=limit)

    def get_work(self, work_id: str) -> WorkModel:
        return self.works.get_work(work_id)

    def delete_work(self, work_id: str) -> list[str]:
        self.get_work(work_id)
        session_ids = self.work_agent_sessions.list_session_ids_for_work(work_id)
        request_ids: list[str] = []
        task_ids: list[str] = []
        with session_scope(self.Session) as session:
            if session_ids:
                request_ids = list(
                    session.scalars(
                        select(RequestModel.id).where(RequestModel.session_id.in_(session_ids))
                    ).all()
                )
                if request_ids:
                    task_ids = list(
                        session.scalars(
                            select(TaskModel.id).where(TaskModel.request_id.in_(request_ids))
                        ).all()
                    )
                if task_ids:
                    session.execute(
                        delete(TaskEventModel).where(TaskEventModel.task_id.in_(task_ids))
                    )
                    session.execute(
                        delete(TemporaryAgentModel).where(
                            TemporaryAgentModel.parent_task_id.in_(task_ids)
                        )
                    )
                session.execute(
                    delete(TaskEventModel).where(TaskEventModel.session_id.in_(session_ids))
                )
                session.execute(
                    delete(ChatMessageModel).where(ChatMessageModel.session_id.in_(session_ids))
                )
                session.execute(
                    delete(ToolInvocationModel).where(
                        ToolInvocationModel.session_id.in_(session_ids)
                    )
                )
                if task_ids:
                    session.execute(delete(TaskModel).where(TaskModel.id.in_(task_ids)))
                if request_ids:
                    session.execute(delete(RequestModel).where(RequestModel.id.in_(request_ids)))
                session.execute(
                    delete(ChatSessionModel).where(ChatSessionModel.id.in_(session_ids))
                )
            session.execute(
                delete(WorkAgentSessionModel).where(WorkAgentSessionModel.work_id == work_id)
            )
            session.execute(
                delete(ClassicActiveWorkModel).where(ClassicActiveWorkModel.work_id == work_id)
            )
            session.execute(delete(WorkModel).where(WorkModel.id == work_id))
            session.commit()
        for session_id in session_ids:
            self.session_contexts.delete(session_id)
        return session_ids

    def get_agent_session_context(
        self, session_id: str, *, target_key: str | None = None
    ) -> dict[str, Any]:
        return self.session_contexts.get(session_id, target_key=target_key)

    def save_agent_session_context(
        self,
        session_id: str,
        *,
        backend: str,
        remote_context_id: str | None,
        remote_agent_url: str | None,
        target_key: str | None = None,
    ) -> dict[str, Any]:
        return self.session_contexts.save(
            session_id,
            backend=backend,
            remote_context_id=remote_context_id,
            remote_agent_url=remote_agent_url,
            target_key=target_key,
        )

    def delete_agent_session_context(
        self, session_id: str, *, target_key: str | None = None
    ) -> None:
        self.session_contexts.delete(session_id, target_key=target_key)

    def get_active_classic_work(self, chat_session_id: str) -> dict[str, Any] | None:
        return self.classic_active_works.get_active_work(chat_session_id)

    def set_active_classic_work(self, **kwargs: Any) -> None:
        self.classic_active_works.set_active_work(**kwargs)

    def clear_active_classic_work(self, chat_session_id: str) -> None:
        self.classic_active_works.clear_active_work(chat_session_id)

    def _expand_session_ids(self, session_id: str | None) -> list[str]:
        if session_id is None:
            return []
        session_ids = [session_id]
        active_work = self.get_active_classic_work(session_id)
        if active_work is not None:
            work_session_id = str(active_work.get("work_session_id") or "").strip()
            if work_session_id and work_session_id not in session_ids:
                session_ids.append(work_session_id)
        return session_ids

    def ensure_work_agent_session(
        self,
        *,
        work_id: str,
        agent_id: str,
        session_id: str,
        session_title: str,
    ) -> str:
        return self.work_agent_sessions.ensure_work_agent_session(
            work_id=work_id,
            agent_id=agent_id,
            session_id=session_id,
            session_title=session_title,
        )

    def list_work_agent_sessions(self, work_id: str) -> list[dict[str, Any]]:
        return self.work_agent_sessions.list_work_agent_sessions(work_id)

    def list_session_ids_for_work(self, work_id: str) -> list[str]:
        return self.work_agent_sessions.list_session_ids_for_work(work_id)

    def find_work_id_by_session(self, *, agent_id: str, session_id: str) -> str | None:
        return self.work_agent_sessions.find_work_id_by_session(
            agent_id=agent_id,
            session_id=session_id,
        )

    def find_waiting_input_task(self, session_id: str) -> dict[str, Any] | None:
        tasks = self.list_tasks(session_id=session_id, limit=200)
        waiting_tasks = [
            task
            for task in tasks
            if task.get("status") == "waiting_input"
            and (task.get("input") or {}).get("maf_pending_request_id")
        ]
        if len(waiting_tasks) != 1:
            return None
        return waiting_tasks[-1]

    def create_session(self, session_id: str, title: str) -> None:
        self.chat.create_session(session_id, title)

    def ensure_session(self, session_id: str, title: str = "New chat") -> None:
        self.chat.ensure_session(session_id, title)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        request_id: str | None = None,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.chat.add_message(session_id, role, content, request_id, route, metadata)

    def rewrite_latest_assistant_message(
        self,
        session_id: str,
        *,
        content: str,
        request_id: str | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> None:
        self.chat.rewrite_latest_assistant_message(
            session_id,
            content=content,
            request_id=request_id,
            metadata_update=metadata_update,
        )

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        return self.chat.list_messages(session_id)

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.log_event(event_type, payload)

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.events.list_events(limit)

    def create_task_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self.events.create_task_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            request_id=request_id,
            task_id=task_id,
        )

    def list_task_events(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        session_ids = self._expand_session_ids(session_id)
        if len(session_ids) <= 1:
            return self.events.list_task_events(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                limit=limit,
            )
        merged: list[dict[str, Any]] = []
        seen: set[int] = set()
        for item_session_id in session_ids:
            for event in self.events.list_task_events(
                session_id=item_session_id,
                request_id=request_id,
                task_id=task_id,
                limit=limit,
            ):
                event_id = int(event["id"])
                if event_id in seen:
                    continue
                seen.add(event_id)
                merged.append(event)
        merged.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)))
        return merged[-limit:]

    def create_request(self, **kwargs: Any) -> None:
        self.requests.create_request(**kwargs)

    def update_request(self, request_id: str, **kwargs: Any) -> None:
        self.requests.update_request(request_id, **kwargs)

    def get_request(self, request_id: str) -> RequestModel:
        return self.requests.get_request(request_id)

    def list_requests(
        self, *, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        session_ids = self._expand_session_ids(session_id)
        if len(session_ids) <= 1:
            return self.requests.list_requests(session_id=session_id, limit=limit)
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item_session_id in session_ids:
            for request in self.requests.list_requests(session_id=item_session_id, limit=limit):
                request_id = str(request["id"])
                if request_id in seen:
                    continue
                seen.add(request_id)
                merged.append(request)
        merged.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
        return merged[-limit:]

    def create_task(self, **kwargs: Any) -> None:
        self.tasks.create_task(**kwargs)

    def update_task(self, task_id: str, **kwargs: Any) -> None:
        self.tasks.update_task(task_id, **kwargs)

    def get_task(self, task_id: str) -> TaskModel:
        return self.tasks.get_task(task_id)

    def list_tasks(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        root_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        session_ids = self._expand_session_ids(session_id)
        if len(session_ids) <= 1:
            return self.tasks.list_tasks(
                session_id=session_id,
                request_id=request_id,
                root_only=root_only,
                limit=limit,
            )
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item_session_id in session_ids:
            for task in self.tasks.list_tasks(
                session_id=item_session_id,
                request_id=request_id,
                root_only=root_only,
                limit=limit,
            ):
                task_id = str(task["id"])
                if task_id in seen:
                    continue
                seen.add(task_id)
                merged.append(task)
        merged.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
        return merged[-limit:]

    def list_task_children(
        self,
        parent_task_id: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.tasks.list_children(
            parent_task_id,
            session_id=session_id,
            request_id=request_id,
            limit=limit,
        )

    def create_tool_invocation(self, **kwargs: Any) -> None:
        self.tools.create_tool_invocation(**kwargs)

    def finish_tool_invocation(self, invocation_id: str, **kwargs: Any) -> None:
        self.tools.finish_tool_invocation(invocation_id, **kwargs)

    def list_tool_invocations(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        session_ids = self._expand_session_ids(session_id)
        if len(session_ids) <= 1:
            return self.tools.list_tool_invocations(
                session_id=session_id,
                request_id=request_id,
                limit=limit,
            )
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item_session_id in session_ids:
            for trace in self.tools.list_tool_invocations(
                session_id=item_session_id,
                request_id=request_id,
                limit=limit,
            ):
                invocation_id = str(trace["invocation_id"])
                if invocation_id in seen:
                    continue
                seen.add(invocation_id)
                merged.append(trace)
        merged.sort(
            key=lambda item: (
                str(item.get("started_at") or ""),
                str(item.get("invocation_id") or ""),
            )
        )
        return merged[-limit:]

    def upsert_agent_profile(self, profile: AgentProfileModel) -> None:
        self.agents.upsert_agent_profile(profile)

    def create_agent_profile(self, profile: AgentProfileModel) -> None:
        self.agents.create_agent_profile(profile)

    def update_agent_profile(self, agent_id: str, **kwargs: Any) -> AgentProfileModel:
        return self.agents.update_agent_profile(agent_id, **kwargs)

    def has_agent_profile(self, agent_id: str) -> bool:
        return self.agents.has_agent_profile(agent_id)

    def list_agent_profiles(self) -> list[AgentProfileModel]:
        return self.agents.list_agent_profiles()

    def get_agent_profile(self, agent_id: str) -> AgentProfileModel:
        return self.agents.get_agent_profile(agent_id)

    def find_active_agents_by_role(self, role: str) -> list[AgentProfileModel]:
        return self.agents.find_active_agents_by_role(role)

    def create_temporary_agent(self, record: TemporaryAgentModel) -> None:
        self.temporary_agents.create_temporary_agent(record)

    def update_temporary_agent(self, temporary_agent_id: str, **kwargs: Any) -> None:
        self.temporary_agents.update_temporary_agent(temporary_agent_id, **kwargs)

    def list_temporary_agents(
        self,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        parent_task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        session_ids = self._expand_session_ids(session_id)
        if len(session_ids) <= 1:
            return self.temporary_agents.list_temporary_agents(
                session_id=session_id,
                request_id=request_id,
                parent_task_id=parent_task_id,
                limit=limit,
            )
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item_session_id in session_ids:
            for agent in self.temporary_agents.list_temporary_agents(
                session_id=item_session_id,
                request_id=request_id,
                parent_task_id=parent_task_id,
                limit=limit,
            ):
                agent_id = str(agent["id"])
                if agent_id in seen:
                    continue
                seen.add(agent_id)
                merged.append(agent)
        merged.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
        return merged[-limit:]

    def get_temporary_agent(self, temporary_agent_id: str) -> TemporaryAgentModel:
        return self.temporary_agents.get_temporary_agent(temporary_agent_id)
