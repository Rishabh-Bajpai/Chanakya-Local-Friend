from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from chanakya.agent_manager import AgentManager
from chanakya.agent.runtime import MAFRuntime
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import TASK_STATUS_DONE
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

    def runtime_metadata(self) -> dict[str, str | None]:
        return {"model": "test-model", "endpoint": "http://test", "runtime": "maf_agent"}

    def run(self, session_id: str, text: str, *, request_id: str) -> _RunResult:
        return _RunResult(
            text=f"{self.profile.role}:{text}", response_mode="direct_answer", tool_traces=[]
        )


class _ManagerStub:
    def should_delegate(self, message: str) -> bool:
        return "implement and test" in message.lower()

    def execute(self, *, session_id: str, request_id: str, root_task_id: str, message: str):
        raise AssertionError("not used in this file")


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def _seed_agent(store: ChanakyaStore, agent_id: str, name: str, role: str) -> AgentProfileModel:
    profile = AgentProfileModel(
        id=agent_id,
        name=name,
        role=role,
        system_prompt=f"You are {name}",
        personality="",
        tool_ids_json=[],
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


def test_agent_manager_selects_expected_workflow_types() -> None:
    store = _build_store()
    manager_profile = _seed_agent(store, "agent_manager", "Agent Manager", "manager")
    session_factory = store.Session
    manager = AgentManager(store, session_factory, manager_profile)

    assert manager.select_workflow("Implement and test a feature") == "chat"
    assert manager.select_workflow("Compare two architecture options") == "chat"
    assert manager.select_workflow("Plan then implement the change") == "chat"
    assert manager.select_workflow("Break this into tasks first") == "chat"


def test_chat_service_delegates_and_persists_child_tasks() -> None:
    store = _build_store()
    chanakya = _seed_agent(store, "agent_chanakya", "Chanakya", "personal_assistant")
    manager_profile = _seed_agent(store, "agent_manager", "Agent Manager", "manager")
    _seed_agent(store, "agent_developer", "Developer", "developer")
    _seed_agent(store, "agent_tester", "Tester", "tester")

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.group_chat_runner = lambda session_id, request_id, message, participants: (
        "\n".join(f"{profile.name}: discussion for {message}" for profile in participants)
    )
    service.manager.summary_runner = lambda prompt: (
        "Developer implemented the calculator and Tester validated the delegated result."
    )

    reply = service.chat("session_mgr", "Implement and test milestone 4")

    assert reply.route == "delegated_manager"
    assert reply.response_mode == "chat"
    assert reply.root_task_status == TASK_STATUS_DONE
    assert "Developer implemented the calculator" in reply.message

    all_tasks = store.list_tasks(session_id="session_mgr", limit=20)
    root_tasks = [task for task in all_tasks if task["parent_task_id"] is None]
    child_tasks = [task for task in all_tasks if task["parent_task_id"] == reply.root_task_id]
    child_tasks_sorted = sorted(child_tasks, key=lambda task: task["task_type"])

    assert len(root_tasks) == 1
    assert len(child_tasks) == 2
    assert child_tasks_sorted[0]["task_type"] == "developer_discussion"
    assert child_tasks_sorted[1]["task_type"] == "tester_discussion"
    assert child_tasks_sorted[0]["dependencies"] == []
    assert child_tasks_sorted[1]["dependencies"] == []
    assert all(task["status"] == TASK_STATUS_DONE for task in child_tasks)

    events = store.list_task_events(session_id="session_mgr")
    event_types = [event["event_type"] for event in events]
    assert "manager_delegated" in event_types
    assert "workflow_selected" in event_types
    assert "workflow_started" in event_types
    assert "workflow_aggregation_completed" in event_types


def test_manager_summary_falls_back_when_chat_output_not_extracted() -> None:
    store = _build_store()
    chanakya = _seed_agent(store, "agent_chanakya", "Chanakya", "personal_assistant")
    manager_profile = _seed_agent(store, "agent_manager", "Agent Manager", "manager")
    _seed_agent(store, "agent_developer", "Developer", "developer")
    _seed_agent(store, "agent_tester", "Tester", "tester")

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.group_chat_runner = lambda session_id, request_id, message, participants: (
        "Group chat orchestration started, but the orchestrator did not emit a parseable final output."
    )
    service.manager.summary_runner = lambda prompt: (
        "A calculator implementation was discussed and testing responsibilities were assigned."
    )

    reply = service.chat("session_mgr_2", "Implement and test milestone 4")

    assert reply.route == "delegated_manager"
    assert reply.root_task_status == TASK_STATUS_DONE
    assert "testing responsibilities were assigned" in reply.message


def test_store_can_filter_agents_by_role() -> None:
    store = _build_store()
    _seed_agent(store, "agent_developer", "Developer", "developer")
    _seed_agent(store, "agent_tester", "Tester", "tester")

    developers = store.find_active_agents_by_role("developer")
    testers = store.find_active_agents_by_role("tester")

    assert [agent.id for agent in developers] == ["agent_developer"]
    assert [agent.id for agent in testers] == ["agent_tester"]
