from __future__ import annotations

import threading
import time

from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import ChatReply, TASK_STATUS_IN_PROGRESS
from chanakya.model import AgentProfileModel
from chanakya.services.mcp_work_tools_server import (
    _create_work,
    _create_work_with_message,
    _get_pending_work_messages,
    _send_message_to_work,
)
from chanakya.store import ChanakyaStore


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    return ChanakyaStore(build_session_factory(engine))


class _DummyChatService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def chat(self, session_id: str, message: str, *, work_id: str | None = None) -> ChatReply:
        self.calls.append((session_id, message, work_id))
        return ChatReply(
            request_id="req_123",
            session_id=session_id,
            work_id=work_id,
            route="delegated_manager",
            message="Processing started.",
            model=None,
            endpoint=None,
            runtime="maf_agent",
            agent_name="Chanakya",
            root_task_id="task_123",
            root_task_status=TASK_STATUS_IN_PROGRESS,
        )


class _SlowChatService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def chat(self, session_id: str, message: str, *, work_id: str | None = None) -> ChatReply:
        self.calls.append((session_id, message, work_id))
        self.started.set()
        self.release.wait(timeout=5)
        return ChatReply(
            request_id="req_slow",
            session_id=session_id,
            work_id=work_id,
            route="delegated_manager",
            message="Processing started.",
            model=None,
            endpoint=None,
            runtime="maf_agent",
            agent_name="Chanakya",
            root_task_id="task_slow",
            root_task_status=TASK_STATUS_IN_PROGRESS,
        )


def _seed_active_agents(store: ChanakyaStore) -> None:
    store.create_agent_profile(
        AgentProfileModel.from_seed(
            {
                "id": "agent_chanakya",
                "name": "Chanakya",
                "role": "personal_assistant",
                "system_prompt": "prompt",
                "personality": "calm",
                "tool_ids": [],
                "workspace": "main",
                "heartbeat_enabled": False,
                "heartbeat_interval_seconds": 300,
                "heartbeat_file_path": None,
                "is_active": True,
            }
        )
    )
    store.create_agent_profile(
        AgentProfileModel.from_seed(
            {
                "id": "agent_manager",
                "name": "Agent Manager",
                "role": "manager",
                "system_prompt": "prompt",
                "personality": "structured",
                "tool_ids": [],
                "workspace": "manager",
                "heartbeat_enabled": False,
                "heartbeat_interval_seconds": 300,
                "heartbeat_file_path": None,
                "is_active": True,
            }
        )
    )


def test_create_work_creates_active_work_and_agent_sessions() -> None:
    store = _build_store()
    _seed_active_agents(store)

    result = _create_work(store, title="New Work", description="Ship it")

    assert result["ok"] is True
    assert result["title"] == "New Work"
    assert result["description"] == "Ship it"
    assert result["status"] == "active"
    assert result["agent_session_count"] == 2
    work_id = result["id"]
    work = store.get_work(work_id)
    assert work.title == "New Work"
    assert work.status == "active"
    sessions = store.list_work_agent_sessions(work_id)
    assert len(sessions) == 2


def test_create_work_rejects_empty_title() -> None:
    store = _build_store()

    result = _create_work(store, title="   ", description="ignored")

    assert result["ok"] is False
    assert result["error"] == "title is required"
    assert "Retry with a short concrete work title" in result["hint"]


def test_list_works_can_filter_to_active_status() -> None:
    store = _build_store()
    store.create_work(work_id="work_active", title="Active", description="", status="active")
    store.create_work(work_id="work_done", title="Done", description="", status="done")

    active = store.list_works(status="active")

    assert [item["id"] for item in active] == ["work_active"]


def test_create_work_with_message_creates_work_and_dispatches_initial_message() -> None:
    store = _build_store()
    _seed_active_agents(store)
    chat_service = _DummyChatService()

    result = _create_work_with_message(
        store,
        chat_service,
        title="Build dashboard",
        description="Create the first dashboard draft",
        message="Create a dashboard with filters and export support.",
    )

    assert result["ok"] is True
    assert result["title"] == "Build dashboard"
    assert result["status"] == "active"
    assert result["agent_session_count"] == 2
    assert result["message"] == (
        'Created work "Build dashboard" and sent the initial request successfully.'
    )
    assert len(chat_service.calls) == 1
    session_id, forwarded_message, forwarded_work_id = chat_service.calls[0]
    assert session_id == result["session_id"]
    assert forwarded_message == "Create a dashboard with filters and export support."
    assert forwarded_work_id == result["id"]


def test_create_work_with_message_rejects_empty_message() -> None:
    store = _build_store()
    _seed_active_agents(store)
    chat_service = _DummyChatService()

    result = _create_work_with_message(
        store,
        chat_service,
        title="Build dashboard",
        description="Create the first dashboard draft",
        message="   ",
    )

    assert result["ok"] is False
    assert result["error"] == "message is required"
    assert "exact initial request" in result["hint"]
    assert store.list_works() == []


def test_send_message_to_work_routes_through_chat_service() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    chat_service = _DummyChatService()

    result = _send_message_to_work(
        store,
        chat_service,
        work_id="work_1",
        message="Please continue the report.",
    )

    assert result["ok"] is True
    assert result["work_id"] == "work_1"
    assert result["work_title"] == "Test Work"
    assert result["message"] == 'Message sent successfully to "Test Work".'
    deadline = time.time() + 2
    while len(chat_service.calls) != 1 and time.time() < deadline:
        time.sleep(0.01)
    assert len(chat_service.calls) == 1
    session_id, forwarded_message, forwarded_work_id = chat_service.calls[0]
    assert session_id == result["session_id"]
    assert forwarded_message == "Please continue the report."
    assert forwarded_work_id == "work_1"


def test_send_message_to_work_returns_before_background_chat_finishes() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    chat_service = _SlowChatService()

    started_at = time.monotonic()
    result = _send_message_to_work(
        store,
        chat_service,
        work_id="work_1",
        message="Please continue the report.",
    )
    elapsed = time.monotonic() - started_at

    assert result["ok"] is True
    assert result["work_title"] == "Test Work"
    assert result["message"] == 'Message sent successfully to "Test Work".'
    assert elapsed < 0.2
    assert chat_service.started.wait(timeout=1)
    chat_service.release.set()


def test_send_message_to_work_rejects_missing_work() -> None:
    store = _build_store()
    chat_service = _DummyChatService()

    result = _send_message_to_work(
        store,
        chat_service,
        work_id="missing",
        message="Please continue.",
    )

    assert result["ok"] is False
    assert "Wrong work ID" in result["error"]
    assert "list_works" in result["hint"]
    assert chat_service.calls == []


def test_send_message_to_work_missing_work_includes_candidates() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    chat_service = _DummyChatService()

    result = _send_message_to_work(
        store,
        chat_service,
        work_id="missing",
        message="Please continue.",
    )

    assert result["ok"] is False
    assert result["available_works"][0]["id"] == "work_1"


def test_send_message_to_work_requires_non_empty_message() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    chat_service = _DummyChatService()

    result = _send_message_to_work(store, chat_service, work_id="work_1", message="   ")

    assert result["ok"] is False
    assert result["error"] == "message is required"
    assert "exact message" in result["hint"]
    assert chat_service.calls == []


def test_get_pending_work_messages_returns_notifications() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    store.work_notifications.create_notification(
        notification_id="wn_1",
        work_id="work_1",
        notification_type="completed",
        title="Work completed",
        text="Finished successfully.",
    )

    result = _get_pending_work_messages(store)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["notifications"][0]["id"] == "wn_1"
    assert result["notifications"][0]["work_id"] == "work_1"
