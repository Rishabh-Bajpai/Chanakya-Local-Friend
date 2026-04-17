from __future__ import annotations

from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import ChatReply, TASK_STATUS_IN_PROGRESS
from chanakya.services.mcp_work_tools_server import _send_message_to_work
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
    assert result["request_id"] == "req_123"
    assert result["root_task_id"] == "task_123"
    assert result["task_status"] == TASK_STATUS_IN_PROGRESS
    assert result["message"] == "Message delivered to work and processing started."
    assert len(chat_service.calls) == 1
    session_id, forwarded_message, forwarded_work_id = chat_service.calls[0]
    assert session_id == result["session_id"]
    assert forwarded_message == "Please continue the report."
    assert forwarded_work_id == "work_1"


def test_send_message_to_work_rejects_missing_work() -> None:
    store = _build_store()
    chat_service = _DummyChatService()

    result = _send_message_to_work(
        store,
        chat_service,
        work_id="missing",
        message="Please continue.",
    )

    assert result == {"ok": False, "error": "Work not found: missing"}
    assert chat_service.calls == []


def test_send_message_to_work_requires_non_empty_message() -> None:
    store = _build_store()
    store.create_work(work_id="work_1", title="Test Work", description="")
    chat_service = _DummyChatService()

    result = _send_message_to_work(store, chat_service, work_id="work_1", message="   ")

    assert result == {"ok": False, "error": "message is required"}
    assert chat_service.calls == []
