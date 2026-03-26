from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        parent_id = make_id("task")
        parent = Task(
            id=parent_id,
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
            },
        )

        dev_result = self.developer.execute(self.store.get_task(dev_task.id), self.store)

        if dev_result.needs_input:
            self.store.update_task_status(
                parent.id, TaskStatus.WAITING_INPUT, "child_waiting_input"
            )
            return ManagerResponse(
                parent_task_id=parent.id,
                status=TaskStatus.WAITING_INPUT,
                user_message="Task paused for required clarification.",
                waiting_input_prompt=dev_result.input_prompt,
            )

        if dev_result.status == TaskStatus.FAILED:
            self.store.update_task_status(parent.id, TaskStatus.FAILED, "child_failed")
            tester_blocked = self.tester.execute(self.store.get_task(test_task.id), self.store)
            return ManagerResponse(
                parent_task_id=parent.id,
                status=TaskStatus.FAILED,
                user_message=(
                    "Delegated task failed during development. "
                    f"Tester status: {tester_blocked.status.value}."
                ),
            )

        test_result = self.tester.execute(self.store.get_task(test_task.id), self.store)
        if test_result.status == TaskStatus.DONE:
            self.store.update_task_result(parent.id, "Implementation and testing completed.")
            self.store.update_task_status(parent.id, TaskStatus.DONE, "all_subtasks_done")
            return ManagerResponse(
                parent_task_id=parent.id,
                status=TaskStatus.DONE,
                user_message="Delegated task completed successfully.",
            )

        self.store.update_task_status(parent.id, TaskStatus.BLOCKED, "tester_blocked")
        return ManagerResponse(
            parent_task_id=parent.id,
            status=TaskStatus.BLOCKED,
            user_message="Delegated task is blocked due to unmet dependency.",
        )

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
        self.store.update_task_status(parent.id, TaskStatus.READY, "resumed_after_input")
        self.store.update_task_status(parent.id, TaskStatus.ASSIGNED, "manager_resuming")
        self.store.update_task_status(parent.id, TaskStatus.IN_PROGRESS, "resume_workflow_started")

        rerun_dev = self.developer.execute(self.store.get_task(dev_task.id), self.store)
        if rerun_dev.status != TaskStatus.DONE:
            self.store.update_task_status(parent.id, TaskStatus.FAILED, "resume_failed")
            return ManagerResponse(
                parent_task_id=parent_task_id,
                status=TaskStatus.FAILED,
                user_message="Task could not resume successfully.",
            )

        test_result = self.tester.execute(self.store.get_task(test_task.id), self.store)
        if test_result.status == TaskStatus.DONE:
            self.store.update_task_result(
                parent.id, "Implementation and testing completed after input."
            )
            self.store.update_task_status(parent.id, TaskStatus.DONE, "resume_all_subtasks_done")
            return ManagerResponse(
                parent_task_id=parent_task_id,
                status=TaskStatus.DONE,
                user_message="Delegated task completed after clarification.",
            )

        self.store.update_task_status(parent.id, TaskStatus.BLOCKED, "resume_tester_blocked")
        return ManagerResponse(
            parent_task_id=parent_task_id,
            status=TaskStatus.BLOCKED,
            user_message="Task remains blocked after clarification.",
        )
