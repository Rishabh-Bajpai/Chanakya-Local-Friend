from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agent_framework import WorkflowBuilder, WorkflowContext, executor
from typing_extensions import Never

from chanakya_mvp.agents import DeveloperAgent, TesterAgent, assign_owner_metadata
from chanakya_mvp.logging_utils import JsonlLogger
from chanakya_mvp.models import Task, TaskStatus, make_id
from chanakya_mvp.store import TaskStore


@dataclass(slots=True)
class ManagerResponse:
    parent_task_id: str
    status: TaskStatus
    user_message: str
    waiting_input_prompt: str | None = None


class AgentManager:
    def __init__(self, store: TaskStore, logger: JsonlLogger) -> None:
        self.store = store
        self.logger = logger
        self.developer = DeveloperAgent()
        self.tester = TesterAgent()

    def create_and_run_workflow(
        self, request_text: str, context: dict[str, Any]
    ) -> ManagerResponse:
        parent, dev_task, test_task = self._create_task_graph(request_text, context)
        return self._run_maf_workflow(parent.id, dev_task.id, test_task.id)

    def resume_waiting_task(self, parent_task_id: str, user_input: str) -> ManagerResponse:
        parent = self.store.get_task(parent_task_id)
        children = self.store.list_children(parent.id)
        dev_task = next(t for t in children if t.owner == self.developer.name)
        test_task = next(t for t in children if t.owner == self.tester.name)

        if dev_task.status != TaskStatus.WAITING_INPUT:
            return ManagerResponse(
                parent_task_id=parent_task_id,
                status=parent.status,
                user_message="No waiting input needed for this task.",
            )

        updated_meta = dev_task.metadata.copy()
        updated_meta["feature_scope"] = user_input
        self.store.update_task_result(dev_task.id, "Received user clarification.", updated_meta)
        self.store.update_task_status(dev_task.id, TaskStatus.READY, "input_received")
        self.store.update_task_status(dev_task.id, TaskStatus.ASSIGNED, "reassigned_after_input")
        if test_task.status == TaskStatus.BLOCKED:
            self.store.update_task_status(test_task.id, TaskStatus.READY, "dependency_cleared")
            self.store.update_task_status(
                test_task.id, TaskStatus.ASSIGNED, "reassigned_after_input"
            )
        self.store.update_task_status(parent.id, TaskStatus.READY, "resumed_after_input")
        self.store.update_task_status(parent.id, TaskStatus.ASSIGNED, "manager_resuming")
        self.store.update_task_status(parent.id, TaskStatus.IN_PROGRESS, "resume_workflow_started")

        return self._run_maf_workflow(parent.id, dev_task.id, test_task.id)

    def _create_task_graph(
        self,
        request_text: str,
        context: dict[str, Any],
    ) -> tuple[Task, Task, Task]:
        parent = Task(
            id=make_id("task"),
            description=request_text,
            owner="agent_manager",
            status=TaskStatus.CREATED,
            metadata=context.copy(),
        )
        self.store.create_task(parent)
        self.store.update_task_status(parent.id, TaskStatus.READY, "manager_received_request")
        self.store.update_task_status(parent.id, TaskStatus.ASSIGNED, "manager_orchestrating")
        self.store.update_task_status(parent.id, TaskStatus.IN_PROGRESS, "workflow_started")

        dev_task = Task(
            id=make_id("task"),
            description="Implement requested feature",
            owner=self.developer.name,
            status=TaskStatus.CREATED,
            parent_task_id=parent.id,
            metadata=assign_owner_metadata(context, self.developer.name),
        )
        test_task = Task(
            id=make_id("task"),
            description="Test implemented feature",
            owner=self.tester.name,
            status=TaskStatus.CREATED,
            parent_task_id=parent.id,
            dependencies=[dev_task.id],
            metadata=assign_owner_metadata(context, self.tester.name),
        )

        self.store.create_task(dev_task)
        self.store.create_task(test_task)
        self.store.update_task_status(dev_task.id, TaskStatus.READY, "subtask_ready")
        self.store.update_task_status(test_task.id, TaskStatus.READY, "subtask_ready")
        self.store.update_task_status(dev_task.id, TaskStatus.ASSIGNED, "assigned_to_developer")
        self.store.update_task_status(test_task.id, TaskStatus.ASSIGNED, "assigned_to_tester")

        self.logger.log(
            "task_decomposed",
            {
                "parent_task_id": parent.id,
                "subtasks": [dev_task.id, test_task.id],
                "owners": [self.developer.name, self.tester.name],
                "runtime": "maf_workflow",
            },
        )
        return parent, dev_task, test_task

    def _run_maf_workflow(
        self, parent_id: str, dev_task_id: str, test_task_id: str
    ) -> ManagerResponse:
        @executor(id="developer_step")
        async def developer_step(
            _: dict[str, Any] | None,
            ctx: WorkflowContext[dict[str, Any]],
        ) -> None:
            dev_task = self.store.get_task(dev_task_id)
            if dev_task.status in {TaskStatus.DONE, TaskStatus.FAILED}:
                await ctx.send_message({"developer_status": dev_task.status.value})
                return

            self.store.update_task_status(dev_task.id, TaskStatus.IN_PROGRESS, "developer_started")
            feature_scope = dev_task.metadata.get("feature_scope")
            if not feature_scope:
                self.store.update_task_status(
                    dev_task.id, TaskStatus.WAITING_INPUT, "missing_feature_scope"
                )
                await ctx.send_message(
                    {
                        "developer_status": TaskStatus.WAITING_INPUT.value,
                        "waiting_input_prompt": (
                            "Please clarify the feature scope for implementation."
                        ),
                    }
                )
                return

            simulate_fail = bool(dev_task.metadata.get("simulate_dev_fail"))
            if simulate_fail:
                self.store.update_task_result(
                    dev_task.id,
                    "Implementation failed due to simulated compile issue.",
                )
                self.store.update_task_status(dev_task.id, TaskStatus.FAILED, "developer_failed")
                await ctx.send_message({"developer_status": TaskStatus.FAILED.value})
                return

            implementation_note = f"Implemented feature: {feature_scope}."
            self.store.update_task_result(dev_task.id, implementation_note)
            self.store.update_task_status(dev_task.id, TaskStatus.DONE, "developer_done")
            await ctx.send_message({"developer_status": TaskStatus.DONE.value})

        @executor(id="tester_step")
        async def tester_step(
            message: dict[str, Any],
            ctx: WorkflowContext[dict[str, Any]],
        ) -> None:
            developer_status = str(message.get("developer_status", ""))
            tester_task = self.store.get_task(test_task_id)

            if developer_status == TaskStatus.FAILED.value:
                if tester_task.status != TaskStatus.BLOCKED:
                    self.store.update_task_status(
                        tester_task.id, TaskStatus.BLOCKED, "dependency_failed"
                    )
                    self.store.update_task_result(
                        tester_task.id,
                        f"Blocked: dependency {dev_task_id} failed.",
                    )
                await ctx.send_message({"tester_status": TaskStatus.BLOCKED.value})
                return

            if developer_status == TaskStatus.WAITING_INPUT.value:
                if tester_task.status != TaskStatus.BLOCKED:
                    self.store.update_task_status(
                        tester_task.id,
                        TaskStatus.BLOCKED,
                        "dependency_incomplete",
                    )
                await ctx.send_message({"tester_status": TaskStatus.BLOCKED.value})
                return

            deps = [self.store.get_task(task_id) for task_id in tester_task.dependencies]
            incomplete_dep = next((dep for dep in deps if dep.status != TaskStatus.DONE), None)
            if incomplete_dep is not None:
                if tester_task.status != TaskStatus.BLOCKED:
                    self.store.update_task_status(
                        tester_task.id,
                        TaskStatus.BLOCKED,
                        "dependency_incomplete",
                    )
                await ctx.send_message({"tester_status": TaskStatus.BLOCKED.value})
                return

            if tester_task.status != TaskStatus.DONE:
                self.store.update_task_status(
                    tester_task.id, TaskStatus.IN_PROGRESS, "tester_started"
                )
                self.store.update_task_result(
                    tester_task.id,
                    "Tests passed: unit + integration checks green.",
                )
                self.store.update_task_status(tester_task.id, TaskStatus.DONE, "tester_done")
            await ctx.send_message({"tester_status": TaskStatus.DONE.value})

        @executor(id="aggregate_step")
        async def aggregate_step(
            _: dict[str, Any],
            ctx: WorkflowContext[Never, dict[str, Any]],
        ) -> None:
            parent = self.store.get_task(parent_id)
            dev_task = self.store.get_task(dev_task_id)
            test_task = self.store.get_task(test_task_id)

            if dev_task.status == TaskStatus.WAITING_INPUT:
                self.store.update_task_status(
                    parent.id, TaskStatus.WAITING_INPUT, "child_waiting_input"
                )
                await ctx.yield_output(
                    {
                        "status": TaskStatus.WAITING_INPUT.value,
                        "message": "Task paused for required clarification.",
                        "waiting_input_prompt": (
                            "Please clarify the feature scope for implementation."
                        ),
                    }
                )
                return

            if dev_task.status == TaskStatus.FAILED:
                self.store.update_task_status(parent.id, TaskStatus.FAILED, "child_failed")
                await ctx.yield_output(
                    {
                        "status": TaskStatus.FAILED.value,
                        "message": (
                            "Delegated task failed during development. "
                            f"Tester status: {test_task.status.value}."
                        ),
                    }
                )
                return

            if test_task.status == TaskStatus.DONE:
                summary = "Implementation and testing completed."
                self.store.update_task_result(parent.id, summary)
                self.store.update_task_status(parent.id, TaskStatus.DONE, "all_subtasks_done")
                await ctx.yield_output(
                    {
                        "status": TaskStatus.DONE.value,
                        "message": "Delegated task completed successfully.",
                    }
                )
                return

            self.store.update_task_status(parent.id, TaskStatus.BLOCKED, "tester_blocked")
            await ctx.yield_output(
                {
                    "status": TaskStatus.BLOCKED.value,
                    "message": "Delegated task is blocked due to unmet dependency.",
                }
            )

        async def run_workflow() -> dict[str, Any]:
            workflow = (
                WorkflowBuilder(start_executor=developer_step)
                .add_edge(developer_step, tester_step)
                .add_edge(tester_step, aggregate_step)
                .build()
            )
            result = await workflow.run({"parent_task_id": parent_id})
            outputs = result.get_outputs()
            if outputs:
                first = outputs[0]
                if isinstance(first, dict):
                    return first
            return {
                "status": TaskStatus.FAILED.value,
                "message": "Workflow completed without an aggregate output.",
            }

        output = asyncio.run(run_workflow())
        status = TaskStatus(str(output.get("status", TaskStatus.FAILED.value)))
        waiting_input_prompt = output.get("waiting_input_prompt")
        return ManagerResponse(
            parent_task_id=parent_id,
            status=status,
            user_message=str(output.get("message", "Delegated workflow completed.")),
            waiting_input_prompt=(
                str(waiting_input_prompt) if isinstance(waiting_input_prompt, str) else None
            ),
        )
