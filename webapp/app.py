from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from chanakya_mvp.chanakya import ChanakyaPA
from chanakya_mvp.logging_utils import JsonlLogger
from chanakya_mvp.manager import AgentManager
from chanakya_mvp.store import TaskStore

BASE_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = BASE_DIR / "artifacts"
EVENTS_FILE = ARTIFACTS_DIR / "events.jsonl"
TASK_DB_FILE = ARTIFACTS_DIR / "tasks.db"


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(BASE_DIR / "webapp" / "templates"))

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    store = TaskStore(TASK_DB_FILE)
    logger = JsonlLogger(EVENTS_FILE)
    manager = AgentManager(store, logger)
    chanakya = ChanakyaPA(manager, logger)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/chat")
    def api_chat() -> Any:
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        raw_context = payload.get("context", {})
        context = raw_context if isinstance(raw_context, dict) else {}

        if not message:
            return jsonify({"error": "message is required"}), 400

        reply = chanakya.handle_message(message, context)
        response: dict[str, Any] = {
            "request_id": reply.request_id,
            "route": reply.route.value,
            "message": reply.message,
            "delegated_task_id": reply.delegated_task_id,
            "waiting_input": reply.waiting_input,
        }
        if reply.delegated_task_id:
            response["task"] = task_snapshot(store, reply.delegated_task_id)
        return jsonify(response)

    @app.post("/api/followup")
    def api_followup() -> Any:
        payload = request.get_json(silent=True) or {}
        request_id = str(payload.get("request_id", "")).strip()
        message = str(payload.get("message", "")).strip()
        if not request_id or not message:
            return jsonify({"error": "request_id and message are required"}), 400

        reply = chanakya.submit_followup(request_id, message)
        response: dict[str, Any] = {
            "request_id": reply.request_id,
            "route": reply.route.value,
            "message": reply.message,
            "delegated_task_id": reply.delegated_task_id,
            "waiting_input": reply.waiting_input,
        }
        if reply.delegated_task_id:
            response["task"] = task_snapshot(store, reply.delegated_task_id)
        return jsonify(response)

    @app.get("/api/events")
    def api_events() -> Any:
        request_id = request.args.get("request_id", default=None, type=str)
        task_id = request.args.get("task_id", default=None, type=str)
        events = read_events(EVENTS_FILE, request_id=request_id, task_id=task_id)
        return jsonify({"events": events})

    @app.get("/api/tasks/<task_id>")
    def api_task(task_id: str) -> Any:
        try:
            return jsonify(task_snapshot(store, task_id))
        except KeyError:
            return jsonify({"error": f"task not found: {task_id}"}), 404

    return app


def read_events(
    file_path: Path,
    request_id: str | None,
    task_id: str | None,
) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    results: list[dict[str, Any]] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if not match_event_filter(payload, request_id=request_id, task_id=task_id):
            continue
        results.append(record)
    return results


def match_event_filter(
    payload: dict[str, Any],
    request_id: str | None,
    task_id: str | None,
) -> bool:
    request_match = True
    task_match = True
    if request_id:
        request_match = request_id in {
            str(payload.get("request_id", "")),
            str(payload.get("originating_request_id", "")),
        }
    if task_id:
        task_match = task_id in {
            str(payload.get("task_id", "")),
            str(payload.get("parent_task_id", "")),
        }
    return request_match and task_match


def task_snapshot(store: TaskStore, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    children = store.list_children(task_id)
    return {
        "id": task.id,
        "description": task.description,
        "owner": task.owner,
        "status": task.status.value,
        "result": task.result,
        "dependencies": task.dependencies,
        "parent_task_id": task.parent_task_id,
        "metadata": task.metadata,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "transitions": store.get_state_history(task_id),
        "children": [
            {
                "id": child.id,
                "description": child.description,
                "owner": child.owner,
                "status": child.status.value,
                "dependencies": child.dependencies,
                "result": child.result,
                "transitions": store.get_state_history(child.id),
            }
            for child in children
        ],
    }


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
