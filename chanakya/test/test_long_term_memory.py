from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.model import AgentProfileModel
from chanakya.services.long_term_memory import run_memory_update_job
from chanakya.services.memory_manager_service import MemoryManagerResult, MemoryManagerService
from chanakya.store import ChanakyaStore


@dataclass
class _Trace:
    tool_id: str
    tool_name: str
    server_name: str
    status: str


@dataclass
class _RunResult:
    text: str
    response_mode: str
    tool_traces: list[_Trace]


class _RuntimeStub:
    def __init__(self) -> None:
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
            created_at="2026-04-28T00:00:00+00:00",
            updated_at="2026-04-28T00:00:00+00:00",
        )
        self.last_prompt_addendum: str | None = None

    def runtime_metadata(self, **kwargs: str | None) -> dict[str, str | None]:
        return {
            "model": "test-model",
            "endpoint": "http://test",
            "runtime": "maf_agent",
            "backend": str(kwargs.get("backend") or "local"),
        }

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        prompt_addendum: str | None = None,
        **kwargs: str | None,
    ) -> _RunResult:
        self.last_prompt_addendum = prompt_addendum
        return _RunResult(text=f"reply:{text}", response_mode="direct_answer", tool_traces=[])

    def clear_session_state(self, session_id: str) -> None:
        return None


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def test_long_term_memory_service_creates_project_memory_from_agent_result(monkeypatch) -> None:
    store = _build_store()

    store.create_session("session_1", "Test")
    store.add_message(
        "session_1",
        "user",
        "My app uses Microsoft Agent Framework and MCP tools.",
        request_id="req_1",
    )
    store.add_message("session_1", "assistant", "Thanks", request_id="req_1")

    monkeypatch.setattr(
        MemoryManagerService,
        "_run_memory_manager",
        lambda self, prompt_text, session_id: MemoryManagerResult(
            status="ok",
            summary="Stored project context.",
            needs_clarification=False,
            clarification_question=None,
            retryable=False,
            error_code=None,
            error_detail=None,
            operations=[
                {
                    "op": "add",
                    "memory_id": None,
                    "scope": "shared",
                    "type": "project",
                    "subject": "project context",
                    "content": "User app uses Microsoft Agent Framework and MCP tools.",
                    "importance": 4,
                    "confidence": 0.94,
                }
            ],
        ),
    )

    run_memory_update_job(store, session_id="session_1", request_id="req_1")

    memories = store.list_memories(owner_id="default_user", session_id="session_1")
    assert len(memories) == 1
    assert memories[0]["type"] == "project"
    assert "Microsoft Agent Framework" in memories[0]["content"]


def test_chat_service_injects_relevant_long_term_memory_into_prompt_addendum() -> None:
    store = _build_store()
    runtime = _RuntimeStub()
    service = ChatService(store, cast(Any, runtime))

    store.create_memory(
        memory_id="memory_1",
        owner_id="default_user",
        session_id="session_1",
        scope="shared",
        type="project",
        subject="project context",
        content="My app uses Microsoft Agent Framework with MCP tools.",
        importance=4,
        confidence=0.9,
    )

    service._schedule_long_term_memory_update = lambda **kwargs: None  # type: ignore[method-assign]
    reply = service.chat("session_1", "Can you help improve my app architecture?")

    assert reply.message == "reply:Can you help improve my app architecture?"
    assert runtime.last_prompt_addendum is not None
    assert "Relevant long-term memory:" in runtime.last_prompt_addendum
    assert "Microsoft Agent Framework" in runtime.last_prompt_addendum


def test_chat_service_background_memory_update_can_be_run_inline_without_affecting_reply(monkeypatch) -> None:
    store = _build_store()
    runtime = _RuntimeStub()
    service = ChatService(store, cast(Any, runtime))
    monkeypatch.setattr(
        MemoryManagerService,
        "_run_memory_manager",
        lambda self, prompt_text, session_id: MemoryManagerResult(
            status="ok",
            summary="Stored user preference.",
            needs_clarification=False,
            clarification_question=None,
            retryable=False,
            error_code=None,
            error_detail=None,
            operations=[
                {
                    "op": "add",
                    "memory_id": None,
                    "scope": "user",
                    "type": "preference",
                    "subject": "user preferences",
                    "content": "User prefers practical architecture recommendations.",
                    "importance": 4,
                    "confidence": 0.93,
                }
            ],
        ),
    )
    service._schedule_long_term_memory_update = (  # type: ignore[method-assign]
        lambda **kwargs: run_memory_update_job(store, **kwargs)
    )

    reply = service.chat(
        "session_1",
        "Remember that I prefer practical architecture recommendations.",
    )

    assert reply.message == "reply:Remember that I prefer practical architecture recommendations."
    memories = store.list_memories(owner_id="default_user", session_id="session_1")
    assert len(memories) == 1
    assert memories[0]["type"] == "preference"
    assert "practical architecture recommendations" in memories[0]["content"]


def test_memory_manager_handles_explicit_memory_request(monkeypatch) -> None:
    store = _build_store()
    store.create_memory(
        memory_id="memory_existing",
        owner_id="default_user",
        session_id="session_1",
        scope="user",
        type="profile",
        subject="user name",
        content="User name is Rishabh.",
        importance=5,
        confidence=0.95,
    )

    monkeypatch.setattr(
        MemoryManagerService,
        "_run_memory_manager",
        lambda self, prompt_text, session_id: MemoryManagerResult(
            status="ok",
            summary="Updated the user's full name.",
            needs_clarification=False,
            clarification_question=None,
            retryable=False,
            error_code=None,
            error_detail=None,
            operations=[
                {
                    "op": "update",
                    "memory_id": "memory_existing",
                    "scope": "user",
                    "type": "profile",
                    "subject": "user name",
                    "content": "User name is Rishabh Bajpai.",
                    "importance": 5,
                    "confidence": 0.99,
                }
            ],
        ),
    )

    result = MemoryManagerService(store).handle_memory_request(
        memory_request='{"session_id":"session_1","request":"Remember that my full name is Rishabh Bajpai."}'
    )

    assert result["status"] == "ok"
    memories = store.list_memories(owner_id="default_user", session_id="session_1")
    assert len(memories) == 1
    assert memories[0]["content"] == "User name is Rishabh Bajpai."


def test_memory_manager_request_envelope_uses_text_field(monkeypatch) -> None:
    store = _build_store()

    monkeypatch.setattr(
        MemoryManagerService,
        "_run_memory_manager",
        lambda self, prompt_text, session_id: MemoryManagerResult(
            status="ok",
            summary="Stored a project fact.",
            needs_clarification=False,
            clarification_question=None,
            retryable=False,
            error_code=None,
            error_detail=None,
            operations=[
                {
                    "op": "add",
                    "memory_id": None,
                    "scope": "shared",
                    "type": "project",
                    "subject": "project context",
                    "content": "User site says they are a postdoctoral researcher.",
                    "importance": 4,
                    "confidence": 0.95,
                }
            ],
        ),
    )

    result = MemoryManagerService(store).handle_memory_request(
        memory_request='{"request":"add","text":"User site says they are a postdoctoral researcher.","session_id":"session_1"}'
    )

    assert result["status"] == "ok"
    memories = store.list_memories(owner_id="default_user", session_id="session_1")
    assert len(memories) == 1
    assert "postdoctoral researcher" in memories[0]["content"]


def test_memory_manager_failure_is_recorded(monkeypatch) -> None:
    store = _build_store()
    store.create_session("session_1", "Test")
    store.add_message(
        "session_1",
        "user",
        "Remember my website details.",
        request_id="req_fail_1",
    )
    store.add_message("session_1", "assistant", "I'll try.", request_id="req_fail_1")

    monkeypatch.setattr(
        MemoryManagerService,
        "_run_memory_manager",
        lambda self, prompt_text, session_id: MemoryManagerResult(
            status="failed",
            summary="Memory manager could not parse the request.",
            needs_clarification=False,
            clarification_question=None,
            retryable=True,
            error_code="parse_failed",
            error_detail="The memory request envelope was malformed.",
            operations=[],
        ),
    )

    run_memory_update_job(store, session_id="session_1", request_id="req_fail_1")

    events = store.list_memory_events(
        owner_id="default_user",
        session_id="session_1",
        request_id="req_fail_1",
    )
    assert events[-1]["event_type"] == "memory_extraction_failed"
    assert events[-1]["payload"]["retryable"] is True
    assert events[-1]["payload"]["error_code"] == "parse_failed"
