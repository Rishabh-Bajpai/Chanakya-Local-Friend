from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from chanakya.domain import now_iso


class Base(DeclarativeBase):
    pass


class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class AgentSessionContextModel(Base):
    __tablename__ = "agent_session_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    backend: Mapped[str] = mapped_column(String, nullable=False)
    remote_context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_agent_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RuntimeConfigModel(Base):
    __tablename__ = "runtime_config"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    backend: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    a2a_url: Mapped[str | None] = mapped_column(String, nullable=True)
    a2a_remote_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    a2a_model_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    a2a_model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_tone_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    tts_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class WorkModel(Base):
    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class WorkAgentSessionModel(Base):
    __tablename__ = "work_agent_sessions"
    __table_args__ = (UniqueConstraint("work_id", "agent_id", name="uq_work_agent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ClassicActiveWorkModel(Base):
    __tablename__ = "classic_active_works"

    chat_session_id: Mapped[str] = mapped_column(String, primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"), nullable=False, index=True)
    work_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    root_request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    route: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    session: Mapped[ChatSessionModel] = relationship(back_populates="messages")


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    work_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_agent_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AppEventModel(Base):
    __tablename__ = "app_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class NotificationSettingsModel(Base):
    __tablename__ = "notification_settings"

    channel_type: Mapped[str] = mapped_column(String, primary_key=True)
    server_url: Mapped[str] = mapped_column(String, nullable=False)
    topic: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_message_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RequestModel(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    root_task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), nullable=False, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String, nullable=False, default="chat_request")
    dependencies_json: Mapped[list[str]] = mapped_column("dependencies", JSON, default=list)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class TaskEventModel(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)


class ToolInvocationModel(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invocation_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    tool_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    server_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentProfileModel(Base):
    __tablename__ = "agent_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    system_prompt: Mapped[str] = mapped_column(String, nullable=False)
    personality: Mapped[str] = mapped_column(String, nullable=False)
    tool_ids_json: Mapped[list[str]] = mapped_column("tool_ids", JSON, default=list)
    workspace: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    heartbeat_file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    @classmethod
    def from_seed(cls, item: dict[str, Any]) -> "AgentProfileModel":
        timestamp = now_iso()
        return cls(
            id=str(item["id"]),
            name=str(item["name"]),
            role=str(item["role"]),
            system_prompt=str(item["system_prompt"]),
            personality=str(item.get("personality", "")),
            tool_ids_json=list(item.get("tool_ids", [])),
            workspace=item.get("workspace"),
            heartbeat_enabled=bool(item.get("heartbeat_enabled", False)),
            heartbeat_interval_seconds=int(item.get("heartbeat_interval_seconds", 300)),
            heartbeat_file_path=item.get("heartbeat_file_path"),
            is_active=bool(item.get("is_active", True)),
            created_at=timestamp,
            updated_at=timestamp,
        )

    def update_from_seed(self, item: dict[str, Any]) -> None:
        self.name = str(item["name"])
        self.role = str(item["role"])
        self.system_prompt = str(item["system_prompt"])
        self.personality = str(item.get("personality", ""))
        self.tool_ids_json = list(item.get("tool_ids", []))
        self.workspace = item.get("workspace")
        self.heartbeat_enabled = bool(item.get("heartbeat_enabled", False))
        self.heartbeat_interval_seconds = int(item.get("heartbeat_interval_seconds", 300))
        self.heartbeat_file_path = item.get("heartbeat_file_path")
        self.is_active = bool(item.get("is_active", True))
        self.updated_at = now_iso()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "system_prompt": self.system_prompt,
            "personality": self.personality,
            "tool_ids": self.tool_ids_json,
            "workspace": self.workspace,
            "heartbeat_enabled": self.heartbeat_enabled,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "heartbeat_file_path": self.heartbeat_file_path,
            "is_active": self.is_active,
        }


class WorkNotificationModel(Base):
    __tablename__ = "work_notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id"), nullable=False, index=True
    )
    notification_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str | None] = mapped_column(String, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, index=True)


class TemporaryAgentModel(Base):
    __tablename__ = "temporary_agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    creator_role: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tool_ids_json: Mapped[list[str]] = mapped_column("tool_ids", JSON, default=list)
    workspace: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cleanup_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    activated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cleaned_up_at: Mapped[str | None] = mapped_column(String, nullable=True)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "parent_agent_id": self.parent_agent_id,
            "parent_task_id": self.parent_task_id,
            "creator_role": self.creator_role,
            "name": self.name,
            "role": self.role,
            "purpose": self.purpose,
            "system_prompt": self.system_prompt,
            "tool_ids": self.tool_ids_json,
            "workspace": self.workspace,
            "status": self.status,
            "cleanup_reason": self.cleanup_reason,
            "metadata": self.metadata_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "activated_at": self.activated_at,
            "cleaned_up_at": self.cleaned_up_at,
        }
