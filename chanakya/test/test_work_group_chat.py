from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agent_framework import Message

from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import TASK_STATUS_DONE, TASK_STATUS_WAITING_INPUT
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

    resumed = service.submit_task_input(first.waiting_task_id, "Use Flask")

    assert resumed.root_task_status == TASK_STATUS_DONE
    assert resumed.messages[-1]["text"] == "Implemented with Flask."
    chanakya_messages = store.list_messages(work_session_id)
    assert any(item.get("content") == "Use Flask" for item in chanakya_messages)
    assert any(item.get("content") == "Implemented with Flask." for item in chanakya_messages)


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
