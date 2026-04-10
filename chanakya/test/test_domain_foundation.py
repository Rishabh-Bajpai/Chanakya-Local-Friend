from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from agent_framework import Message

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
from chanakya.history_provider import SQLAlchemyHistoryProvider
from chanakya.services.async_loop import run_in_maf_loop
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

    def runtime_metadata(self, model_id: str | None = None) -> dict[str, str | None]:
        return {"model": "test-model", "endpoint": "http://test", "runtime": "maf_agent"}

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
    ) -> _RunResult:
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


def test_chat_post_processes_visible_assistant_message() -> None:
    class _PostProcessorStub:
        enabled = True

        def wrap_reply(
            self,
            *,
            session_id: str,
            user_message: str,
            assistant_message: str,
            model_id: str | None = None,
            metadata: dict[str, str] | None = None,
        ):
            return type(
                "Wrapped",
                (),
                {
                    "response": f"layered:{assistant_message}",
                    "messages": [{"text": f"layered:{assistant_message}", "delay_ms": 0}],
                    "metadata": {"pending_delivery_count": 0, "source": "conversation_layer"},
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))
    service._conversation_layer = _PostProcessorStub()  # type: ignore[attr-defined]

    reply = service.chat("session_layered", "Explain recursion")

    messages = store.list_messages("session_layered")
    assert messages[1]["content"] == "layered:reply:Explain recursion"
    assert messages[1]["metadata"]["conversation_layer_applied"] is True
    assert reply.message == "layered:reply:Explain recursion"


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


def test_history_provider_filters_control_json_messages() -> None:
    row = type(
        "Row",
        (),
        {
            "role": "assistant",
            "content": '{"should_create_subagents": false, "reason": "not needed"}',
            "metadata_json": {},
        },
    )()
    assert SQLAlchemyHistoryProvider._is_control_history_row(row) is True

    normal_row = type(
        "Row",
        (),
        {
            "role": "assistant",
            "content": "Here is the final report.",
            "metadata_json": {},
        },
    )()
    assert SQLAlchemyHistoryProvider._is_control_history_row(normal_row) is False


def test_history_provider_compresses_history_with_relevance_and_recency() -> None:
    rows = [
        type(
            "Row", (), {"content": "old unrelated note", "role": "assistant", "metadata_json": {}}
        )(),
        type(
            "Row",
            (),
            {
                "content": "billing retry policy and timeout handling",
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
        type(
            "Row",
            (),
            {"content": "another unrelated item", "role": "assistant", "metadata_json": {}},
        )(),
        type(
            "Row", (), {"content": "latest user follow-up", "role": "user", "metadata_json": {}}
        )(),
        type(
            "Row",
            (),
            {"content": "latest assistant reply", "role": "assistant", "metadata_json": {}},
        )(),
    ]

    selected = SQLAlchemyHistoryProvider._compress_history_rows(
        rows,
        query_text="help with billing retry",
        recent_window=2,
        max_messages=3,
        max_chars=2000,
        max_message_chars=500,
    )

    texts = [content for _, content in selected]
    assert any("billing retry policy" in text for text in texts)
    assert any("latest user follow-up" in text for text in texts)
    assert any("latest assistant reply" in text for text in texts)


def test_history_provider_enforces_character_budgets() -> None:
    rows = [
        type(
            "Row",
            (),
            {
                "content": "A" * 500,
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
        type(
            "Row",
            (),
            {
                "content": "B" * 500,
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
    ]

    selected = SQLAlchemyHistoryProvider._compress_history_rows(
        rows,
        query_text="",
        recent_window=2,
        max_messages=10,
        max_chars=320,
        max_message_chars=180,
    )

    assert selected
    combined = "".join(content for _, content in selected)
    assert len(combined) <= 323
    assert all(len(content) <= 183 for _, content in selected)


def test_history_context_stats_are_persisted_in_message_metadata() -> None:
    store = _build_store()
    provider = SQLAlchemyHistoryProvider(store.Session)

    run_in_maf_loop(
        provider.save_messages(
            "session_hist_stats",
            [Message(role="assistant", text="Final answer")],
            state={
                "request_id": "req_hist_stats",
                "history_context_stats": {
                    "available_messages": 12,
                    "selected_messages": 5,
                    "selected_chars": 980,
                    "relevance_hits": 2,
                    "backfill_hits": 1,
                    "truncated_messages": 0,
                    "query_text": "implement billing retry",
                },
            },
        )
    )

    messages = store.list_messages("session_hist_stats")
    assert len(messages) == 1
    metadata = messages[0]["metadata"]
    assert "history_context" in metadata
    assert metadata["history_context"]["selected_messages"] == 5
    assert metadata["history_context"]["relevance_hits"] == 2
