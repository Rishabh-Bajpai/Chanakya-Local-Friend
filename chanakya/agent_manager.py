from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatClient
from agent_framework.orchestrations import GroupChatBuilder
from sqlalchemy.orm import Session, sessionmaker

from chanakya.debug import debug_log
from chanakya.domain import (
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    make_id,
    now_iso,
)
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import ChanakyaStore


WORKFLOW_CHAT = "chat"


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
class GroupChatExtractionResult:
    text: str
    extracted: bool
    source: str


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
        self.group_chat_runner: Any | None = None
        self.summary_runner: Any | None = None

    def should_delegate(self, message: str) -> bool:
        lowered = message.lower()
        markers = [
            "implement and test",
            "build and verify",
            "write code and test",
            "research and summarize",
            "compare approaches",
            "compare",
            "debate",
            "brainstorm",
            "plan then implement",
            "research then implement",
            "break into tasks",
            "break this into tasks",
            "decompose",
            "split this into",
        ]
        return any(marker in lowered for marker in markers)

    def select_workflow(self, message: str) -> str:
        return WORKFLOW_CHAT

    def execute(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        message: str,
    ) -> ManagerRunResult:
        workflow_type = WORKFLOW_CHAT
        participants = self._select_participants(message)
        child_task_ids: list[str] = []

        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="manager_delegated",
            payload={
                "manager_agent_id": self.manager_profile.id,
                "workflow_type": workflow_type,
                "message": message,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="workflow_selected",
            payload={
                "workflow_type": workflow_type,
                "manager_agent_id": self.manager_profile.id,
                "participant_roles": [profile.role for profile in participants],
            },
        )
        self.store.update_task(
            root_task_id,
            summary="Delegated via manager-supervised chat workflow",
            owner_agent_id=self.manager_profile.id,
            input_json={
                "message": message,
                "workflow_type": workflow_type,
                "participant_roles": [profile.role for profile in participants],
            },
        )

        for profile in participants:
            task_id = make_id("task")
            child_task_ids.append(task_id)
            self.store.create_task(
                task_id=task_id,
                request_id=request_id,
                parent_task_id=root_task_id,
                title=f"{profile.name} Contribution",
                summary="Participate in the manager-supervised discussion and contribute your perspective.",
                status=TASK_STATUS_IN_PROGRESS,
                owner_agent_id=profile.id,
                task_type=f"{profile.role}_discussion",
                input_json={
                    "workflow_type": workflow_type,
                    "root_message": message,
                    "participant_role": profile.role,
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                event_type="workflow_task_discovered",
                payload={
                    "workflow_type": workflow_type,
                    "parent_task_id": root_task_id,
                    "owner_agent_id": profile.id,
                    "task_type": f"{profile.role}_discussion",
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                event_type="workflow_agent_assigned",
                payload={
                    "owner_agent_id": profile.id,
                    "owner_agent_name": profile.name,
                    "role": profile.role,
                },
            )

        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="workflow_started",
            payload={
                "workflow_type": workflow_type,
                "child_task_ids": child_task_ids,
                "participant_roles": [profile.role for profile in participants],
            },
        )

        try:
            extraction = self._run_group_chat(
                session_id=session_id,
                request_id=request_id,
                message=message,
                participants=participants,
            )
        except Exception as exc:
            finished_at = now_iso()
            for task_id in child_task_ids:
                self.store.update_task(
                    task_id,
                    status=TASK_STATUS_FAILED,
                    error_text=str(exc),
                    finished_at=finished_at,
                )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="workflow_failed",
                payload={
                    "workflow_type": workflow_type,
                    "error": str(exc),
                    "finished_at": finished_at,
                },
            )
            return ManagerRunResult(
                text=f"Delegated chat workflow failed: {exc}",
                workflow_type=workflow_type,
                child_task_ids=child_task_ids,
                manager_agent_id=self.manager_profile.id,
                worker_agent_ids=[profile.id for profile in participants],
                task_status=TASK_STATUS_FAILED,
                result_json={
                    "workflow_type": workflow_type,
                    "child_task_ids": child_task_ids,
                    "error": str(exc),
                },
            )

        finished_at = now_iso()
        for task_id, profile in zip(child_task_ids, participants, strict=True):
            self.store.update_task(
                task_id,
                status=TASK_STATUS_DONE,
                finished_at=finished_at,
                result_json={
                    "workflow_type": workflow_type,
                    "worker_agent_id": profile.id,
                    "worker_agent_name": profile.name,
                    "conversation_excerpt": extraction.text,
                    "conversation_extracted": extraction.extracted,
                    "conversation_source": extraction.source,
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                event_type="workflow_phase_completed",
                payload={
                    "workflow_type": workflow_type,
                    "owner_agent_id": profile.id,
                    "finished_at": finished_at,
                },
            )
        final_summary = self._generate_manager_summary(
            root_message=message,
            participants=participants,
            extraction=extraction,
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="workflow_aggregation_completed",
            payload={
                "workflow_type": workflow_type,
                "child_task_ids": child_task_ids,
                "conversation_extracted": extraction.extracted,
                "conversation_source": extraction.source,
            },
        )
        return ManagerRunResult(
            text=final_summary,
            workflow_type=workflow_type,
            child_task_ids=child_task_ids,
            manager_agent_id=self.manager_profile.id,
            worker_agent_ids=[profile.id for profile in participants],
            task_status=TASK_STATUS_DONE,
            result_json={
                "workflow_type": workflow_type,
                "child_task_ids": child_task_ids,
                "worker_agent_ids": [profile.id for profile in participants],
                "conversation": extraction.text,
                "conversation_extracted": extraction.extracted,
                "conversation_source": extraction.source,
                "summary": final_summary,
            },
        )

    def _select_participants(self, message: str) -> list[AgentProfileModel]:
        lowered = message.lower()
        if any(token in lowered for token in ["implement", "build", "code", "test", "verify"]):
            return [self._pick_worker("developer"), self._pick_worker("tester")]
        if any(
            token in lowered
            for token in ["research", "summarize", "compare", "brainstorm", "write"]
        ):
            return [self._pick_worker("researcher"), self._pick_worker("writer")]
        return [self._pick_worker("developer"), self._pick_worker("researcher")]

    def _pick_worker(self, role: str) -> AgentProfileModel:
        matches = self.store.find_active_agents_by_role(role)
        if matches:
            return matches[0]
        return self.store.get_agent_profile("agent_chanakya")

    def _summarize_manager_result(
        self,
        root_message: str,
        participants: list[AgentProfileModel],
        extraction: GroupChatExtractionResult,
    ) -> str:
        participant_names = ", ".join(profile.name for profile in participants)
        if extraction.extracted:
            summary_text = extraction.text
        else:
            summary_text = (
                "The delegated workflow completed, but the orchestrator did not return a usable final summary. "
                "Participant tasks ran successfully, but the final multi-agent discussion output could not be extracted."
            )
        return (
            f"Delegated via chat workflow with {participant_names}.\n"
            f"Request: {root_message}\n"
            f"Conversation summary:\n{summary_text}"
        )

    def _generate_manager_summary(
        self,
        *,
        root_message: str,
        participants: list[AgentProfileModel],
        extraction: GroupChatExtractionResult,
    ) -> str:
        fallback_summary = self._summarize_manager_result(root_message, participants, extraction)
        participant_names = ", ".join(profile.name for profile in participants)
        prompt = (
            "You are Chanakya's Agent Manager. Produce a concise, user-facing final summary for the delegated request. "
            "If the group chat transcript is weak or incomplete, still provide the best possible grounded summary based on the available information.\n\n"
            f"User request: {root_message}\n"
            f"Workflow type: {WORKFLOW_CHAT}\n"
            f"Participants: {participant_names}\n"
            f"Transcript extracted: {extraction.extracted}\n"
            f"Transcript source: {extraction.source}\n"
            f"Transcript or fallback text:\n{extraction.text}\n\n"
            "Respond with only the final summary."
        )
        try:
            summary = self._run_manager_summary(prompt)
        except Exception as exc:
            debug_log(
                "agent_manager_summary_failed",
                {
                    "error": str(exc),
                    "transcript_source": extraction.source,
                    "transcript_extracted": extraction.extracted,
                },
            )
            return fallback_summary
        cleaned = summary.strip()
        return cleaned or fallback_summary

    def _run_manager_summary(self, prompt: str) -> str:
        if self.summary_runner is not None:
            return str(self.summary_runner(prompt))
        return run_in_maf_loop(self._run_manager_summary_async(prompt))

    async def _run_manager_summary_async(self, prompt: str) -> str:
        manager_agent = Agent(
            client=self.client,
            name=self.manager_profile.name,
            instructions=self.manager_profile.system_prompt,
        )
        response = await manager_agent.run(
            Message(role="user", text=prompt),
            options={"store": False},
        )
        return str(response).strip()

    def _run_group_chat(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> GroupChatExtractionResult:
        if self.group_chat_runner is not None:
            return GroupChatExtractionResult(
                text=str(self.group_chat_runner(session_id, request_id, message, participants)),
                extracted=True,
                source="test_runner",
            )
        return run_in_maf_loop(
            self._run_group_chat_async(
                session_id=session_id,
                request_id=request_id,
                message=message,
                participants=participants,
            )
        )

    @staticmethod
    def _extract_text_from_workflow_result(result: Any) -> GroupChatExtractionResult:
        outputs = result.get_outputs()
        if outputs:
            last_output = outputs[-1]
            if isinstance(last_output, list):
                parts: list[str] = []
                for item in last_output:
                    author = getattr(item, "author_name", None) or getattr(
                        item, "role", "assistant"
                    )
                    text = getattr(item, "text", None)
                    if isinstance(text, str) and text.strip():
                        parts.append(f"{author}: {text}")
                if parts:
                    return GroupChatExtractionResult(
                        text="\n".join(parts),
                        extracted=True,
                        source="outputs_list",
                    )
            if isinstance(last_output, str) and last_output.strip():
                return GroupChatExtractionResult(
                    text=last_output.strip(),
                    extracted=True,
                    source="outputs_string",
                )
            if isinstance(last_output, dict) and last_output:
                return GroupChatExtractionResult(
                    text=str(last_output),
                    extracted=True,
                    source="outputs_dict",
                )

        timeline = getattr(result, "status_timeline", None)
        if callable(timeline):
            timeline_events = timeline()
            if isinstance(timeline_events, list):
                for event in reversed(timeline_events):
                    value = getattr(event, "value", None)
                    if isinstance(value, str) and value.strip():
                        return GroupChatExtractionResult(
                            text=value.strip(),
                            extracted=True,
                            source="status_timeline_string",
                        )
                    event_text = getattr(value, "text", None)
                    if isinstance(event_text, str) and event_text.strip():
                        return GroupChatExtractionResult(
                            text=event_text.strip(),
                            extracted=True,
                            source="status_timeline_text",
                        )

        final_state_getter = getattr(result, "get_final_state", None)
        if callable(final_state_getter):
            final_state = final_state_getter()
            if final_state is not None:
                final_text = getattr(final_state, "message", None) or getattr(
                    final_state, "output", None
                )
                if isinstance(final_text, str) and final_text.strip():
                    return GroupChatExtractionResult(
                        text=final_text.strip(),
                        extracted=True,
                        source="final_state",
                    )

        return GroupChatExtractionResult(
            text="Group chat completed, but no final conversation output was produced.",
            extracted=False,
            source="none",
        )

    async def _run_group_chat_async(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> GroupChatExtractionResult:
        debug_log(
            "agent_manager_group_chat_start",
            {
                "session_id": session_id,
                "request_id": request_id,
                "participant_ids": [profile.id for profile in participants],
            },
        )
        participant_agents = [
            Agent(
                client=self.client,
                name=profile.name,
                instructions=profile.system_prompt,
            )
            for profile in participants
        ]
        manager_agent = Agent(
            client=self.client,
            name=self.manager_profile.name,
            instructions=self.manager_profile.system_prompt,
        )
        workflow = GroupChatBuilder(
            participants=participant_agents,
            orchestrator_agent=manager_agent,
            max_rounds=4,
        ).build()
        try:
            result = await workflow.run(
                message=Message(
                    role="user", text=message, additional_properties={"request_id": request_id}
                ),
                include_status_events=True,
            )
        except Exception as exc:
            error_text = str(exc)
            if "AgentOrchestrationOutput" not in error_text:
                raise
            debug_log(
                "agent_manager_group_chat_output_fallback",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": error_text,
                },
            )
            return GroupChatExtractionResult(
                text="Group chat orchestration started, but the orchestrator did not emit a parseable final output.",
                extracted=False,
                source="orchestrator_parse_error",
            )
        return self._extract_text_from_workflow_result(result)
