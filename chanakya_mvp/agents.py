from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chanakya_mvp.models import Task, TaskStatus
from chanakya_mvp.store import TaskStore


@dataclass(slots=True)
class AgentResult:
    status: TaskStatus
    message: str
    needs_input: bool = False
    input_prompt: str | None = None


class DeveloperAgent:
    name = "developer_agent"

    def execute(self, task: Task, store: TaskStore) -> AgentResult:
        store.update_task_status(task.id, TaskStatus.IN_PROGRESS, "developer_started")
        simulate_fail = bool(task.metadata.get("simulate_dev_fail"))
        feature_scope = task.metadata.get("feature_scope")

        if not feature_scope:
            store.update_task_status(task.id, TaskStatus.WAITING_INPUT, "missing_feature_scope")
            return AgentResult(
                status=TaskStatus.WAITING_INPUT,
                message="Developer needs clarification before implementation.",
                needs_input=True,
                input_prompt="Please clarify the feature scope for implementation.",
            )

        if simulate_fail:
            store.update_task_result(
                task.id, "Implementation failed due to simulated compile issue."
            )
            store.update_task_status(task.id, TaskStatus.FAILED, "developer_failed")
            return AgentResult(
                status=TaskStatus.FAILED,
                message="Developer failed to complete implementation.",
            )

        implementation_note = f"Implemented feature: {feature_scope}."
        store.update_task_result(task.id, implementation_note)
        store.update_task_status(task.id, TaskStatus.DONE, "developer_done")
        return AgentResult(status=TaskStatus.DONE, message=implementation_note)


class TesterAgent:
    name = "tester_agent"

    def execute(self, task: Task, store: TaskStore) -> AgentResult:
        deps = [store.get_task(task_id) for task_id in task.dependencies]
        failed_dep = next((d for d in deps if d.status == TaskStatus.FAILED), None)
        incomplete_dep = next((d for d in deps if d.status != TaskStatus.DONE), None)

        if failed_dep is not None:
            store.update_task_status(task.id, TaskStatus.BLOCKED, "dependency_failed")
            store.update_task_result(task.id, f"Blocked: dependency {failed_dep.id} failed.")
            return AgentResult(
                status=TaskStatus.BLOCKED, message="Tester blocked due to failed dependency."
            )

        if incomplete_dep is not None:
            store.update_task_status(task.id, TaskStatus.BLOCKED, "dependency_incomplete")
            return AgentResult(
                status=TaskStatus.BLOCKED, message="Tester waiting for developer completion."
            )

        store.update_task_status(task.id, TaskStatus.IN_PROGRESS, "tester_started")
        store.update_task_result(task.id, "Tests passed: unit + integration checks green.")
        store.update_task_status(task.id, TaskStatus.DONE, "tester_done")
        return AgentResult(status=TaskStatus.DONE, message="Testing completed successfully.")


def assign_owner_metadata(metadata: dict[str, Any], owner: str) -> dict[str, Any]:
    merged = metadata.copy()
    merged["assigned_owner"] = owner
    return merged
