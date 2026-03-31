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
            conversation_text = self._run_group_chat(
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
                    "conversation_excerpt": conversation_text,
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

        final_summary = self._summarize_manager_result(message, participants, conversation_text)
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="workflow_aggregation_completed",
            payload={
                "workflow_type": workflow_type,
                "child_task_ids": child_task_ids,
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
                "conversation": conversation_text,
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
        conversation_text: str,
    ) -> str:
        participant_names = ", ".join(profile.name for profile in participants)
        return (
            f"Delegated via chat workflow with {participant_names}.\n"
            f"Request: {root_message}\n"
            f"Conversation summary:\n{conversation_text}"
        )

    def _run_group_chat(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> str:
        if self.group_chat_runner is not None:
            return str(self.group_chat_runner(session_id, request_id, message, participants))
        return run_in_maf_loop(
            self._run_group_chat_async(
                session_id=session_id,
                request_id=request_id,
                message=message,
                participants=participants,
            )
        )

    async def _run_group_chat_async(
        self,
        *,
        session_id: str,
        request_id: str,
        message: str,
        participants: list[AgentProfileModel],
    ) -> str:
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
        result = await workflow.run(
            message=Message(
                role="user", text=message, additional_properties={"request_id": request_id}
            ),
            include_status_events=True,
        )
        outputs = result.get_outputs()
        conversation_items = outputs[-1] if outputs else []
        parts: list[str] = []
        for item in conversation_items:
            author = getattr(item, "author_name", None) or getattr(item, "role", "assistant")
            text = getattr(item, "text", None)
            if text:
                parts.append(f"{author}: {text}")
        return "\n".join(parts).strip() or "No group chat output produced."
