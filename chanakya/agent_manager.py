from __future__ import annotations

from difflib import SequenceMatcher
import json
from dataclasses import dataclass
from typing import Any

from agent_framework import Message
from agent_framework.openai import OpenAIChatClient
from agent_framework.orchestrations import SequentialBuilder
from sqlalchemy.orm import Session, sessionmaker

from chanakya.agent.runtime import build_profile_agent
from chanakya.debug import debug_log
from chanakya.domain import (
    TASK_STATUS_CREATED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    make_id,
    now_iso,
)
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import ChanakyaStore

WORKFLOW_SOFTWARE = "software_delivery"
WORKFLOW_INFORMATION = "information_delivery"


@dataclass(slots=True)
class ManagerRunResult:
    text: str
    workflow_type: str
    child_task_ids: list[str]
    manager_agent_id: str
    worker_agent_ids: list[str]
    task_status: str
    result_json: dict[str, Any]


@dataclass(slots=True)
class RoutingDecision:
    selected_agent_id: str
    selected_role: str
    reason: str
    execution_mode: str
    source: str


@dataclass(slots=True)
class SpecialistWorkflowResult:
    text: str
    task_status: str
    child_task_ids: list[str]
    worker_agent_ids: list[str]
    result_json: dict[str, Any]


class AgentManager:
    def __init__(
        self,
        store: ChanakyaStore,
        session_factory: sessionmaker[Session],
        manager_profile: AgentProfileModel,
    ) -> None:
        self.store = store
        self.session_factory = session_factory
        self.manager_profile = manager_profile
        self.client = OpenAIChatClient(env_file_path=".env")
        self.route_runner: Any | None = None
        self.summary_runner: Any | None = None
        self.specialist_runner: Any | None = None
        self.workflow_runner: Any | None = None

    def should_delegate(self, message: str) -> bool:
        return bool(message.strip())

    def select_workflow(self, message: str) -> str:
        return self._fallback_route(message).execution_mode

    def execute(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        message: str,
    ) -> ManagerRunResult:
        manager_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=root_task_id,
            owner_profile=self.manager_profile,
            title="Agent Manager Orchestration",
            summary="Route the request to the correct top-level specialist and aggregate the result.",
            task_type="manager_orchestration",
            session_id=session_id,
            started=True,
            input_json={"message": message},
        )
        child_task_ids = [manager_task_id]

        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="manager_delegated",
            payload={
                "manager_agent_id": self.manager_profile.id,
                "manager_task_id": manager_task_id,
                "message": message,
            },
        )

        route = self._select_route(message)
        specialist_profile = self.store.get_agent_profile(route.selected_agent_id)
        specialist_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=manager_task_id,
            owner_profile=specialist_profile,
            title=f"{specialist_profile.name} Supervision",
            summary=route.reason,
            task_type=f"{specialist_profile.role}_supervision",
            session_id=session_id,
            started=True,
            input_json={
                "message": message,
                "route_reason": route.reason,
                "execution_mode": route.execution_mode,
                "route_source": route.source,
            },
        )
        child_task_ids.append(specialist_task_id)

        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=manager_task_id,
            event_type="manager_route_selected",
            payload={
                "selected_agent_id": route.selected_agent_id,
                "selected_role": route.selected_role,
                "reason": route.reason,
                "execution_mode": route.execution_mode,
                "source": route.source,
                "specialist_task_id": specialist_task_id,
            },
        )

        if route.selected_agent_id == "agent_cto":
            specialist_result = self._execute_software_workflow(
                session_id=session_id,
                request_id=request_id,
                message=message,
                specialist_profile=specialist_profile,
                specialist_task_id=specialist_task_id,
            )
        else:
            specialist_result = self._execute_information_workflow(
                session_id=session_id,
                request_id=request_id,
                message=message,
                specialist_profile=specialist_profile,
                specialist_task_id=specialist_task_id,
            )

        child_task_ids.extend(specialist_result.child_task_ids)
        final_summary = self._generate_manager_summary(
            root_message=message,
            route=route,
            specialist_profile=specialist_profile,
            specialist_result=specialist_result,
        )
        finished_at = now_iso()
        self._transition_task(
            session_id=session_id,
            request_id=request_id,
            task_id=manager_task_id,
            from_status=TASK_STATUS_IN_PROGRESS,
            to_status=specialist_result.task_status,
            finished_at=finished_at,
            result_json={
                "route": {
                    "selected_agent_id": route.selected_agent_id,
                    "selected_role": route.selected_role,
                    "reason": route.reason,
                    "execution_mode": route.execution_mode,
                    "source": route.source,
                },
                "specialist_task_id": specialist_task_id,
                "specialist_summary": specialist_result.text,
                "final_summary": final_summary,
            },
            event_type=(
                "workflow_completed"
                if specialist_result.task_status == TASK_STATUS_DONE
                else "workflow_failed"
            ),
            event_payload={
                "workflow_type": route.execution_mode,
                "specialist_task_id": specialist_task_id,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=manager_task_id,
            event_type="manager_summary_completed",
            payload={
                "task_status": specialist_result.task_status,
                "workflow_type": route.execution_mode,
                "finished_at": finished_at,
            },
        )
        return ManagerRunResult(
            text=final_summary,
            workflow_type=route.execution_mode,
            child_task_ids=child_task_ids,
            manager_agent_id=self.manager_profile.id,
            worker_agent_ids=[specialist_profile.id, *specialist_result.worker_agent_ids],
            task_status=specialist_result.task_status,
            result_json={
                "workflow_type": route.execution_mode,
                "route": {
                    "selected_agent_id": route.selected_agent_id,
                    "selected_role": route.selected_role,
                    "reason": route.reason,
                    "execution_mode": route.execution_mode,
                    "source": route.source,
                },
                "child_task_ids": child_task_ids,
                "worker_agent_ids": [specialist_profile.id, *specialist_result.worker_agent_ids],
                "specialist_task_id": specialist_task_id,
                "specialist_summary": specialist_result.text,
                "summary": final_summary,
            },
        )

    def _execute_software_workflow(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        specialist_profile: AgentProfileModel,
        specialist_task_id: str,
    ) -> SpecialistWorkflowResult:
        developer_profile = self._pick_worker("developer")
        tester_profile = self._pick_worker("tester")
        developer_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=specialist_task_id,
            owner_profile=developer_profile,
            title=f"{developer_profile.name} Implementation",
            summary="Implement the requested software change and prepare a testing handoff.",
            task_type="developer_execution",
            session_id=session_id,
            started=True,
            input_json={"message": message},
        )
        tester_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=specialist_task_id,
            owner_profile=tester_profile,
            title=f"{tester_profile.name} Validation",
            summary="Validate the developer handoff after implementation completes.",
            task_type="tester_execution",
            session_id=session_id,
            status=TASK_STATUS_BLOCKED,
            dependencies=[developer_task_id],
            input_json={"message": message},
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=tester_task_id,
            event_type="workflow_dependency_recorded",
            payload={"depends_on_task_id": developer_task_id, "workflow_type": WORKFLOW_SOFTWARE},
        )
        try:
            implementation_brief = self._run_specialist_prompt(
                specialist_profile,
                self._build_cto_brief_prompt(message),
                step="brief",
            )
            developer_prompt = self._build_developer_stage_prompt(message, implementation_brief)
            tester_prompt = self._build_tester_stage_prompt(message, implementation_brief)
            self.store.update_task(
                developer_task_id,
                input_json={
                    "message": message,
                    "supervisor_brief": implementation_brief,
                    "effective_prompt": developer_prompt,
                },
            )
            self.store.update_task(
                tester_task_id,
                input_json={
                    "message": message,
                    "supervisor_brief": implementation_brief,
                    "effective_prompt": tester_prompt,
                    "waiting_on_task_id": developer_task_id,
                },
            )
            worker_outputs = self._run_sequential_workflow(
                session_id=session_id,
                request_id=request_id,
                workflow_type=WORKFLOW_SOFTWARE,
                message=self._build_software_worker_prompt(message, implementation_brief),
                participants=[developer_profile, tester_profile],
            )
            developer_output = worker_outputs[0]
            tester_output = worker_outputs[1]
            tester_started_at = now_iso()
            finished_at = now_iso()
            self.store.update_task(
                tester_task_id,
                input_json={
                    "message": message,
                    "supervisor_brief": implementation_brief,
                    "effective_prompt": tester_prompt,
                    "waiting_on_task_id": developer_task_id,
                    "developer_handoff": developer_output,
                    "delegated_handoff_prompt": self._build_tester_handoff_prompt(
                        message,
                        implementation_brief,
                        developer_output,
                    ),
                },
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=developer_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "implementation_brief": implementation_brief,
                    "handoff": developer_output,
                },
                event_type="worker_handoff_ready",
                event_payload={"handoff_for_role": tester_profile.role},
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=tester_task_id,
                from_status=TASK_STATUS_BLOCKED,
                to_status=TASK_STATUS_IN_PROGRESS,
                started_at=tester_started_at,
                event_type="worker_unblocked",
                event_payload={"dependency_task_id": developer_task_id},
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=tester_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "developer_task_id": developer_task_id,
                    "validation_report": tester_output,
                },
                event_type="worker_validation_completed",
                event_payload={"validated_task_id": developer_task_id},
            )
            summary = self._run_specialist_prompt(
                specialist_profile,
                self._build_cto_review_prompt(
                    message, implementation_brief, developer_output, tester_output
                ),
                step="review",
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "implementation_brief": implementation_brief,
                    "developer_task_id": developer_task_id,
                    "tester_task_id": tester_task_id,
                    "developer_output": developer_output,
                    "tester_output": tester_output,
                    "summary": summary,
                },
                event_type="specialist_workflow_completed",
                event_payload={
                    "workflow_type": WORKFLOW_SOFTWARE,
                    "worker_task_ids": [developer_task_id, tester_task_id],
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                event_type="specialist_review_completed",
                payload={
                    "workflow_type": WORKFLOW_SOFTWARE,
                    "worker_agent_ids": [developer_profile.id, tester_profile.id],
                },
            )
            return SpecialistWorkflowResult(
                text=summary,
                task_status=TASK_STATUS_DONE,
                child_task_ids=[developer_task_id, tester_task_id],
                worker_agent_ids=[developer_profile.id, tester_profile.id],
                result_json={
                    "workflow_type": WORKFLOW_SOFTWARE,
                    "developer_task_id": developer_task_id,
                    "tester_task_id": tester_task_id,
                    "implementation_brief": implementation_brief,
                    "developer_output": developer_output,
                    "tester_output": tester_output,
                    "summary": summary,
                },
            )
        except Exception as exc:
            finished_at = now_iso()
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=developer_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
                event_type="worker_failed",
            )
            self.store.update_task(
                tester_task_id,
                error_text=str(exc),
                finished_at=finished_at,
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
                event_type="specialist_workflow_failed",
                event_payload={"workflow_type": WORKFLOW_SOFTWARE},
            )
            failure_text = (
                "Software delivery workflow failed before a complete supervisor review was available. "
                f"Failure: {exc}"
            )
            return SpecialistWorkflowResult(
                text=failure_text,
                task_status=TASK_STATUS_FAILED,
                child_task_ids=[developer_task_id, tester_task_id],
                worker_agent_ids=[developer_profile.id, tester_profile.id],
                result_json={
                    "workflow_type": WORKFLOW_SOFTWARE,
                    "developer_task_id": developer_task_id,
                    "tester_task_id": tester_task_id,
                    "error": str(exc),
                },
            )

    def _execute_information_workflow(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        specialist_profile: AgentProfileModel,
        specialist_task_id: str,
    ) -> SpecialistWorkflowResult:
        researcher_profile = self._pick_worker("researcher")
        writer_profile = self._pick_worker("writer")
        researcher_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=specialist_task_id,
            owner_profile=researcher_profile,
            title=f"{researcher_profile.name} Research",
            summary="Gather grounded facts and produce a structured writer handoff.",
            task_type="researcher_execution",
            session_id=session_id,
            started=True,
            input_json={"message": message},
        )
        writer_task_id = self._create_child_task(
            request_id=request_id,
            parent_task_id=specialist_task_id,
            owner_profile=writer_profile,
            title=f"{writer_profile.name} Writing",
            summary="Turn the research handoff into a polished response.",
            task_type="writer_execution",
            session_id=session_id,
            status=TASK_STATUS_BLOCKED,
            dependencies=[researcher_task_id],
            input_json={"message": message},
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=writer_task_id,
            event_type="workflow_dependency_recorded",
            payload={
                "depends_on_task_id": researcher_task_id,
                "workflow_type": WORKFLOW_INFORMATION,
            },
        )
        try:
            research_brief = self._run_specialist_prompt(
                specialist_profile,
                self._build_informer_brief_prompt(message),
                step="brief",
            )
            researcher_prompt = self._build_researcher_stage_prompt(message, research_brief)
            self.store.update_task(
                researcher_task_id,
                input_json={
                    "message": message,
                    "supervisor_brief": research_brief,
                    "effective_prompt": researcher_prompt,
                },
            )
            self.store.update_task(
                writer_task_id,
                input_json={
                    "message": message,
                    "waiting_on_task_id": researcher_task_id,
                },
            )
            worker_outputs = self._run_sequential_workflow(
                session_id=session_id,
                request_id=request_id,
                workflow_type=WORKFLOW_INFORMATION,
                message=self._build_information_worker_prompt(message, research_brief),
                participants=[researcher_profile, writer_profile],
            )
            researcher_output = worker_outputs[0]
            writer_output = worker_outputs[1]
            if self._is_invalid_writer_output(writer_output, researcher_output):
                writer_output = self._run_writer_recovery(
                    writer_profile=writer_profile,
                    researcher_output=researcher_output,
                )
            writer_started_at = now_iso()
            finished_at = now_iso()
            self.store.update_task(
                writer_task_id,
                input_json={
                    "message": message,
                    "waiting_on_task_id": researcher_task_id,
                    "research_handoff": researcher_output,
                    "delegated_handoff_prompt": self._build_writer_handoff_prompt(
                        researcher_output
                    ),
                },
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=researcher_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={"research_brief": research_brief, "handoff": researcher_output},
                event_type="worker_handoff_ready",
                event_payload={"handoff_for_role": writer_profile.role},
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=writer_task_id,
                from_status=TASK_STATUS_BLOCKED,
                to_status=TASK_STATUS_IN_PROGRESS,
                started_at=writer_started_at,
                event_type="worker_unblocked",
                event_payload={"dependency_task_id": researcher_task_id},
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=writer_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "researcher_task_id": researcher_task_id,
                    "written_response": writer_output,
                },
                event_type="worker_output_completed",
                event_payload={"source_task_id": researcher_task_id},
            )
            summary = self._run_specialist_prompt(
                specialist_profile,
                self._build_informer_review_prompt(
                    message, research_brief, researcher_output, writer_output
                ),
                step="review",
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "research_brief": research_brief,
                    "researcher_task_id": researcher_task_id,
                    "writer_task_id": writer_task_id,
                    "researcher_output": researcher_output,
                    "writer_output": writer_output,
                    "summary": summary,
                },
                event_type="specialist_workflow_completed",
                event_payload={
                    "workflow_type": WORKFLOW_INFORMATION,
                    "worker_task_ids": [researcher_task_id, writer_task_id],
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                event_type="specialist_review_completed",
                payload={
                    "workflow_type": WORKFLOW_INFORMATION,
                    "worker_agent_ids": [researcher_profile.id, writer_profile.id],
                },
            )
            return SpecialistWorkflowResult(
                text=summary,
                task_status=TASK_STATUS_DONE,
                child_task_ids=[researcher_task_id, writer_task_id],
                worker_agent_ids=[researcher_profile.id, writer_profile.id],
                result_json={
                    "workflow_type": WORKFLOW_INFORMATION,
                    "researcher_task_id": researcher_task_id,
                    "writer_task_id": writer_task_id,
                    "summary": summary,
                },
            )
        except Exception as exc:
            finished_at = now_iso()
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=researcher_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
                event_type="worker_failed",
            )
            self.store.update_task(
                writer_task_id,
                error_text=str(exc),
                finished_at=finished_at,
            )
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=specialist_task_id,
                from_status=TASK_STATUS_IN_PROGRESS,
                to_status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
                event_type="specialist_workflow_failed",
                event_payload={"workflow_type": WORKFLOW_INFORMATION},
            )
            failure_text = (
                "Information workflow failed before a complete supervisor review was available. "
                f"Failure: {exc}"
            )
            return SpecialistWorkflowResult(
                text=failure_text,
                task_status=TASK_STATUS_FAILED,
                child_task_ids=[researcher_task_id, writer_task_id],
                worker_agent_ids=[researcher_profile.id, writer_profile.id],
                result_json={
                    "workflow_type": WORKFLOW_INFORMATION,
                    "researcher_task_id": researcher_task_id,
                    "writer_task_id": writer_task_id,
                    "error": str(exc),
                },
            )

    def _select_route(self, message: str) -> RoutingDecision:
        prompt = self._build_manager_route_prompt(message)
        raw = self._run_route_prompt(prompt)
        decision = self._parse_routing_decision(raw, source="prompt")
        if decision is not None:
            return decision

        repair_prompt = (
            "Your previous routing output was invalid. Repair it and return only valid JSON with keys "
            "selected_agent_id, selected_role, reason, execution_mode."
        )
        repaired = self._run_route_prompt(f"{prompt}\n\n{repair_prompt}")
        decision = self._parse_routing_decision(repaired, source="repair")
        if decision is not None:
            return decision

        fallback = self._fallback_route(message)
        debug_log(
            "agent_manager_route_fallback",
            {"message": message, "selected_agent_id": fallback.selected_agent_id},
        )
        return fallback

    def _build_manager_route_prompt(self, message: str) -> str:
        return (
            "You are Chanakya's routing supervisor. Choose exactly one top-level specialist. "
            "Do not solve the request. Do not mention any worker agents. Return only JSON.\n\n"
            "Allowed routing targets:\n"
            "- agent_cto / role cto / execution_mode software_delivery: for software implementation, debugging, architecture, testing, refactoring, engineering delivery.\n"
            "- agent_informer / role informer / execution_mode information_delivery: for research, explanation, writing, factual summaries, non-software tasks.\n\n"
            f"User request: {message}\n\n"
            "Return JSON with this exact schema:\n"
            '{"selected_agent_id":"agent_cto","selected_role":"cto","reason":"...","execution_mode":"software_delivery"}'
        )

    def _build_cto_brief_prompt(self, message: str) -> str:
        return (
            "You are the software-delivery supervisor. Convert the request into a developer-first execution brief. "
            "Do not implement or test directly. Return JSON only with keys implementation_brief, assumptions, risks, testing_focus.\n\n"
            f"User request: {message}"
        )

    def _build_cto_review_prompt(
        self,
        message: str,
        implementation_brief: str,
        developer_output: str,
        tester_output: str,
    ) -> str:
        return (
            "You are the CTO supervisor. Review the developer and tester outputs and return a concise engineering summary. "
            "Do not add unsupported claims. Respond with only the final summary.\n\n"
            f"User request: {message}\n\n"
            f"Implementation brief:\n{implementation_brief}\n\n"
            f"Developer output:\n{developer_output}\n\n"
            f"Tester output:\n{tester_output}"
        )

    def _build_informer_brief_prompt(self, message: str) -> str:
        return (
            "You are the information supervisor. Convert the request into a research-first brief. "
            "Do not produce the final polished answer yourself. Return JSON only with keys research_brief, audience, required_facts, caveats.\n\n"
            f"User request: {message}"
        )

    def _build_informer_review_prompt(
        self,
        message: str,
        research_brief: str,
        researcher_output: str,
        writer_output: str,
    ) -> str:
        return (
            "You are the Informer supervisor. Review the research handoff and written answer for grounding, clarity, and completeness. "
            "Respond with only the final summary that should be passed back to the manager.\n\n"
            f"User request: {message}\n\n"
            f"Research brief:\n{research_brief}\n\n"
            f"Researcher output:\n{researcher_output}\n\n"
            f"Writer output:\n{writer_output}"
        )

    def _build_software_worker_prompt(self, message: str, implementation_brief: str) -> str:
        return (
            "This is a deterministic two-stage software workflow executed in order.\n"
            "Stage 1 agent is the developer. Produce only a structured implementation handoff. Include implementation_summary, assumptions, risks, and testing_focus.\n"
            "Stage 2 agent is the tester. Consume the developer handoff from prior workflow context and produce only a structured validation report. Include validation_summary, checks_performed, defects_or_risks, and pass_fail_recommendation.\n"
            "Each stage must stay within its role boundary and output only its own result.\n\n"
            f"User request: {message}\n\n"
            f"Supervisor implementation brief:\n{implementation_brief}"
        )

    def _build_developer_stage_prompt(self, message: str, implementation_brief: str) -> str:
        return (
            "Research and implement the software change described below. Produce only the developer handoff.\n\n"
            f"Original request: {message}\n\n"
            f"Implementation brief: {implementation_brief}"
        )

    def _build_tester_stage_prompt(self, message: str, implementation_brief: str) -> str:
        return (
            "Validate the implementation after the developer handoff is available. Produce only the tester report.\n\n"
            f"Original request: {message}\n\n"
            f"Implementation brief: {implementation_brief}"
        )

    def _build_tester_handoff_prompt(
        self,
        message: str,
        implementation_brief: str,
        developer_output: str,
    ) -> str:
        return (
            "The developer completed the implementation handoff below. Validate it and produce a structured tester report.\n\n"
            f"Original request: {message}\n\n"
            f"Implementation brief: {implementation_brief}\n\n"
            f"Developer handoff: {developer_output}"
        )

    def _build_information_worker_prompt(self, message: str, research_brief: str) -> str:
        return (
            "This is a deterministic two-stage information workflow executed in order.\n"
            "Stage 1 agent is the researcher. Produce only a structured research handoff with facts, references_or_sources, uncertainties, and notes_for_writer.\n"
            "Stage 2 agent is the writer. Consume the researcher handoff from prior workflow context and produce only the polished user-facing answer.\n"
            "Each stage must stay within its role boundary and output only its own result.\n\n"
            f"User request: {message}\n\n"
            f"Supervisor research brief:\n{research_brief}"
        )

    def _build_researcher_stage_prompt(self, message: str, research_brief: str) -> str:
        return (
            "Research the topic below and produce only a structured research handoff.\n\n"
            f"Original request: {message}\n\n"
            f"Research brief: {research_brief}"
        )

    def _build_writer_handoff_prompt(
        self,
        researcher_output: str,
    ) -> str:
        return (
            "I have collected the following research. Turn it into a beautiful, clear, well-structured response without inventing unsupported claims.\n\n"
            f"Research handoff: {researcher_output}"
        )

    def _build_writer_repair_prompt(
        self,
        researcher_output: str,
    ) -> str:
        return (
            "Write a short final biography for the user using the research below. "
            "Do not repeat the research handoff verbatim. Do not include labels such as Researcher Handoff, "
            "Writer Notes, Verification Points, or Process Summary. Return only the final biography in polished prose.\n\n"
            f"Research handoff: {researcher_output}"
        )

    def _generate_manager_summary(
        self,
        *,
        root_message: str,
        route: RoutingDecision,
        specialist_profile: AgentProfileModel,
        specialist_result: SpecialistWorkflowResult,
    ) -> str:
        fallback_summary = specialist_result.text.strip() or (
            "The delegated workflow completed but did not produce a usable supervisor summary."
        )
        prompt = (
            "You are Chanakya's Agent Manager. Produce the final user-facing summary. "
            "Do not mention internal routing mechanics unless they are needed to explain a failure. Respond with only the final summary.\n\n"
            f"User request: {root_message}\n"
            f"Selected specialist: {specialist_profile.id}\n"
            f"Execution mode: {route.execution_mode}\n"
            f"Specialist status: {specialist_result.task_status}\n"
            f"Specialist summary:\n{specialist_result.text}"
        )
        try:
            summary = self._run_summary_prompt(prompt)
        except Exception as exc:
            debug_log("agent_manager_summary_failed", {"error": str(exc)})
            return fallback_summary
        cleaned = summary.strip()
        return cleaned or fallback_summary

    def _run_route_prompt(self, prompt: str) -> str:
        if self.route_runner is not None:
            return str(self.route_runner(prompt))
        return self._run_profile_prompt(self.manager_profile, prompt)

    def _run_summary_prompt(self, prompt: str) -> str:
        if self.summary_runner is not None:
            return str(self.summary_runner(prompt))
        return self._run_profile_prompt(self.manager_profile, prompt)

    def _run_specialist_prompt(
        self,
        profile: AgentProfileModel,
        prompt: str,
        *,
        step: str,
    ) -> str:
        if self.specialist_runner is not None:
            return str(self.specialist_runner(profile, prompt, step))
        return self._run_profile_prompt(profile, prompt)

    def _run_writer_recovery(
        self,
        *,
        writer_profile: AgentProfileModel,
        researcher_output: str,
    ) -> str:
        handoff_prompt = self._build_writer_handoff_prompt(
            researcher_output,
        )
        recovered = self._run_profile_prompt(writer_profile, handoff_prompt).strip()
        if self._is_invalid_writer_output(recovered, researcher_output):
            repair_prompt = self._build_writer_repair_prompt(
                researcher_output,
            )
            recovered = self._run_profile_prompt(writer_profile, repair_prompt).strip()
        if self._is_invalid_writer_output(recovered, researcher_output):
            raise ValueError(
                "Writer produced invalid or echoed output instead of a polished response"
            )
        return recovered

    def _run_profile_prompt(self, profile: AgentProfileModel, prompt: str) -> str:
        return run_in_maf_loop(self._run_profile_prompt_async(profile, prompt))

    async def _run_profile_prompt_async(self, profile: AgentProfileModel, prompt: str) -> str:
        agent, _ = build_profile_agent(
            profile,
            self.session_factory,
            client=self.client,
            include_history=False,
        )
        response = await agent.run(Message(role="user", text=prompt), options={"store": False})
        return str(response).strip()

    def _run_sequential_workflow(
        self,
        *,
        session_id: str,
        request_id: str,
        workflow_type: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> list[str]:
        if self.workflow_runner is not None:
            result = self.workflow_runner(
                session_id, request_id, workflow_type, message, participants
            )
            if isinstance(result, list):
                return [str(item).strip() for item in result]
            raise ValueError("workflow runner must return a list of stage outputs")
        return run_in_maf_loop(
            self._run_sequential_workflow_async(
                request_id=request_id,
                workflow_type=workflow_type,
                message=message,
                participants=participants,
            )
        )

    async def _run_sequential_workflow_async(
        self,
        *,
        request_id: str,
        workflow_type: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> list[str]:
        debug_log(
            "agent_manager_sequential_workflow_start",
            {
                "request_id": request_id,
                "workflow_type": workflow_type,
                "participant_ids": [profile.id for profile in participants],
            },
        )
        participant_agents = [
            build_profile_agent(
                profile,
                self.session_factory,
                client=self.client,
                include_history=False,
            )[0]
            for profile in participants
        ]
        workflow = SequentialBuilder(
            participants=participant_agents,
            intermediate_outputs=True,
        ).build()
        result = await workflow.run(
            message=Message(
                role="user", text=message, additional_properties={"request_id": request_id}
            ),
            include_status_events=True,
        )
        texts = self._extract_stage_outputs(result)
        if len(texts) < len(participants):
            raise ValueError(
                f"Sequential workflow returned {len(texts)} outputs for {len(participants)} participants"
            )
        return texts[: len(participants)]

    def _extract_stage_outputs(self, result: Any) -> list[str]:
        outputs = result.get_outputs()
        stage_texts: list[str] = []
        for output in outputs:
            flattened = self._flatten_output_text(output)
            if flattened:
                stage_texts.append(flattened)
        if stage_texts:
            return stage_texts
        timeline = getattr(result, "status_timeline", None)
        if callable(timeline):
            timeline_events = timeline()
            if isinstance(timeline_events, list):
                for event in timeline_events:
                    value = getattr(event, "value", None)
                    text = self._flatten_output_text(value)
                    if text:
                        stage_texts.append(text)
        return stage_texts

    def _flatten_output_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            try:
                return json.dumps(value)
            except TypeError:
                return str(value).strip()
        if isinstance(value, list):
            parts = [self._flatten_output_text(item) for item in value]
            non_empty = [part for part in parts if part]
            return non_empty[-1] if non_empty else ""
        text = getattr(value, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        message = getattr(value, "message", None)
        if isinstance(message, str) and message.strip():
            return message.strip()
        content = getattr(value, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        output = getattr(value, "output", None)
        if isinstance(output, str) and output.strip():
            return output.strip()
        return ""

    def _is_invalid_worker_output(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        invalid_markers = [
            "This is a deterministic two-stage",
            "Stage 1 agent is the researcher",
            "Stage 2 agent is the writer",
            "<agent_framework._types.Message object",
        ]
        return any(marker in stripped for marker in invalid_markers)

    def _is_invalid_writer_output(self, text: str, research_handoff: str) -> bool:
        stripped = text.strip()
        if self._is_invalid_worker_output(stripped):
            return True
        lowered = stripped.lower()
        invalid_writer_markers = [
            "researcher handoff",
            "prepared for: stage 2 writer",
            "verification points for writer",
            "writer's notes",
            "end of research handoff",
        ]
        if any(marker in lowered for marker in invalid_writer_markers):
            return True
        if self._normalized_similarity(stripped, research_handoff) >= 0.82:
            return True
        return False

    def _normalized_similarity(self, left: str, right: str) -> float:
        left_normalized = " ".join(left.lower().split())
        right_normalized = " ".join(right.lower().split())
        if not left_normalized or not right_normalized:
            return 0.0
        return SequenceMatcher(None, left_normalized, right_normalized).ratio()

    def _parse_routing_decision(self, raw: str, *, source: str) -> RoutingDecision | None:
        payload = self._extract_json_object(raw)
        if payload is None:
            return None
        selected_agent_id = str(payload.get("selected_agent_id", "")).strip()
        selected_role = str(payload.get("selected_role", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        execution_mode = str(payload.get("execution_mode", "")).strip()
        if selected_agent_id not in {"agent_cto", "agent_informer"}:
            return None
        if selected_agent_id == "agent_cto" and selected_role != "cto":
            return None
        if selected_agent_id == "agent_informer" and selected_role != "informer":
            return None
        if execution_mode not in {WORKFLOW_SOFTWARE, WORKFLOW_INFORMATION}:
            return None
        if not reason:
            return None
        return RoutingDecision(
            selected_agent_id=selected_agent_id,
            selected_role=selected_role,
            reason=reason,
            execution_mode=execution_mode,
            source=source,
        )

    def _extract_json_object(self, raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if not text:
            return None
        candidates = [text]
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start : end + 1])
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _fallback_route(self, message: str) -> RoutingDecision:
        lowered = message.lower()
        software_markers = [
            "implement",
            "code",
            "build",
            "debug",
            "bug",
            "fix",
            "test",
            "refactor",
            "architecture",
            "api",
            "database",
            "python",
            "flask",
            "frontend",
            "backend",
        ]
        if any(marker in lowered for marker in software_markers):
            return RoutingDecision(
                selected_agent_id="agent_cto",
                selected_role="cto",
                reason="The request is primarily about implementing, debugging, or validating software work.",
                execution_mode=WORKFLOW_SOFTWARE,
                source="fallback",
            )
        return RoutingDecision(
            selected_agent_id="agent_informer",
            selected_role="informer",
            reason="The request is best handled as research, explanation, or non-software writing.",
            execution_mode=WORKFLOW_INFORMATION,
            source="fallback",
        )

    def _pick_worker(self, role: str) -> AgentProfileModel:
        matches = self.store.find_active_agents_by_role(role)
        if matches:
            return matches[0]
        raise KeyError(f"No active agent found for role: {role}")

    def _create_child_task(
        self,
        *,
        request_id: str,
        parent_task_id: str,
        owner_profile: AgentProfileModel,
        title: str,
        summary: str,
        task_type: str,
        session_id: str,
        started: bool = False,
        status: str = TASK_STATUS_IN_PROGRESS,
        dependencies: list[str] | None = None,
        input_json: dict[str, Any] | None = None,
    ) -> str:
        task_id = make_id("task")
        self.store.create_task(
            task_id=task_id,
            request_id=request_id,
            parent_task_id=parent_task_id,
            title=title,
            summary=summary,
            status=TASK_STATUS_CREATED,
            owner_agent_id=owner_profile.id,
            task_type=task_type,
            dependencies=dependencies or [],
            input_json=input_json or {},
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            event_type="task_created",
            payload={
                "title": title,
                "owner_agent_id": owner_profile.id,
                "task_type": task_type,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            event_type="workflow_task_discovered",
            payload={
                "parent_task_id": parent_task_id,
                "owner_agent_id": owner_profile.id,
                "owner_agent_name": owner_profile.name,
                "task_type": task_type,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            event_type="task_owner_assigned",
            payload={
                "owner_agent_id": owner_profile.id,
                "owner_agent_name": owner_profile.name,
                "task_type": task_type,
            },
        )
        if status != TASK_STATUS_CREATED:
            self._transition_task(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                from_status=TASK_STATUS_CREATED,
                to_status=status,
                started_at=now_iso() if started else None,
                event_type="task_started" if started else "task_state_set",
            )
        return task_id

    def _transition_task(
        self,
        *,
        session_id: str,
        request_id: str,
        task_id: str,
        from_status: str,
        to_status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        result_json: dict[str, Any] | None = None,
        error_text: str | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.update_task(
            task_id,
            status=to_status,
            started_at=started_at,
            finished_at=finished_at,
            result_json=result_json,
            error_text=error_text,
        )
        payload = {
            "from_status": from_status,
            "to_status": to_status,
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error_text,
        }
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            event_type="task_status_changed",
            payload=payload,
        )
        if event_type is not None:
            merged_payload = dict(payload)
            if event_payload is not None:
                merged_payload.update(event_payload)
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                event_type=event_type,
                payload=merged_payload,
            )
