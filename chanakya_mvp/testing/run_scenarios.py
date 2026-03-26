from __future__ import annotations

from pathlib import Path

from chanakya_mvp.scenarios import ScenarioRunner


def write_transition_report(runner: ScenarioRunner, output_file: Path) -> None:
    tasks = runner.store.list_tasks()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Task State Transition Records", ""]
    for task in tasks:
        lines.append(f"## Task `{task.id}` ({task.owner})")
        lines.append(f"- Description: {task.description}")
        lines.append(f"- Current Status: {task.status.value}")
        if task.parent_task_id:
            lines.append(f"- Parent: {task.parent_task_id}")
        if task.dependencies:
            lines.append(f"- Dependencies: {', '.join(task.dependencies)}")
        history = runner.store.get_state_history(task.id)
        lines.append("- State History:")
        for event in history:
            lines.append(
                "  - "
                f"{event['timestamp']}: {event['from_status']} -> {event['to_status']} "
                f"({event['reason']})"
            )
        lines.append("")
    output_file.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    runner = ScenarioRunner(base_dir)
    results = runner.run_all()

    print("Chanakya MAF MVP Scenario Results")
    print("=" * 34)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{result.id}: {status} - {result.details}")

    all_passed = all(r.passed for r in results)
    print("=" * 34)
    print("Overall:", "PASS" if all_passed else "PARTIAL/FAIL")

    write_transition_report(runner, base_dir / "docs" / "transition_records.md")


if __name__ == "__main__":
    main()
