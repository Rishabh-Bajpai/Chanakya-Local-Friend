from __future__ import annotations

import json
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


class _TrackingManager(AgentManager):
    def __init__(self, store: ChanakyaStore, session_factory, manager_profile: AgentProfileModel) -> None:
        super().__init__(store, session_factory, manager_profile)
        self.prompts_run: list[tuple[str, str]] = []
        self.completion_adjudication_runner = self._adjudicate_completion

    def _run_profile_prompt(self, profile: AgentProfileModel, prompt: str) -> str:  # type: ignore[override]
        self.prompts_run.append((profile.role, prompt))
        return "Merged file saved to `/workspace/rishabh_bajpai_merged.txt` with the summary and citations combined."

    @staticmethod
    def _adjudicate_completion(profile, prompt: str, request_message: str, visible_messages: list[dict[str, Any]], completion_payload: dict[str, Any]) -> str:
        if visible_messages:
            latest = str(visible_messages[-1].get("text") or "").strip()
            return '{"status":"completed","summary":' + json.dumps(latest) + ',"reason":null,"termination_case":"user_request_satisfied"}'
        return '{"status":"failed","summary":null,"reason":"The workflow reported completion without producing any visible result.","termination_case":"blocker_or_failure"}'


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


class _TracedFakeWorkflow(_FakeWorkflow):
    def __init__(self, builder, trace: RuntimeGroupChatTrace) -> None:
        super().__init__(builder)
        self._chanakya_group_chat_trace = trace


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
    manager = _TrackingManager(store, store.Session, manager_profile)

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
    visible_message_events = [
        item for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_visible_message_emitted"
    ]
    assert speaker_event["payload"]["selected_speaker"] == "Researcher"
    assert speaker_event["payload"]["selected_agent_id"] == "agent_researcher"
    assert termination_event["payload"]["termination_case"] == "user_request_satisfied"
    assert len(visible_message_events) == 2
    assert execution_trace["context_policy"]["strategy"] == "compact_summary_plus_recent_visible_turns"
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


def test_work_group_chat_termination_event_uses_final_completion_payload() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_trace_reconcile")
    manager = _TrackingManager(store, store.Session, manager_profile)
    trace = RuntimeGroupChatTrace(
        manager_decisions=[
            {
                "round_index": 0,
                "decision": {
                    "terminate": False,
                    "reason": "Developer should implement first.",
                    "next_speaker": "Developer",
                },
            },
            {
                "round_index": 1,
                "decision": {
                    "terminate": True,
                    "reason": "The conversation is stalled.",
                    "final_message": "Implementation is complete.",
                },
            },
        ]
    )
    manager._build_work_group_chat_workflow = lambda **kwargs: _TracedFakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="Implemented the Flask app.", author_name="Developer"),
            Message(
                role="assistant",
                text='{"status":"completed","summary":"Implemented the Flask app."}',
                author_name="Agent Manager",
            ),
        ],
        trace,
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Build the Flask app", work_id="work_trace_reconcile")

    assert reply.root_task_status == TASK_STATUS_DONE
    termination_event = next(
        item
        for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_termination_decided"
    )
    assert termination_event["payload"]["status"] == "completed"
    assert termination_event["payload"]["termination_case"] == "user_request_satisfied"


def test_group_chat_participant_profiles_are_minimized_by_request_type() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)

    assert [item.id for item in manager._group_chat_participant_profiles(message="implement a flask api")] == [
        "agent_developer"
    ]
    assert [
        item.id
        for item in manager._group_chat_participant_profiles(
            message="implement and test a flask api"
        )
    ] == ["agent_developer", "agent_tester"]
    assert [item.id for item in manager._group_chat_participant_profiles(message="summarize this market report")] == [
        "agent_researcher",
        "agent_writer",
    ]
    assert [item.id for item in manager._group_chat_participant_profiles(message="rewrite it in a friendlier tone")] == [
        "agent_writer"
    ]


def test_group_chat_split_collapses_consecutive_messages_from_same_agent() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)
    participant_profiles = manager._group_chat_participant_profiles()

    completion, visible_messages = manager._split_group_chat_completion(
        conversation_slice=[
            Message(role="assistant", text="First update.", author_name="Developer"),
            Message(role="assistant", text="Second update.", author_name="Developer"),
            Message(role="assistant", text='{"status":"completed","summary":"Done."}', author_name="Agent Manager"),
        ],
        participant_profiles=participant_profiles,
    )

    assert completion["status"] == "completed"
    assert len(visible_messages) == 1
    assert visible_messages[0]["agent_name"] == "Developer"
    assert visible_messages[0]["text"] == "First update.\n\nSecond update."


def test_work_chat_binds_classic_session_to_active_work() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_binding")
    manager = _TrackingManager(store, store.Session, manager_profile)
    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="Here is the work update.", author_name="Researcher"),
            Message(
                role="assistant",
                text='{"status":"completed","summary":"Here is the work update."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    classic_session_id = "session_classic_binding"
    store.ensure_session(classic_session_id, title="Classic Chat")
    reply = service.chat(classic_session_id, "Continue this work", work_id="work_binding")

    assert reply.root_task_status == TASK_STATUS_DONE
    active_work = store.get_active_classic_work(classic_session_id)
    assert active_work is not None
    assert active_work["work_id"] == "work_binding"
    assert active_work["work_session_id"] == work_session_id
    assert active_work["root_request_id"] == reply.request_id
    assert active_work["workflow_type"] == "work_group_chat"


def test_work_group_chat_waiting_input_resumes_same_request() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_wait")
    manager = _TrackingManager(store, store.Session, manager_profile)
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
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    group_chat_state = dict(root_task["input"]).get("work_group_chat_state")
    assert isinstance(group_chat_state, dict)
    assert group_chat_state["context_policy"]["strategy"] == "compact_summary_plus_recent_visible_turns"
    clarification_event = next(
        item for item in store.list_task_events(session_id=work_session_id)
        if item.get("event_type") == "group_chat_clarification_requested"
    )
    assert clarification_event["payload"]["requesting_agent_name"] == "Developer"
    assert clarification_event["payload"]["latest_synchronized_conversation_cursor"] >= 1

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


def test_group_chat_seed_conversation_compacts_older_visible_history() -> None:
    store = _build_store()
    _chanakya, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)

    records: list[dict[str, Any]] = []
    for index in range(14):
        if index % 2 == 0:
            records.append(
                {
                    "role": "user",
                    "content": f"User request {index // 2}",
                    "metadata": {},
                }
            )
        else:
            records.append(
                {
                    "role": "assistant",
                    "content": f"Agent update {index // 2}",
                    "metadata": {"visible_agent_name": "Developer"},
                }
            )

    seeded = manager.build_group_chat_seed_conversation_from_records(records)

    assert seeded[0].author_name == "Chanakya"
    assert seeded[0].text.startswith("Earlier shared context summary:")
    assert "User request 0" in seeded[0].text
    assert len(seeded) == 9
    assert seeded[-1].text == "Agent update 6"


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


def test_group_chat_software_completion_requires_developer_and_tester_when_validation_requested() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_validation_guard")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text="I've updated `hello_world.py` with the requested implementation and verified it conceptually.",
                author_name="Researcher",
            ),
            Message(
                role="assistant",
                text='{"status":"completed","summary":"Finished the software change."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Implement and test the hello world update", work_id="work_validation_guard")

    assert reply.root_task_status == TASK_STATUS_FAILED
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    group_chat_state = dict(root_task["input"]).get("work_group_chat_state") or {}
    assert group_chat_state["manager_termination_state"]["termination_case"] == "completion_requirements_not_met"
    manager_task = next(
        task
        for task in store.list_tasks(session_id=work_session_id, limit=20)
        if task.get("task_type") == "manager_group_chat_orchestration"
    )
    completion = manager_task.get("result", {}).get("completion", {})
    assert completion["termination_case"] == "completion_requirements_not_met"
    assert completion["completion_requirements"]["require_tester_validation"] is True
    assert completion["completion_requirements"]["developer_implementation_seen"] is False


def test_group_chat_software_completion_allows_developer_only_when_validation_not_requested() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_developer_only")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="Implemented `/workspace/hello_world.py` with the requested output.", author_name="Developer"),
            Message(
                role="assistant",
                text='{"status":"completed","summary":"Implemented the requested output."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Implement the hello world update", work_id="work_developer_only")

    assert reply.root_task_status == TASK_STATUS_DONE


def test_group_chat_participant_profiles_narrow_for_software_requests() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)

    software_profiles = manager._group_chat_participant_profiles("Implement the hello world update")
    validated_profiles = manager._group_chat_participant_profiles("Implement and test the hello world update")
    default_profiles = manager._group_chat_participant_profiles()

    assert [profile.role for profile in software_profiles] == ["developer"]
    assert [profile.role for profile in validated_profiles] == ["developer", "tester"]
    assert len(default_profiles) > len(validated_profiles)


def test_group_chat_split_completion_collapses_consecutive_messages_from_same_agent() -> None:
    store = _build_store()
    _, manager_profile = _seed_full_hierarchy(store)
    manager = AgentManager(store, store.Session, manager_profile)
    participant_profiles = manager._group_chat_participant_profiles()

    completion, visible_messages = manager._split_group_chat_completion(
        conversation_slice=[
            Message(role="assistant", text="First implementation update.", author_name="Developer"),
            Message(role="assistant", text="Second implementation update.", author_name="Developer"),
            Message(role="assistant", text='{"status":"completed","summary":"Done."}', author_name="Agent Manager"),
        ],
        participant_profiles=participant_profiles,
    )

    assert completion["status"] == "completed"
    assert len(visible_messages) == 1
    assert visible_messages[0]["agent_name"] == "Developer"
    assert visible_messages[0]["text"] == "First implementation update.\n\nSecond implementation update."


def test_group_chat_recovers_false_negative_failure_when_developer_evidence_exists() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_false_negative")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text="Script saved to `/workspace/primes_between_74_and_534.py` in the shared workspace.",
                author_name="Developer",
            ),
            Message(
                role="assistant",
                text='{"status":"failed","reason":"The conversation has been terminated by the agent."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(
        work_session_id,
        "write a Python script for finding the prime number between 74 and 534. then save the code",
        work_id="work_false_negative",
    )

    assert reply.root_task_status == TASK_STATUS_DONE
    assert "primes_between_74_and_534.py" in reply.messages[-1]["text"]


def test_group_chat_failed_run_uses_failure_reason_when_no_visible_output() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_reason_surface")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text='{"status":"failed","reason":"No configured participant/tool path could capture a screenshot for this request."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(
        work_session_id,
        "take a screenshot of this website https://example.com",
        work_id="work_reason_surface",
    )

    assert reply.root_task_status == TASK_STATUS_FAILED
    assert reply.message == "No configured participant/tool path could capture a screenshot for this request."


def test_group_chat_recovers_successful_information_followup_from_bad_failure_payload() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_info_followup")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text="Here is the extracted and summarized text content from the website.",
                author_name="Researcher",
            ),
            Message(
                role="assistant",
                text='{"status":"failed","reason":"The user requested to extract and summarize the text content of the website. The Researcher has already fetched the content and provided a comprehensive summary. No further steps are required."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(
        work_session_id,
        "Extract and summarize the text content from this website",
        work_id="work_info_followup",
    )

    assert reply.root_task_status == TASK_STATUS_DONE
    assert reply.messages[-1]["agent_name"] == "Researcher"


def test_group_chat_save_followup_stops_once_workspace_path_is_reported() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_save_followup")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text="Done. The summary has been saved to `/workspace/rishabh_bajpai_summary.txt` in the active workspace.",
                author_name="Developer",
            ),
            Message(
                role="assistant",
                text='{"status":"failed","reason":"The 1-paragraph summary of Rishabh Bajpai has been saved to the workspace."}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "save it to the workspace", work_id="work_save_followup")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert "/workspace/rishabh_bajpai_summary.txt" in reply.messages[-1]["text"]


def test_group_chat_max_rounds_falls_back_to_success_when_visible_report_is_sufficient() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_report_rounds")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text="Report saved as **`climate_change_2025_report.md`** in the shared workspace.\n\n**Word count: ~170 words.**\n\n### Summary of Key Points:\n1. Temperatures remained near record highs in 2025.\n2. Climate action progress was uneven across sectors.\n3. Extreme weather continued to intensify globally.\n4. Major legal and diplomatic milestones shaped the year.\n5. Adaptation and emissions gaps remained significant.",
                author_name="Researcher",
            ),
            Message(
                role="assistant",
                text="The group chat has reached the maximum number of rounds.",
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "ok do it, I want 100-200 words", work_id="work_report_rounds")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert "climate_change_2025_report.md" in reply.messages[-1]["text"]


def test_group_chat_work_context_memo_includes_recent_requests_outputs_and_paths() -> None:
    store = _build_store()
    _chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_context_memo")
    manager = _TrackingManager(store, store.Session, manager_profile)

    store.add_message(work_session_id, "user", "First request", metadata={})
    store.add_message(
        work_session_id,
        "assistant",
        "Done. The summary has been saved to `/workspace/rishabh_bajpai_summary.txt` in the active workspace.",
        metadata={"visible_agent_name": "Developer"},
    )
    store.add_message(
        work_session_id,
        "assistant",
        "I've saved Rishabh Bajpai's citations to `/workspace/rishabh_bajpai_citations.txt` in the active workspace.",
        metadata={"visible_agent_name": "Researcher"},
    )
    memo = manager._build_group_chat_work_context_memo(
        session_id=work_session_id,
        current_message="good! can you merge those files?",
    )

    assert "Current user request: good! can you merge those files?" in memo
    assert "Recent user requests:" in memo
    assert "Recent visible work outputs:" in memo
    assert "/workspace/rishabh_bajpai_summary.txt" in memo
    assert "/workspace/rishabh_bajpai_citations.txt" in memo
    assert "resolve it against the recent requests, outputs, and workspace artifacts" in memo


def test_group_chat_retries_with_sanitized_user_seed_after_missing_user_query_failure() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_retry_seed")
    manager = AgentManager(store, store.Session, manager_profile)
    call_count = {"count": 0}

    def _fake_builder(**kwargs):
        class _Workflow:
            async def run(self, message=None, include_status_events: bool = False, **more_kwargs):
                call_count["count"] += 1
                seeded = list(message or [])
                if call_count["count"] == 1:
                    return _FakeWorkflowResult([
                        [
                            *seeded,
                            Message(
                                role="assistant",
                                text='{"status":"failed","reason":"Internal framework error: prompt template rendering failed (no user query found in messages). Please retry the request."}',
                                author_name="Agent Manager",
                            ),
                        ]
                    ])
                assert seeded[-1].role == "user"
                assert seeded[-1].text == "please try again"
                return _FakeWorkflowResult([
                    [
                        *seeded,
                        Message(
                            role="assistant",
                            text="Merged file saved to `/workspace/rishabh_bajpai_merged.txt` in the active workspace.",
                            author_name="Developer",
                        ),
                        Message(
                            role="assistant",
                            text='{"status":"completed","summary":"Merged file saved to `/workspace/rishabh_bajpai_merged.txt` in the active workspace."}',
                            author_name="Agent Manager",
                        ),
                    ]
                ])

        return _Workflow()

    manager._build_work_group_chat_workflow = _fake_builder  # type: ignore[method-assign]
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "please try again", work_id="work_retry_seed")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert "/workspace/rishabh_bajpai_merged.txt" in reply.messages[-1]["text"]
    assert call_count["count"] == 2


def test_delegated_tool_traces_are_persisted_and_counted() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_tool_trace")
    manager = _TrackingManager(store, store.Session, manager_profile)
    trace = RuntimeGroupChatTrace(
        manager_decisions=[
            {
                "round_index": 0,
                "call_input": {"input_messages": [], "available_tools": []},
                "decision": {
                    "terminate": False,
                    "reason": "Developer should write the file.",
                    "next_speaker": "Developer",
                    "final_message": None,
                },
                "response_messages": [],
                "raw_response_text": '{"terminate":false}',
            },
            {
                "round_index": 1,
                "call_input": {"input_messages": [], "available_tools": []},
                "decision": {
                    "terminate": True,
                    "reason": "Work complete.",
                    "next_speaker": None,
                    "final_message": '{"status":"completed","summary":"Saved report."}',
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
                "call_input": {"input_messages": [], "available_tools": []},
                "response_messages": [
                    {"role": "assistant", "author_name": "Developer", "text": "Saved report to /workspace/report.md", "content_types": []},
                ],
                "tool_traces": [
                    {
                        "tool_id": "mcp_filesystem",
                        "tool_name": "Filesystem",
                        "server_name": "basic",
                        "status": "succeeded",
                        "input_payload": '{"path":"/workspace/report.md"}',
                        "output_text": '"ok"',
                        "error_text": None,
                    }
                ],
            }
        ],
    )

    manager._build_work_group_chat_workflow = lambda **kwargs: _TracedFakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(role="assistant", text="Saved report to /workspace/report.md", author_name="Developer"),
            Message(role="assistant", text='{"status":"completed","summary":"Saved report."}', author_name="Agent Manager"),
        ],
        trace,
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Write and save a report", work_id="work_tool_trace")

    assert reply.root_task_status == TASK_STATUS_DONE
    assert reply.tool_calls_used == 1
    traces = store.list_tool_invocations(request_id=reply.request_id, limit=20)
    assert len(traces) == 1
    assert traces[0]["agent_id"] == "agent_developer"
    assert traces[0]["agent_name"] == "Developer"
    events = store.list_task_events(session_id=work_session_id)
    persisted_event = next(item for item in events if item.get("event_type") == "response_persisted")
    assert persisted_event["payload"]["tool_calls_used"] == 1


def test_group_chat_completed_without_result_is_rejected() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_empty_success")
    manager = AgentManager(store, store.Session, manager_profile)

    manager._build_work_group_chat_workflow = lambda **kwargs: _FakeWorkflow(  # type: ignore[method-assign]
        lambda seeded: [
            *seeded,
            Message(
                role="assistant",
                text='{"status":"completed","summary":""}',
                author_name="Agent Manager",
            ),
        ]
    )

    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "try again", work_id="work_empty_success")

    assert reply.root_task_status == TASK_STATUS_FAILED
    assert reply.message == "The workflow reported completion without producing any visible result."


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


def test_group_chat_failure_preserves_runtime_failure_classification() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    work_session_id = _create_work_with_sessions(store, "work_retry_fail")
    manager = AgentManager(store, store.Session, manager_profile)

    def _fake_builder(**kwargs):
        class _AlwaysFailsWorkflow:
            async def run(self, message=None, include_status_events: bool = False, **more_kwargs):
                raise RuntimeError("Error code: 502 - {'detail': ''}")

        return _AlwaysFailsWorkflow()

    manager._build_work_group_chat_workflow = _fake_builder  # type: ignore[method-assign]
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(chanakya)), manager)
    service._conversation_layer = type("_DisabledLayer", (), {"enabled": False})()  # type: ignore[attr-defined]

    reply = service.chat(work_session_id, "Retry until it works", work_id="work_retry_fail")

    assert reply.root_task_status == TASK_STATUS_FAILED
    root_task = next(task for task in store.list_tasks(session_id=work_session_id, root_only=True) if task["is_root"])
    group_chat_state = dict(root_task["input"]).get("work_group_chat_state") or {}
    assert group_chat_state["manager_termination_state"]["termination_case"] == "transient_provider_failure"


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

    assert len(seeded) == 9
    assert seeded[0].text.startswith("Earlier shared context summary:")
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
