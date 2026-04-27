from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agent_framework import Message

from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager, RuntimeGroupChatTrace
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_WAITING_INPUT
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
    def __init__(self, profile: AgentProfileModel) -> None:
        self.profile = profile

    def runtime_metadata(
        self,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> dict[str, str | None]:
        return {
            "model": model_id or "test-model",
            "endpoint": "http://test",
            "runtime": "maf_agent",
            "backend": backend or "local",
        }

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        prompt_addendum: str | None = None,
    ) -> _RunResult:
        return _RunResult(text=f"{self.profile.role}:{text}", response_mode="direct_answer", tool_traces=[])

    def clear_session_state(self, session_id: str) -> None:
        return None


class _FakeWorkflowResult(list):
    def __init__(self, outputs: list[Any]) -> None:
        super().__init__()
        self._outputs = outputs

    def get_outputs(self) -> list[Any]:
        return list(self._outputs)


class _FakeWorkflow:
    def __init__(self, builder) -> None:
        self._builder = builder

    async def run(self, message=None, include_status_events: bool = False, **kwargs):
        seeded = list(message or [])
        return _FakeWorkflowResult([self._builder(seeded)])


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def _seed_agent(
    store: ChanakyaStore,
    agent_id: str,
    name: str,
    role: str,
    *,
    tool_ids: list[str] | None = None,
) -> AgentProfileModel:
    profile = AgentProfileModel(
        id=agent_id,
        name=name,
        role=role,
        system_prompt=f"You are {name}",
        personality="",
        tool_ids_json=tool_ids or [],
        workspace=None,
        heartbeat_enabled=False,
        heartbeat_interval_seconds=300,
        heartbeat_file_path=None,
        is_active=True,
        created_at="2026-03-31T00:00:00+00:00",
        updated_at="2026-03-31T00:00:00+00:00",
    )
    store.upsert_agent_profile(profile)
    return profile


def _seed_full_hierarchy(store: ChanakyaStore) -> tuple[AgentProfileModel, AgentProfileModel]:
    chanakya = _seed_agent(store, "agent_chanakya", "Chanakya", "personal_assistant")
    manager = _seed_agent(store, "agent_manager", "Agent Manager", "manager")
    _seed_agent(store, "agent_cto", "CTO", "cto")
    _seed_agent(store, "agent_informer", "Informer", "informer")
    _seed_agent(store, "agent_developer", "Developer", "developer")
    _seed_agent(store, "agent_tester", "Tester", "tester")
    _seed_agent(store, "agent_researcher", "Researcher", "researcher")
    _seed_agent(store, "agent_writer", "Writer", "writer")
    return chanakya, manager


def _create_work_with_sessions(store: ChanakyaStore, work_id: str = "work_gc") -> str:
    store.create_work(work_id=work_id, title="Group Chat Work", description="", status="active")
    for profile in store.list_agent_profiles():
        store.ensure_work_agent_session(
            work_id=work_id,
            agent_id=profile.id,
            session_id=f"session_{work_id}_{profile.id}",
            session_title=f"{work_id} - {profile.name}",
        )
    return store.ensure_work_agent_session(
        work_id=work_id,
        agent_id="agent_chanakya",
        session_id=f"session_{work_id}_agent_chanakya",
        session_title=f"{work_id} - Chanakya",
    )


def test_work_group_chat_persists_visible_agent_turns_and_mirrors_history() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store)
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="I found the core facts.", author_name="Researcher"),
            Message(role="assistant", text="Here is the polished answer.", author_name="Writer"),
            Message(
                role="assistant",
                text='{"status":"completed","summary":"Here is the polished answer."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Summarize the market", work_id="work_gc")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert [item.get("agent_name") for item in reply.messages] == ["Researcher", "Writer"]
    manager_task = next(
        task
        for task in store.list_tasks(session_id=work_session_id, limit=20)
        if task.get("task_type") == "manager_group_chat_orchestration"
    )
    execution_trace = manager_task.get("result", {}).get("execution_trace")
    assert execution_trace["request_message"] == "Summarize the market"
    assert execution_trace["call_sequence"][0]["kind"] == "manager_decision"
    assert execution_trace["call_sequence"][1]["kind"] == "participant_turn"
    assert execution_trace["call_sequence"][1]["agent_name"] == "Researcher"
    assert execution_trace["prompt_refs"]["orchestrator"]["agent_name"] == "Agent Manager"
    assert execution_trace["prompt_refs"]["participant:agent_researcher"]["agent_name"] == "Researcher"
    assert manager_task.get("input", {}).get("group_chat_state", {}).get("manager_termination_state", {}).get("status") == "completed"
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    assert root_task.get("input", {}).get("work_group_chat_state", {}).get("manager_termination_state", {}).get("status") == "completed"
    event_types = [item.get("event_type") for item in store.list_task_events(session_id=work_session_id)]
    assert "group_chat_speaker_selected" in event_types
    assert "group_chat_termination_decided" in event_types
    speaker_event = next(
        item for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_speaker_selected"
    )
    termination_event = next(
        item for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_termination_decided"
    )
    assert speaker_event["payload"]["selected_speaker"] == "Researcher"
    assert speaker_event["payload"]["selected_agent_id"] == "agent_researcher"
    assert termination_event["payload"]["termination_case"] == "user_request_satisfied"
    chanakya_messages = store.list_messages(work_session_id)
    assistant_messages = [item for item in chanakya_messages if item.get("role") == "assistant"]
    assert [item.get("metadata", {}).get("visible_agent_name") for item in assistant_messages] == [
        "Researcher",
        "Writer",
    ]
    developer_session = store.ensure_work_agent_session(
        work_id="work_gc",
        agent_id="agent_developer",
        session_id="unused",
        session_title="unused",
    )
    mirrored = store.list_messages(developer_session)
    assert any(item.get("content") == "Summarize the market" for item in mirrored)
    assert any(item.get("content") == "Here is the polished answer." for item in mirrored)


def test_work_group_chat_waiting_input_resumes_same_request() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_wait")
    manager = AgentManager(store, store.Session, manager_profile)
    call_count = {"count": 0}

    def _fake_builder(**kwargs):
        def _conversation(seeded):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return [
                    *seeded,
                    Message(
                        role="assistant",
                        text="NEEDS_USER_INPUT: Need a framework choice before implementation can continue.",
                        author_name="Developer",
                    ),
                    Message(
                        role="assistant",
                        text=(
                            '{"status":"needs_user_input","question":"Should we use Flask or FastAPI?",'
                            '"reason":"Framework not chosen.","requesting_agent_id":"agent_developer",'
                            '"requesting_agent_name":"Developer"}'
                        ),
                        author_name="Agent Manager",
                    ),
                ]
            return [
                *seeded,
                Message(role="assistant", text="Implemented with Flask.", author_name="Developer"),
                Message(
                    role="assistant",
                    text='{"status":"completed","summary":"Implemented with Flask."}',
                    author_name="Agent Manager",
                ),
            ]

        return _FakeWorkflow(_conversation)

    manager._build_work_group_chat_workflow = _fake_builder  # type: ignore[method-assign]

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    first = service.chat(work_session_id, "Build the API", work_id="work_wait")

    assert first.root_task_status == TASK_STATUS_WAITING_INPUT
    assert first.waiting_task_id
    assert (
        first.input_prompt
        == "I need one detail before I can continue: Should we use Flask or FastAPI?"
    )
    assert all("NEEDS_USER_INPUT:" not in str(item.get("text") or "") for item in first.messages)

    resumed = service.submit_task_input(first.waiting_task_id, "Use Flask")

    assert resumed.root_task_status == TASK_STATUS_DONE
    assert resumed.messages[-1]["text"] == "Implemented with Flask."
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    pending_state = dict(root_task["input"]).get("work_pending_interaction")
    assert isinstance(pending_state, dict)
    assert pending_state["active"] is False
    group_chat_state = dict(root_task["input"]).get("work_group_chat_state")
    assert isinstance(group_chat_state, dict)
    assert group_chat_state["manager_termination_state"]["status"] == "completed"
    chanakya_messages = store.list_messages(work_session_id)
    assert any(item.get("content") == "Use Flask" for item in chanakya_messages)
    assert any(item.get("content") == "Implemented with Flask." for item in chanakya_messages)


def test_work_chat_autoresume_prefers_explicit_active_pending_interaction() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_pending_marker")
    manager = AgentManager(store, store.Session, manager_profile)
    call_count = {"count": 0}

    def _fake_builder(**kwargs):
        def _conversation(seeded):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return [
                    *seeded,
                    Message(
                        role="assistant",
                        text="NEEDS_USER_INPUT: Need a framework choice before implementation can continue.",
                        author_name="Developer",
                    ),
                    Message(
                        role="assistant",
                        text=(
                            '{"status":"needs_user_input","question":"Should we use Flask or FastAPI?",'
                            '"reason":"Framework not chosen.","requesting_agent_id":"agent_developer",'
                            '"requesting_agent_name":"Developer"}'
                        ),
                        author_name="Agent Manager",
                    ),
                ]
            return [
                *seeded,
                Message(role="assistant", text="Implemented with Flask.", author_name="Developer"),
                Message(
                    role="assistant",
                    text='{"status":"completed","summary":"Implemented with Flask."}',
                    author_name="Agent Manager",
                ),
            ]

        return _FakeWorkflow(_conversation)

    manager._build_work_group_chat_workflow = _fake_builder  # type: ignore[method-assign]
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    first = service.chat(work_session_id, "Build the API", work_id="work_pending_marker")
    assert first.root_task_status == TASK_STATUS_WAITING_INPUT
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    pending_state = dict(root_task["input"]).get("work_pending_interaction")
    assert isinstance(pending_state, dict)
    assert pending_state["active"] is True
    assert pending_state["waiting_task_id"] == first.waiting_task_id
    group_chat_state = dict(root_task["input"]).get("work_group_chat_state")
    assert isinstance(group_chat_state, dict)
    assert group_chat_state["pending_clarification_owner"]["agent_name"] == "Developer"

    store.create_task(
        task_id="task_stale_waiting",
        request_id=root_task["request_id"],
        parent_task_id=None,
        title="Stale waiting",
        summary="Stale waiting",
        status=TASK_STATUS_WAITING_INPUT,
        owner_agent_id="agent_developer",
        task_type="developer_execution",
        input_json={"maf_pending_request_id": "pending_stale"},
    )

    resumed = service.chat(work_session_id, "Use Flask", work_id="work_pending_marker")

    assert resumed.root_task_status == TASK_STATUS_DONE
    assert resumed.messages[-1]["text"] == "Implemented with Flask."


def test_group_chat_max_rounds_is_normalized_into_bounded_failure() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_round_limit")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="Developer is still iterating.", author_name="Developer"),
            Message(
                role="assistant",
                text="The group chat has reached the maximum number of rounds.",
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Keep iterating until done", work_id="work_round_limit")

    assert reply.root_task_status == TASK_STATUS_FAILED
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    group_chat_state = root_task.get("input", {}).get("work_group_chat_state", {})
    assert group_chat_state["manager_termination_state"]["termination_case"] == "max_rounds_reached"
    manager_task = next(
        task
        for task in store.list_tasks(session_id=work_session_id, limit=20)
        if task.get("task_type") == "manager_group_chat_orchestration"
    )
    completion = manager_task.get("result", {}).get("completion", {})
    assert completion["termination_case"] == "max_rounds_reached"
    termination_event = next(
        item for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_termination_decided"
    )
    assert termination_event["payload"]["termination_case"] == "max_rounds_reached"


def test_work_group_chat_retries_transient_502() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_retry")
    manager = AgentManager(store, store.Session, manager_profile)
    call_count = {"count": 0}

    def _fake_builder(**kwargs):
        class _TransientWorkflow:
            async def run(self, message=None, include_status_events: bool = False, **more_kwargs):
                call_count["count"] += 1
                if call_count["count"] == 1:
                    raise RuntimeError("Error code: 502 - {'detail': ''}")
                seeded = list(message or [])
                return _FakeWorkflowResult([
                    [
                        *seeded,
                        Message(role="assistant", text="Recovered after retry.", author_name="Writer"),
                        Message(
                            role="assistant",
                            text='{"status":"completed","summary":"Recovered after retry."}',
                            author_name="Agent Manager",
                        ),
                    ]
                ])

        return _TransientWorkflow()

    manager._build_work_group_chat_workflow = _fake_builder  # type: ignore[method-assign]
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Retry this", work_id="work_retry")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert call_count["count"] == 2
    assert reply.messages[-1]["text"] == "Recovered after retry."


def test_group_chat_seeded_history_is_bounded() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)
    session_id = "session_seeded"
    store.ensure_session(session_id, title="Seeded")
    long_text = "x" * 2000
    for index in range(15):
        store.add_message(session_id, "assistant" if index % 2 else "user", f"{index}:{long_text}")

    seeded = manager._build_group_chat_seed_conversation(session_id)

    assert len(seeded) == 12
    assert all(len(item.text or "") <= 1215 for item in seeded)


def test_group_chat_orchestrator_is_built_with_retry_attempts() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)
    participant_profiles = manager._group_chat_participant_profiles()

    workflow = manager._build_work_group_chat_workflow(
        message="write a short public report",
        participant_profiles=participant_profiles,
    )

    orchestrator = workflow.executors.get(workflow.start_executor_id)
    assert orchestrator is not None
    assert getattr(orchestrator, "_retry_attempts", None) == 2


def test_group_chat_execution_trace_includes_runtime_decisions_and_tool_calls() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)
    participant_profiles = manager._group_chat_participant_profiles()
    trace = RuntimeGroupChatTrace(
        manager_decisions=[
            {
                "call_input": {
                    "input_messages": [
                        {"role": "user", "author_name": "User", "text": "Build a demo API", "content_types": []},
                        {"role": "user", "author_name": None, "text": "orchestrator instruction", "content_types": []},
                    ],
                    "available_tools": [],
                    "model": "test-model",
                    "backend": "local",
                    "endpoint": "http://test",
                    "response_format": "AgentOrchestrationOutput",
                },
                "decision": {
                    "terminate": False,
                    "reason": "Developer should implement first.",
                    "next_speaker": "Developer",
                    "final_message": None,
                },
                "response_messages": [],
                "raw_response_text": '{"terminate":false}',
            },
            {
                "call_input": {
                    "input_messages": [
                        {"role": "user", "author_name": "User", "text": "Build a demo API", "content_types": []},
                        {"role": "assistant", "author_name": "Developer", "text": "Implemented the API.", "content_types": []},
                        {"role": "user", "author_name": None, "text": "orchestrator instruction", "content_types": []},
                    ],
                    "available_tools": [],
                    "model": "test-model",
                    "backend": "local",
                    "endpoint": "http://test",
                    "response_format": "AgentOrchestrationOutput",
                },
                "decision": {
                    "terminate": True,
                    "reason": "Work is complete.",
                    "next_speaker": None,
                    "final_message": '{"status":"completed","summary":"Implemented the API."}',
                },
                "response_messages": [],
                "raw_response_text": '{"terminate":true}',
            },
        ],
        participant_calls=[
            {
                "agent_id": "agent_developer",
                "agent_name": "Developer",
                "agent_role": "developer",
                "prompt_ref": "participant:agent_developer",
                "call_input": {
                    "input_messages": [
                        {"role": "user", "author_name": "User", "text": "Build a demo API", "content_types": []},
                    ],
                    "available_tools": [{"tool_id": "mcp_filesystem", "tool_name": "Filesystem", "server_name": "basic"}],
                    "model": "test-model",
                    "backend": "local",
                    "endpoint": "http://test",
                },
                "response_messages": [
                    {"role": "assistant", "author_name": "Developer", "text": "Implemented the API.", "content_types": []},
                ],
                "tool_traces": [
                    {
                        "tool_id": "mcp_filesystem",
                        "tool_name": "Filesystem",
                        "server_name": "basic",
                        "status": "succeeded",
                        "input_payload": '{"path":"/workspace/app.py"}',
                        "output_text": '"ok"',
                        "error_text": None,
                    }
                ],
            }
        ],
    )

    execution_trace = manager.build_group_chat_execution_trace(
        request_message="Build a demo API",
        participant_profiles=participant_profiles,
        seeded_conversation=[Message(role="user", text="Build a demo API", author_name="User")],
        visible_messages=[
            {
                "text": "Implemented the API.",
                "agent_id": "agent_developer",
                "agent_name": "Developer",
                "agent_role": "developer",
                "turn_index": 0,
            }
        ],
        completion_payload={"status": "completed", "summary": "Implemented the API."},
        runtime_trace=trace,
    )

    assert execution_trace["capture_mode"] == "runtime_traced"
    assert execution_trace["manager_decisions"][0]["decision"]["next_speaker"] == "Developer"
    assert execution_trace["participant_calls"][0]["agent_name"] == "Developer"
    assert execution_trace["call_sequence"][1]["participant_call_input"]["backend"] == "local"
    assert execution_trace["call_sequence"][1]["tool_traces"][0]["tool_id"] == "mcp_filesystem"
    assert execution_trace["tool_calls"][0]["tool_traces"][0]["status"] == "succeeded"
