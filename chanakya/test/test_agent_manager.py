from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pytest import MonkeyPatch

from chanakya.agent.runtime import MAFRuntime, build_profile_agent_config
from chanakya.agent_manager import AgentManager, WORKFLOW_INFORMATION, WORKFLOW_SOFTWARE
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import TASK_STATUS_BLOCKED, TASK_STATUS_DONE
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
    _seed_agent(store, "agent_researcher", "Researcher", "researcher", tool_ids=["mcp_fetch"])
    _seed_agent(store, "agent_writer", "Writer", "writer")
    return chanakya, manager


def test_agent_manager_selects_expected_workflow_types() -> None:
    store = _build_store()
    _seed_full_hierarchy(store)
    manager_profile = store.get_agent_profile("agent_manager")
    manager = AgentManager(store, store.Session, manager_profile)

    assert manager.select_workflow("Implement and test login rate limiting") == WORKFLOW_SOFTWARE
    assert manager.select_workflow("Write a short essay about solar energy") == WORKFLOW_INFORMATION


def test_chat_service_routes_every_request_through_manager_for_software() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_cto","selected_role":"cto","reason":"software work","execution_mode":"software_delivery"}'
    )
    service.manager.specialist_runner = lambda profile, prompt, step: {
        (
            "cto",
            "brief",
        ): '{"implementation_brief":"Build it","assumptions":[],"risks":[],"testing_focus":["login"]}',
        (
            "cto",
            "review",
        ): '```python\nprint("Hello World")\n```\n\nValidation: output matches expected text.\nRisks: minimal; requires Python 3 runtime.',
    }[(profile.role, step)]
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            '{"implementation_summary":"Implemented rate limiting","assumptions":[],"risks":[],"testing_focus":["burst traffic"]}',
            '{"validation_summary":"Validated successfully","checks_performed":["unit tests"],"defects_or_risks":[],"pass_fail_recommendation":"pass"}',
        ]
    )
    service.manager.summary_runner = lambda prompt: (
        "Login rate limiting was implemented and validated successfully."
    )

    reply = service.chat("session_mgr", "Please fix and test login rate limiting")

    assert reply.route == "delegated_manager"
    assert reply.response_mode == WORKFLOW_SOFTWARE
    assert reply.root_task_status == TASK_STATUS_DONE
    assert "```python" in reply.message
    assert 'print("Hello World")' in reply.message

    all_tasks = store.list_tasks(session_id="session_mgr", limit=20)
    root_task = next(task for task in all_tasks if task["parent_task_id"] is None)
    manager_task = next(task for task in all_tasks if task["task_type"] == "manager_orchestration")
    specialist_task = next(task for task in all_tasks if task["task_type"] == "cto_supervision")
    developer_task = next(task for task in all_tasks if task["task_type"] == "developer_execution")
    tester_task = next(task for task in all_tasks if task["task_type"] == "tester_execution")

    assert manager_task["parent_task_id"] == root_task["id"]
    assert specialist_task["parent_task_id"] == manager_task["id"]
    assert developer_task["parent_task_id"] == specialist_task["id"]
    assert tester_task["parent_task_id"] == specialist_task["id"]
    assert tester_task["dependencies"] == [developer_task["id"]]
    assert developer_task["status"] == TASK_STATUS_DONE
    assert tester_task["status"] == TASK_STATUS_DONE

    events = store.list_task_events(session_id="session_mgr")
    event_types = [event["event_type"] for event in events]
    assert "manager_delegated" in event_types
    assert "task_created" in event_types
    assert "task_owner_assigned" in event_types
    assert "task_started" in event_types
    assert "manager_route_selected" in event_types
    assert "workflow_dependency_recorded" in event_types
    assert "worker_handoff_ready" in event_types
    assert "worker_unblocked" in event_types
    assert "worker_validation_completed" in event_types
    assert "specialist_workflow_completed" in event_types
    assert "workflow_completed" in event_types
    assert "manager_summary_completed" in event_types


def test_chat_service_routes_non_software_requests_through_informer_chain() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_informer","selected_role":"informer","reason":"research and writing","execution_mode":"information_delivery"}'
    )
    service.manager.specialist_runner = lambda profile, prompt, step: {
        (
            "informer",
            "brief",
        ): '{"research_brief":"Gather Berlin weather","audience":"user","required_facts":["temperature"],"caveats":["forecast can change"]}',
        (
            "informer",
            "review",
        ): "Berlin weather was researched and presented as a concise grounded answer.",
    }[(profile.role, step)]
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            '{"facts":["Berlin is cool today"],"references_or_sources":["forecast"],"uncertainties":["subject to change"],"notes_for_writer":["be concise"]}',
            "Berlin is cool today. Forecasts can change, so check again later for the latest conditions.",
        ]
    )
    service.manager.summary_runner = lambda prompt: (
        "Berlin weather was researched first and then turned into a concise answer."
    )

    reply = service.chat(
        "session_informer", "Research the weather in Berlin and write a concise answer"
    )

    assert reply.route == "delegated_manager"
    assert reply.response_mode == WORKFLOW_INFORMATION
    assert (
        reply.message
        == "Berlin weather was researched first and then turned into a concise answer."
    )
    writer_task = next(
        task
        for task in store.list_tasks(session_id="session_informer", limit=20)
        if task["task_type"] == "writer_execution"
    )
    researcher_task = next(
        task
        for task in store.list_tasks(session_id="session_informer", limit=20)
        if task["task_type"] == "researcher_execution"
    )
    assert writer_task["dependencies"] == [researcher_task["id"]]
    assert writer_task["status"] == TASK_STATUS_DONE
    event_types = [
        event["event_type"] for event in store.list_task_events(session_id="session_informer")
    ]
    assert "worker_handoff_ready" in event_types
    assert "worker_unblocked" in event_types
    assert "worker_output_completed" in event_types


def test_manager_preserves_specialist_response_when_user_did_not_request_summary() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_informer","selected_role":"informer","reason":"research and writing","execution_mode":"information_delivery"}'
    )
    service.manager.specialist_runner = lambda profile, prompt, step: {
        (
            "informer",
            "brief",
        ): '{"research_brief":"Gather Life of Pi facts","audience":"user","required_facts":["author"],"caveats":["avoid spoilers"]}',
        (
            "informer",
            "review",
        ): "Life of Pi is a 2001 novel by Yann Martel about survival, faith, and storytelling, later adapted into Ang Lee's 2012 film.",
    }[(profile.role, step)]
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            "Research handoff for Life of Pi",
            "Life of Pi is a 2001 novel by Yann Martel about survival, faith, and storytelling, later adapted into Ang Lee's 2012 film.",
        ]
    )
    service.manager.summary_runner = lambda prompt: "This should not be used."

    reply = service.chat("session_passthrough", "Tell me something about Life of Pi")

    assert reply.message == (
        "Life of Pi is a 2001 novel by Yann Martel about survival, faith, and storytelling, later adapted into Ang Lee's 2012 film."
    )


def test_informer_writer_recovers_when_workflow_output_contains_artifacts() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_informer","selected_role":"informer","reason":"research and writing","execution_mode":"information_delivery"}'
    )
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            "Structured research handoff about Virat Kohli",
            "This is a deterministic two-stage information workflow executed in order.\n<agent_framework._types.Message object at 0x1>",
        ]
    )
    service.manager.summary_runner = lambda prompt: "Virat Kohli facts were delivered."

    def _specialist_runner(profile: AgentProfileModel, prompt: str, step: str) -> str:
        if profile.role == "informer":
            if step == "brief":
                return '{"research_brief":"Gather Virat Kohli facts","audience":"user","required_facts":["birth"],"caveats":["verify freshness"]}'
            return "Virat Kohli facts were researched and presented clearly."
        return "Virat Kohli was born on 5 November 1988 in New Delhi and is one of cricket's most decorated batters."

    service.manager.specialist_runner = _specialist_runner

    reply = service.chat(
        "session_writer_recovery", "Tell me some important facts about Virat Kohli"
    )

    writer_task = next(
        task
        for task in store.list_tasks(session_id="session_writer_recovery", limit=20)
        if task["task_type"] == "writer_execution"
    )
    written_response = writer_task["result"]["written_response"]

    assert reply.message == written_response
    assert "deterministic two-stage" not in written_response
    assert "agent_framework._types.Message object" not in written_response
    assert "Virat Kohli" in written_response


def test_informer_writer_recovers_when_output_echoes_research_handoff() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_informer","selected_role":"informer","reason":"research and writing","execution_mode":"information_delivery"}'
    )
    research_handoff = (
        "**Researcher Handoff: Virat Kohli Biography Brief**\n\n"
        "Virat Kohli was born on 5 November 1988 in New Delhi and became one of India's most decorated batters."
    )
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            research_handoff,
            research_handoff,
        ]
    )
    service.manager.summary_runner = lambda prompt: "Virat Kohli biography delivered."

    def _specialist_runner(profile: AgentProfileModel, prompt: str, step: str) -> str:
        if profile.role == "informer":
            if step == "brief":
                return '{"research_brief":"Gather a short Virat Kohli biography","audience":"user","required_facts":["birth"],"caveats":["verify freshness"]}'
            return "Virat Kohli biography was reviewed and finalized."
        return "unused"

    service.manager.specialist_runner = _specialist_runner
    recovery_calls: list[str] = []

    def _fake_run_profile_prompt(profile: AgentProfileModel, prompt: str) -> str:
        recovery_calls.append(prompt)
        if len(recovery_calls) == 1:
            return research_handoff
        return (
            "Virat Kohli is an Indian cricketer born on 5 November 1988 in New Delhi. "
            "He rose from India's 2008 Under-19 World Cup-winning side to become one of the country's most successful batters and captains."
        )

    service.manager._run_profile_prompt = _fake_run_profile_prompt  # type: ignore[method-assign]

    reply = service.chat("session_writer_echo", "Give me a short biography of Virat Kohli")

    writer_task = next(
        task
        for task in store.list_tasks(session_id="session_writer_echo", limit=20)
        if task["task_type"] == "writer_execution"
    )
    written_response = writer_task["result"]["written_response"]

    assert reply.message == "Virat Kohli biography delivered."
    assert len(recovery_calls) == 2
    assert "Researcher Handoff" not in written_response
    assert written_response.startswith("Virat Kohli is an Indian cricketer")


def test_cto_tester_recovers_when_output_echoes_developer_handoff() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_cto","selected_role":"cto","reason":"software work","execution_mode":"software_delivery"}'
    )
    developer_handoff = '# Implementation Handoff\n\nprint("Hello World")'
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            developer_handoff,
            developer_handoff,
        ]
    )

    def _specialist_runner(profile: AgentProfileModel, prompt: str, step: str) -> str:
        if profile.role == "cto":
            if step == "brief":
                return '{"implementation_brief":"Build hello world","assumptions":[],"risks":[],"testing_focus":["stdout"]}'
            return '```python\nprint("Hello World")\n```\n\nValidation: output matches expected text.\nRisks: minimal.'
        return "unused"

    service.manager.specialist_runner = _specialist_runner
    tester_calls: list[str] = []

    def _fake_run_profile_prompt(profile: AgentProfileModel, prompt: str) -> str:
        tester_calls.append(prompt)
        if len(tester_calls) == 1:
            return developer_handoff
        return (
            '{"validation_summary":"Output matches Hello World","checks_performed":["stdout check"],'
            '"defects_or_risks":["requires Python 3"],"pass_fail_recommendation":"pass"}'
        )

    service.manager._run_profile_prompt = _fake_run_profile_prompt  # type: ignore[method-assign]

    reply = service.chat("session_tester_recovery", "Write a python program to print hello world")

    tester_task = next(
        task
        for task in store.list_tasks(session_id="session_tester_recovery", limit=20)
        if task["task_type"] == "tester_execution"
    )
    validation_report = tester_task["result"]["validation_report"]

    assert len(tester_calls) == 2
    assert "Implementation Handoff" not in validation_report
    assert "validation_summary" in validation_report
    assert "```python" in reply.message


def test_agent_manager_retries_invalid_route_then_falls_back() -> None:
    store = _build_store()
    _seed_full_hierarchy(store)
    manager_profile = store.get_agent_profile("agent_manager")
    manager = AgentManager(store, store.Session, manager_profile)
    calls: list[str] = []

    def _invalid_route(prompt: str) -> str:
        calls.append(prompt)
        return "not json"

    manager.route_runner = _invalid_route

    route = manager._select_route("Implement and test the billing API")

    assert route.selected_agent_id == "agent_cto"
    assert route.execution_mode == WORKFLOW_SOFTWARE
    assert route.source == "fallback"
    assert len(calls) == 2


def test_manager_prefers_saved_active_agents_during_delegation() -> None:
    store = _build_store()
    chanakya = _seed_agent(store, "agent_chanakya", "Chanakya", "personal_assistant")
    manager_profile = _seed_agent(store, "agent_manager", "Agent Manager", "manager")
    _seed_agent(store, "agent_cto", "CTO", "cto")
    _seed_agent(store, "agent_informer", "Informer", "informer")
    _seed_agent(store, "agent_seed_developer", "Developer Seed", "developer")
    _seed_agent(store, "agent_seed_tester", "Tester Seed", "tester")
    custom_developer = _seed_agent(store, "agent_a_developer", "A Developer", "developer")
    custom_tester = _seed_agent(store, "agent_a_tester", "A Tester", "tester")
    _seed_agent(store, "agent_researcher", "Researcher", "researcher")
    _seed_agent(store, "agent_writer", "Writer", "writer")

    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_cto","selected_role":"cto","reason":"software work","execution_mode":"software_delivery"}'
    )
    service.manager.specialist_runner = lambda profile, prompt, step: (
        "{}" if step == "brief" else "reviewed"
    )
    service.manager.workflow_runner = (
        lambda session_id, request_id, workflow_type, message, participants: [
            "developer output",
            "tester output",
        ]
    )
    service.manager.summary_runner = lambda prompt: "Saved agents completed the delegated workflow."

    reply = service.chat("session_saved_agents", "Implement and test milestone 5")

    worker_tasks = [
        task
        for task in store.list_tasks(session_id="session_saved_agents", limit=20)
        if task["parent_task_id"] is not None
        and task["task_type"] in {"developer_execution", "tester_execution"}
    ]
    owner_ids = sorted(task["owner_agent_id"] for task in worker_tasks)
    assert reply.root_task_status == TASK_STATUS_DONE
    assert owner_ids == sorted([custom_developer.id, custom_tester.id])


def test_build_profile_agent_config_uses_persisted_tool_ids_for_delegated_agents(
    monkeypatch: MonkeyPatch,
) -> None:
    class _Function:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name
            self.functions = [_Function(f"{name}_fn", f"Function for {name}")]

    profile = AgentProfileModel(
        id="agent_researcher",
        name="Researcher",
        role="researcher",
        system_prompt="You are Researcher",
        personality="",
        tool_ids_json=["mcp_fetch"],
        workspace=None,
        heartbeat_enabled=False,
        heartbeat_interval_seconds=300,
        heartbeat_file_path=None,
        is_active=True,
        created_at="2026-03-31T00:00:00+00:00",
        updated_at="2026-03-31T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "chanakya.agent.runtime.get_cached_tools",
        lambda: [_Tool("mcp_fetch"), _Tool("mcp_calculator")],
    )
    monkeypatch.setattr(
        "chanakya.agent.runtime.get_tools_availability",
        lambda: [{"tool_id": "mcp_fetch", "status": "available"}],
    )

    config = build_profile_agent_config(profile)

    assert [tool.name for tool in config.cached_tools] == ["mcp_fetch"]
    assert "mcp_fetch_fn" in config.system_prompt
    assert config.availability == [{"tool_id": "mcp_fetch", "status": "available"}]


def test_blocked_worker_dependency_is_persisted_before_completion() -> None:
    store = _build_store()
    chanakya, manager_profile = _seed_full_hierarchy(store)
    service = ChatService(
        store,
        cast(MAFRuntime, _RuntimeStub(chanakya)),
        AgentManager(store, store.Session, manager_profile),
    )
    assert service.manager is not None
    service.manager.route_runner = lambda prompt: (
        '{"selected_agent_id":"agent_cto","selected_role":"cto","reason":"software work","execution_mode":"software_delivery"}'
    )
    service.manager.specialist_runner = lambda profile, prompt, step: (
        "{}" if step == "brief" else "reviewed"
    )

    def _workflow_runner(
        session_id: str,
        request_id: str,
        workflow_type: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> list[str]:
        tasks = store.list_tasks(session_id=session_id, limit=20)
        tester_task = next(task for task in tasks if task["task_type"] == "tester_execution")
        assert tester_task["status"] == TASK_STATUS_BLOCKED
        assert len(tester_task["dependencies"]) == 1
        return ["developer output", "tester output"]

    service.manager.workflow_runner = _workflow_runner
    service.manager.summary_runner = lambda prompt: "done"

    reply = service.chat("session_dependency", "Implement and test dependency handling")

    assert reply.root_task_status == TASK_STATUS_DONE
