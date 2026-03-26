from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chanakya_mvp.chanakya import ChanakyaPA
from chanakya_mvp.logging_utils import JsonlLogger
from chanakya_mvp.manager import AgentManager
from chanakya_mvp.models import TaskStatus
from chanakya_mvp.store import TaskStore


@dataclass(slots=True)
class ScenarioResult:
    id: str
    passed: bool
    details: str


class ScenarioRunner:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.log_path = self.base_dir / "artifacts" / "events.jsonl"
        self.db_path = self.base_dir / "artifacts" / "tasks.db"
        if self.log_path.exists():
            self.log_path.unlink()
        if self.db_path.exists():
            self.db_path.unlink()
        self.store = TaskStore(self.db_path)
        self.logger = JsonlLogger(self.log_path)
        self.manager = AgentManager(self.store, self.logger)
        self.chanakya = ChanakyaPA(self.manager, self.logger)

    def run_all(self) -> list[ScenarioResult]:
        return [
            self.ts_001_direct(),
            self.ts_002_weather(),
            self.ts_003_delegation(),
            self.ts_004_dependency_enforcement(),
            self.ts_005_failure_path(),
            self.ts_006_missing_input_resume(),
            self.ts_007_final_aggregation(),
        ]

    def ts_001_direct(self) -> ScenarioResult:
        reply = self.chanakya.handle_message("Hello Chanakya, how are you?")
        passed = reply.route.value == "direct" and "Direct response path succeeded" in reply.message
        return ScenarioResult("TS-001", passed, reply.message)

    def ts_002_weather(self) -> ScenarioResult:
        reply = self.chanakya.handle_message("What is the weather in Bengaluru?")
        passed = reply.route.value == "tool" and "Weather for Bengaluru" in reply.message
        return ScenarioResult("TS-002", passed, reply.message)

    def ts_003_delegation(self) -> ScenarioResult:
        reply = self.chanakya.handle_message(
            "Please implement and test login form validation.",
            context={"feature_scope": "login form validation"},
        )
        passed = reply.route.value == "manager" and reply.delegated_task_id is not None
        return ScenarioResult("TS-003", passed, reply.message)

    def ts_004_dependency_enforcement(self) -> ScenarioResult:
        reply = self.chanakya.handle_message(
            "Implement and test dashboard filters.",
            context={"simulate_dev_fail": True, "feature_scope": "dashboard filters"},
        )
        if not reply.delegated_task_id:
            return ScenarioResult("TS-004", False, "No parent task returned.")
        children = self.store.list_children(reply.delegated_task_id)
        tester = next((t for t in children if t.owner == "tester_agent"), None)
        passed = tester is not None and tester.status == TaskStatus.BLOCKED
        detail = "Tester blocked until developer completion or due to failure."
        return ScenarioResult("TS-004", passed, detail)

    def ts_005_failure_path(self) -> ScenarioResult:
        reply = self.chanakya.handle_message(
            "Implement and test metrics exporter.",
            context={"simulate_dev_fail": True, "feature_scope": "metrics exporter"},
        )
        if not reply.delegated_task_id:
            return ScenarioResult("TS-005", False, "No parent task returned.")
        parent = self.store.get_task(reply.delegated_task_id)
        passed = parent.status == TaskStatus.FAILED
        return ScenarioResult("TS-005", passed, f"Parent status: {parent.status.value}")

    def ts_006_missing_input_resume(self) -> ScenarioResult:
        reply = self.chanakya.handle_message("Please implement and test profile settings page.")
        if not reply.waiting_input:
            return ScenarioResult("TS-006", False, "Expected waiting input state.")
        follow = self.chanakya.submit_followup(
            reply.request_id, "profile settings validation and save flow"
        )
        if not follow.delegated_task_id:
            return ScenarioResult("TS-006", False, "Follow-up not linked to parent task.")
        parent = self.store.get_task(follow.delegated_task_id)
        passed = parent.status == TaskStatus.DONE
        return ScenarioResult("TS-006", passed, follow.message)

    def ts_007_final_aggregation(self) -> ScenarioResult:
        reply = self.chanakya.handle_message(
            "Implement and test checkout discount logic.",
            context={"feature_scope": "checkout discount logic"},
        )
        if not reply.delegated_task_id:
            return ScenarioResult("TS-007", False, "No delegated task ID.")
        parent = self.store.get_task(reply.delegated_task_id)
        passed = parent.status == TaskStatus.DONE and parent.result is not None
        return ScenarioResult("TS-007", passed, parent.result or "No parent result.")
