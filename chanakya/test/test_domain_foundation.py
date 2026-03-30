from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from chanakya.agent.runtime import MAFRuntime
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import (
    REQUEST_STATUS_COMPLETED,
    REQUEST_STATUS_FAILED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
)
from chanakya.model import AgentProfileModel
from chanakya.store import ChanakyaStore


@dataclass
class _Trace:
    tool_id: str
    tool_name: str
    server_name: str
    status: str
    input_payload: str | None = None
    output_text: str | None = None
    error_text: str | None = None


@dataclass
class _RunResult:
    text: str
    response_mode: str
    tool_traces: list[_Trace]


class _RuntimeStub:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.profile = AgentProfileModel(
            id="agent_chanakya",
            name="Chanakya",
            role="assistant",
            system_prompt="test",
            personality="",
            tool_ids_json=[],
            workspace=None,
            heartbeat_enabled=False,
            heartbeat_interval_seconds=300,
            heartbeat_file_path=None,
            is_active=True,
            created_at="2026-03-29T00:00:00+00:00",
            updated_at="2026-03-29T00:00:00+00:00",
        )
        self.should_fail = should_fail

    def runtime_metadata(self) -> dict[str, str | None]:
        return {"model": "test-model", "endpoint": "http://test", "runtime": "maf_agent"}

    def run(self, session_id: str, text: str, *, request_id: str) -> _RunResult:
        if self.should_fail:
            raise RuntimeError("runtime exploded")
        return _RunResult(
            text=f"reply:{text}",
            response_mode="direct_answer",
            tool_traces=[],
        )


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def test_chat_persists_request_root_task_and_timeline() -> None:
    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))

    reply = service.chat("session_1", "Implement milestone 3")

    requests = store.list_requests(session_id="session_1")
    assert len(requests) == 1
    assert requests[0]["id"] == reply.request_id
    assert requests[0]["status"] == REQUEST_STATUS_COMPLETED
    assert requests[0]["root_task_id"] == reply.root_task_id

    tasks = store.list_tasks(session_id="session_1", root_only=True)
    assert len(tasks) == 1
    assert tasks[0]["id"] == reply.root_task_id
    assert tasks[0]["status"] == TASK_STATUS_DONE
    assert tasks[0]["result"]["message"] == "reply:Implement milestone 3"

    task_events = store.list_task_events(session_id="session_1")
    event_types = [item["event_type"] for item in task_events]
    assert event_types == [
        "request_received",
        "task_created",
        "task_status_changed",
        "response_persisted",
        "task_status_changed",
    ]

    messages = store.list_messages("session_1")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["root_task_id"] == reply.root_task_id


def test_chat_failure_marks_request_and_task_failed() -> None:
    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(should_fail=True)))

    try:
        service.chat("session_2", "This should fail")
    except RuntimeError as exc:
        assert str(exc) == "runtime exploded"
    else:
        raise AssertionError("expected runtime error")

    requests = store.list_requests(session_id="session_2")
    assert len(requests) == 1
    assert requests[0]["status"] == REQUEST_STATUS_FAILED

    tasks = store.list_tasks(session_id="session_2", root_only=True)
    assert len(tasks) == 1
    assert tasks[0]["status"] == TASK_STATUS_FAILED
    assert tasks[0]["error"] == "runtime exploded"

    task_events = store.list_task_events(session_id="session_2")
    assert task_events[-1]["event_type"] == "task_status_changed"
    assert task_events[-1]["payload"]["to_status"] == TASK_STATUS_FAILED


def test_update_task_preserves_error_until_non_failed_transition() -> None:
    store = _build_store()
    store.create_request(
        request_id="req_1",
        session_id="session_3",
        user_message="Investigate failure",
        status="created",
        root_task_id="task_1",
    )
    store.create_task(
        task_id="task_1",
        request_id="req_1",
        parent_task_id=None,
        title="Investigate failure",
        summary=None,
        status="created",
        owner_agent_id="agent_chanakya",
        task_type="chat_request",
    )

    store.update_task("task_1", status=TASK_STATUS_FAILED, error_text="boom")
    store.update_task("task_1", status=TASK_STATUS_FAILED)
    assert store.list_tasks(session_id="session_3", root_only=True)[0]["error"] == "boom"

    store.update_task("task_1", status=TASK_STATUS_IN_PROGRESS)
    assert store.list_tasks(session_id="session_3", root_only=True)[0]["error"] is None
