from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.config import get_data_dir, get_database_url, load_local_env
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.debug import debug_log
from chanakya.domain import make_id
from chanakya.heartbeat import read_heartbeat
from chanakya.seed import load_agent_seeds
from chanakya.store import ChanakyaStore

BASE_DIR = Path(__file__).resolve().parents[1]


def create_app() -> Flask:
    load_local_env()
    app = Flask(__name__, template_folder=str(BASE_DIR / "chanakya" / "templates"))

    data_dir = get_data_dir()
    database_url = get_database_url()
    heartbeat_dir = data_dir / "heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)

    engine = build_engine(database_url)
    init_database(engine)
    session_factory = build_session_factory(engine)
    store = ChanakyaStore(session_factory)
    load_agent_seeds(store, BASE_DIR / "chanakya" / "seeds" / "agents.json")
    ensure_heartbeat_files(store, BASE_DIR)
    debug_log(
        "app_initialized",
        {
            "base_dir": str(BASE_DIR),
            "data_dir": str(data_dir),
            "database_url": database_url,
            "seed_file": str(BASE_DIR / "chanakya" / "seeds" / "agents.json"),
            "agent_count": len(store.list_agent_profiles()),
        },
    )

    from chanakya.services.tool_loader import initialize_all_tools

    initialize_all_tools()

    chanakya_profile = store.get_agent_profile("agent_chanakya")
    manager_profile = store.get_agent_profile("agent_manager")
    runtime = MAFRuntime(chanakya_profile, session_factory)
    manager = AgentManager(store, session_factory, manager_profile)
    chat_service = ChatService(store, runtime, manager)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/chat")
    def api_chat() -> Any:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or make_id("session"))
        message = str(payload.get("message", "")).strip()
        debug_log(
            "api_chat_request",
            {
                "session_id": session_id,
                "message": message,
                "has_existing_session": bool(payload.get("session_id")),
            },
        )
        if not message:
            return jsonify({"error": "message is required"}), 400
        store.ensure_session(session_id, title=message[:60] or "New chat")
        reply = chat_service.chat(session_id, message)
        debug_log(
            "api_chat_response",
            {
                "session_id": reply.session_id,
                "request_id": reply.request_id,
                "route": reply.route,
                "agent_name": reply.agent_name,
                "runtime": reply.runtime,
                "model": reply.model,
                "response": reply.message,
                "response_mode": reply.response_mode,
                "tool_calls_used": reply.tool_calls_used,
            },
        )
        return jsonify(asdict(reply))

    @app.get("/api/sessions/<session_id>")
    def api_session(session_id: str) -> Any:
        messages = store.list_messages(session_id)
        debug_log(
            "api_session_request",
            {
                "session_id": session_id,
                "message_count": len(messages),
            },
        )
        return jsonify({"session_id": session_id, "messages": messages})

    @app.get("/api/events")
    def api_events() -> Any:
        events = store.list_events(limit=100)
        debug_log("api_events_request", {"event_count": len(events)})
        return jsonify({"events": events})

    @app.get("/api/requests")
    def api_requests() -> Any:
        session_id = request.args.get("session_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        records = store.list_requests(session_id=session_id, limit=limit)
        debug_log("api_requests_request", {"request_count": len(records)})
        return jsonify({"requests": records})

    @app.get("/api/tasks")
    def api_tasks() -> Any:
        session_id = request.args.get("session_id")
        request_id = request.args.get("request_id")
        root_only = request.args.get("root_only", "false").lower() == "true"
        parent_task_id = request.args.get("parent_task_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        if parent_task_id:
            tasks = store.list_task_children(
                parent_task_id,
                session_id=session_id,
                request_id=request_id,
                limit=limit,
            )
        else:
            tasks = store.list_tasks(
                session_id=session_id,
                request_id=request_id,
                root_only=root_only,
                limit=limit,
            )
        debug_log("api_tasks_request", {"task_count": len(tasks)})
        return jsonify({"tasks": tasks})

    @app.get("/api/task-events")
    def api_task_events() -> Any:
        session_id = request.args.get("session_id")
        request_id = request.args.get("request_id")
        task_id = request.args.get("task_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        events = store.list_task_events(
            session_id=session_id,
            request_id=request_id,
            task_id=task_id,
            limit=limit,
        )
        debug_log("api_task_events_request", {"event_count": len(events)})
        return jsonify({"events": events})

    @app.get("/api/agents")
    def api_agents() -> Any:
        agents = []
        for profile in store.list_agent_profiles():
            heartbeat = read_heartbeat(profile, BASE_DIR)
            agent_payload = profile.to_public_dict()
            agent_payload["heartbeat_preview"] = heartbeat.content_preview
            agents.append(agent_payload)
        debug_log("api_agents_request", {"agent_count": len(agents)})
        return jsonify({"agents": agents})

    @app.get("/api/tool-traces")
    def api_tool_traces() -> Any:
        """Return tool invocation traces, optionally filtered by session or request."""
        session_id = request.args.get("session_id")
        request_id = request.args.get("request_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        traces = store.list_tool_invocations(
            session_id=session_id,
            request_id=request_id,
            limit=limit,
        )
        debug_log("api_tool_traces_request", {"trace_count": len(traces)})
        return jsonify({"traces": traces})

    return app


def ensure_heartbeat_files(store: ChanakyaStore, repo_root: Path) -> None:
    for profile in store.list_agent_profiles():
        if not profile.heartbeat_file_path:
            continue
        target = repo_root / profile.heartbeat_file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        target.write_text(
            (
                f"# Heartbeat for {profile.name}\n\n"
                "- Pending task check: none yet\n"
                "- Notes: heartbeat execution will be added in Milestone 9\n"
            ),
            encoding="utf-8",
        )
        debug_log(
            "heartbeat_file_created",
            {
                "agent_id": profile.id,
                "path": str(target),
            },
        )


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5123, debug=True)
