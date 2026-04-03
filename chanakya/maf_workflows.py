import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, response_handler
from agent_framework._workflows._checkpoint import FileCheckpointStorage
from typing_extensions import Never

from chanakya.domain import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_WAITING_INPUT,
    now_iso,
)
from chanakya.model import AgentProfileModel
from chanakya.store import ChanakyaStore


@dataclass(slots=True)
class ClarificationRequest:
    question: str
    reason: str
    task_id: str


@dataclass(slots=True)
class ClarificationResponse:
    answer: str


@dataclass(slots=True)
class PendingInputState:
    checkpoint_id: str
    pending_request_id: str
    prompt: str
    reason: str


@dataclass(slots=True)
class SoftwareWorkerWorkflowResult:
    status: str
    developer_output: str | None = None
    tester_output: str | None = None
    developer_temporary_agent_ids: list[str] = field(default_factory=list)
    tester_temporary_agent_ids: list[str] = field(default_factory=list)
    pending_input: PendingInputState | None = None
    error_text: str | None = None


class _DeveloperExecutor(Executor):
    def __init__(
        self,
        *,
        manager: Any,
        store: ChanakyaStore,
        session_id: str,
        request_id: str,
        message: str,
        implementation_brief: str,
        developer_profile: AgentProfileModel,
        tester_profile: AgentProfileModel,
        developer_task_id: str,
        tester_task_id: str,
    ) -> None:
        super().__init__(id=f"developer_step_{developer_task_id}")
        self.manager = manager
        self.store = store
        self.session_id = session_id
        self.request_id = request_id
        self.message = message
        self.implementation_brief = implementation_brief
        self.developer_profile = developer_profile
        self.tester_profile = tester_profile
        self.developer_task_id = developer_task_id
        self.tester_task_id = tester_task_id

    @handler
    async def run(self, payload: dict, ctx: WorkflowContext[dict]) -> None:
        await self._run_stage(payload, ctx, clarification_answer=None)

    @response_handler(request=ClarificationRequest, response=ClarificationResponse, output=dict)
    async def handle_clarification(
        self,
        original_request: ClarificationRequest,
        response: ClarificationResponse,
        ctx: WorkflowContext[dict],
    ) -> None:
        payload = cast_payload(ctx.get_state("developer_payload", {}))
        payload["clarification_answer"] = response.answer.strip()
        await self._run_stage(payload, ctx, clarification_answer=response.answer.strip())

    async def _run_stage(
        self,
        payload: dict,
        ctx: WorkflowContext[dict],
        *,
        clarification_answer: str | None,
    ) -> None:
        developer_prompt = str(payload["developer_prompt"])
        ctx.set_state("developer_payload", payload)
        developer_task = self.store.get_task(self.developer_task_id)
        if developer_task.status == TASK_STATUS_WAITING_INPUT:
            resumed_at = now_iso()
            self.manager._transition_task(
                session_id=self.session_id,
                request_id=self.request_id,
                task_id=self.developer_task_id,
                from_status=TASK_STATUS_WAITING_INPUT,
                to_status=TASK_STATUS_IN_PROGRESS,
                started_at=resumed_at,
                event_type="task_resumed",
                event_payload={"pending_request_id": payload.get("maf_pending_request_id")},
            )

        clarification_decision = self.manager._decide_worker_clarification(
            self.developer_profile,
            self.message,
            developer_prompt,
            clarification_answer=clarification_answer,
        )
        if clarification_decision is not None:
            waiting_at = now_iso()
            developer_input = dict(developer_task.input_json or {})
            developer_input.update(
                {
                    "maf_pending_prompt": clarification_decision["question"],
                    "maf_pending_reason": clarification_decision["reason"],
                    "clarification_required": True,
                }
            )
            self.store.update_task(
                self.developer_task_id,
                status=TASK_STATUS_WAITING_INPUT,
                input_json=developer_input,
                started_at=developer_task.started_at or waiting_at,
            )
            self.store.create_task_event(
                session_id=self.session_id,
                request_id=self.request_id,
                task_id=self.developer_task_id,
                event_type="user_input_requested",
                payload={
                    "question": clarification_decision["question"],
                    "reason": clarification_decision["reason"],
                    "waiting_at": waiting_at,
                },
            )
            await ctx.request_info(
                ClarificationRequest(
                    question=clarification_decision["question"],
                    reason=clarification_decision["reason"],
                    task_id=self.developer_task_id,
                ),
                ClarificationResponse,
            )
            return

        effective_prompt = developer_prompt
        if clarification_answer:
            effective_prompt = f"{developer_prompt}\n\nUser clarification received:\n{clarification_answer.strip()}"

        developer_input = dict(developer_task.input_json or {})
        developer_input.update(
            {
                "effective_prompt": effective_prompt,
                "clarification_answer": clarification_answer,
                "maf_pending_prompt": None,
                "maf_pending_reason": None,
                "maf_pending_request_id": None,
                "maf_checkpoint_id": None,
                "clarification_required": False,
            }
        )
        self.store.update_task(self.developer_task_id, input_json=developer_input)

        developer_result = self.manager._run_worker_with_optional_subagents(
            session_id=self.session_id,
            request_id=self.request_id,
            worker_profile=self.developer_profile,
            worker_task_id=self.developer_task_id,
            message=self.message,
            effective_prompt=effective_prompt,
        )
        developer_output = developer_result.text
        developer_finished_at = now_iso()
        tester_handoff_prompt = self.manager._build_tester_handoff_prompt(
            self.message,
            self.implementation_brief,
            developer_output,
        )
        tester_input = dict(self.store.get_task(self.tester_task_id).input_json or {})
        tester_input.update(
            {
                "message": self.message,
                "supervisor_brief": self.implementation_brief,
                "effective_prompt": tester_handoff_prompt,
                "waiting_on_task_id": self.developer_task_id,
                "developer_handoff": developer_output,
                "delegated_handoff_prompt": tester_handoff_prompt,
                "temporary_agent_ids": developer_result.temporary_agent_ids,
            }
        )
        self.store.update_task(self.tester_task_id, input_json=tester_input)
        self.manager._transition_task(
            session_id=self.session_id,
            request_id=self.request_id,
            task_id=self.developer_task_id,
            from_status=TASK_STATUS_IN_PROGRESS,
            to_status=TASK_STATUS_DONE,
            finished_at=developer_finished_at,
            result_json={
                "implementation_brief": self.implementation_brief,
                "handoff": developer_output,
                "temporary_agent_ids": developer_result.temporary_agent_ids,
            },
            event_type="worker_handoff_ready",
            event_payload={"handoff_for_role": self.tester_profile.role},
        )
        await ctx.send_message(
            {
                "developer_status": TASK_STATUS_DONE,
                "developer_output": developer_output,
                "developer_temporary_agent_ids": developer_result.temporary_agent_ids,
                "tester_handoff_prompt": tester_handoff_prompt,
            }
        )


class _TesterExecutor(Executor):
    def __init__(
        self,
        *,
        manager: Any,
        store: ChanakyaStore,
        session_id: str,
        request_id: str,
        message: str,
        implementation_brief: str,
        developer_task_id: str,
        tester_task_id: str,
        tester_profile: AgentProfileModel,
    ) -> None:
        super().__init__(id=f"tester_step_{tester_task_id}")
        self.manager = manager
        self.store = store
        self.session_id = session_id
        self.request_id = request_id
        self.message = message
        self.implementation_brief = implementation_brief
        self.developer_task_id = developer_task_id
        self.tester_task_id = tester_task_id
        self.tester_profile = tester_profile

    @handler
    async def run(self, payload: dict, ctx: WorkflowContext[Never, dict]) -> None:
        developer_status = str(payload.get("developer_status", ""))
        tester_task = self.store.get_task(self.tester_task_id)
        if developer_status != TASK_STATUS_DONE:
            if tester_task.status != TASK_STATUS_BLOCKED:
                self.manager._transition_task(
                    session_id=self.session_id,
                    request_id=self.request_id,
                    task_id=self.tester_task_id,
                    from_status=tester_task.status,
                    to_status=TASK_STATUS_BLOCKED,
                    event_type="workflow_dependency_recorded",
                    event_payload={"dependency_task_id": self.developer_task_id},
                )
            await ctx.yield_output(
                {
                    "status": TASK_STATUS_FAILED,
                    "error": f"Developer stage did not complete successfully: {developer_status}",
                }
            )
            return

        tester_started_at = now_iso()
        self.manager._transition_task(
            session_id=self.session_id,
            request_id=self.request_id,
            task_id=self.tester_task_id,
            from_status=TASK_STATUS_BLOCKED,
            to_status=TASK_STATUS_IN_PROGRESS,
            started_at=tester_started_at,
            event_type="worker_unblocked",
            event_payload={"dependency_task_id": self.developer_task_id},
        )
        tester_result = self.manager._run_worker_with_optional_subagents(
            session_id=self.session_id,
            request_id=self.request_id,
            worker_profile=self.tester_profile,
            worker_task_id=self.tester_task_id,
            message=self.message,
            effective_prompt=str(payload.get("tester_handoff_prompt") or ""),
        )
        tester_output = tester_result.text
        developer_output = str(payload.get("developer_output") or "")
        if self.manager._is_invalid_tester_output(tester_output, developer_output):
            tester_output = self.manager._run_tester_recovery(
                tester_profile=self.tester_profile,
                message=self.message,
                implementation_brief=self.implementation_brief,
                developer_output=developer_output,
            )
        finished_at = now_iso()
        self.manager._transition_task(
            session_id=self.session_id,
            request_id=self.request_id,
            task_id=self.tester_task_id,
            from_status=TASK_STATUS_IN_PROGRESS,
            to_status=TASK_STATUS_DONE,
            finished_at=finished_at,
            result_json={
                "developer_task_id": self.developer_task_id,
                "validation_report": tester_output,
                "temporary_agent_ids": tester_result.temporary_agent_ids,
            },
            event_type="worker_validation_completed",
            event_payload={"validated_task_id": self.developer_task_id},
        )
        await ctx.yield_output(
            {
                "status": TASK_STATUS_DONE,
                "developer_output": developer_output,
                "tester_output": tester_output,
                "developer_temporary_agent_ids": list(
                    payload.get("developer_temporary_agent_ids") or []
                ),
                "tester_temporary_agent_ids": tester_result.temporary_agent_ids,
            }
        )


class ManagerWorkflowRuntime:
    def __init__(self, store: ChanakyaStore, checkpoint_dir: Path) -> None:
        self.store = store
        self.checkpoint_storage = FileCheckpointStorage(checkpoint_dir)

    def start_software_workflow(
        self,
        *,
        manager: Any,
        session_id: str,
        request_id: str,
        workflow_name: str,
        message: str,
        implementation_brief: str,
        developer_profile: AgentProfileModel,
        tester_profile: AgentProfileModel,
        developer_task_id: str,
        tester_task_id: str,
        developer_prompt: str,
        tester_prompt: str,
    ) -> SoftwareWorkerWorkflowResult:
        payload = {
            "developer_prompt": developer_prompt,
            "tester_prompt": tester_prompt,
        }
        return self._run_async(
            self._run_software_workflow(
                manager=manager,
                session_id=session_id,
                request_id=request_id,
                workflow_name=workflow_name,
                message=message,
                implementation_brief=implementation_brief,
                developer_profile=developer_profile,
                tester_profile=tester_profile,
                developer_task_id=developer_task_id,
                tester_task_id=tester_task_id,
                payload=payload,
                checkpoint_id=None,
                responses=None,
            )
        )

    def resume_software_workflow(
        self,
        *,
        manager: Any,
        session_id: str,
        request_id: str,
        workflow_name: str,
        message: str,
        implementation_brief: str,
        developer_profile: AgentProfileModel,
        tester_profile: AgentProfileModel,
        developer_task_id: str,
        tester_task_id: str,
        checkpoint_id: str,
        pending_request_id: str,
        user_input: str,
    ) -> SoftwareWorkerWorkflowResult:
        return self._run_async(
            self._run_software_workflow(
                manager=manager,
                session_id=session_id,
                request_id=request_id,
                workflow_name=workflow_name,
                message=message,
                implementation_brief=implementation_brief,
                developer_profile=developer_profile,
                tester_profile=tester_profile,
                developer_task_id=developer_task_id,
                tester_task_id=tester_task_id,
                payload=None,
                checkpoint_id=checkpoint_id,
                responses={pending_request_id: ClarificationResponse(answer=user_input)},
            )
        )

    def cancel_waiting_workflow(self, *, checkpoint_id: str) -> bool:
        return bool(self._run_async(self.checkpoint_storage.delete(checkpoint_id)))

    def _run_async(self, coro: Any) -> Any:
        return asyncio.run(coro)

    async def _run_software_workflow(
        self,
        *,
        manager: Any,
        session_id: str,
        request_id: str,
        workflow_name: str,
        message: str,
        implementation_brief: str,
        developer_profile: AgentProfileModel,
        tester_profile: AgentProfileModel,
        developer_task_id: str,
        tester_task_id: str,
        payload: dict[str, Any] | None,
        checkpoint_id: str | None,
        responses: dict[str, ClarificationResponse] | None,
    ) -> SoftwareWorkerWorkflowResult:
        developer = _DeveloperExecutor(
            manager=manager,
            store=self.store,
            session_id=session_id,
            request_id=request_id,
            message=message,
            implementation_brief=implementation_brief,
            developer_profile=developer_profile,
            tester_profile=tester_profile,
            developer_task_id=developer_task_id,
            tester_task_id=tester_task_id,
        )
        tester = _TesterExecutor(
            manager=manager,
            store=self.store,
            session_id=session_id,
            request_id=request_id,
            message=message,
            implementation_brief=implementation_brief,
            developer_task_id=developer_task_id,
            tester_task_id=tester_task_id,
            tester_profile=tester_profile,
        )
        workflow = (
            WorkflowBuilder(
                start_executor=developer,
                checkpoint_storage=self.checkpoint_storage,
                name=workflow_name,
            )
            .add_edge(developer, tester)
            .build()
        )
        if checkpoint_id is not None and responses is not None:
            run_result = await workflow.run(
                responses=responses,
                checkpoint_id=checkpoint_id,
                checkpoint_storage=self.checkpoint_storage,
                include_status_events=True,
            )
        else:
            run_result = await workflow.run(
                payload,
                checkpoint_storage=self.checkpoint_storage,
                include_status_events=True,
            )
        request_events = run_result.get_request_info_events()
        if request_events:
            request_event = request_events[-1]
            latest_checkpoint = await self.checkpoint_storage.get_latest(
                workflow_name=workflow_name
            )
            if latest_checkpoint is None:
                raise RuntimeError("workflow requested input without persisting a checkpoint")
            prompt = "Additional user input is required before the delegated task can continue."
            reason = "clarification_required"
            if isinstance(request_event.data, ClarificationRequest):
                prompt = request_event.data.question
                reason = request_event.data.reason
            return SoftwareWorkerWorkflowResult(
                status=TASK_STATUS_WAITING_INPUT,
                pending_input=PendingInputState(
                    checkpoint_id=latest_checkpoint.checkpoint_id,
                    pending_request_id=request_event.request_id,
                    prompt=prompt,
                    reason=reason,
                ),
            )
        outputs = run_result.get_outputs()
        if outputs:
            output = outputs[-1]
            if isinstance(output, dict):
                return SoftwareWorkerWorkflowResult(
                    status=str(output.get("status") or TASK_STATUS_FAILED),
                    developer_output=cast_str(output.get("developer_output")),
                    tester_output=cast_str(output.get("tester_output")),
                    developer_temporary_agent_ids=cast_str_list(
                        output.get("developer_temporary_agent_ids")
                    ),
                    tester_temporary_agent_ids=cast_str_list(
                        output.get("tester_temporary_agent_ids")
                    ),
                    error_text=cast_str(output.get("error")),
                )
        return SoftwareWorkerWorkflowResult(
            status=TASK_STATUS_FAILED,
            error_text="Workflow completed without a final output.",
        )


def cast_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def cast_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def cast_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
