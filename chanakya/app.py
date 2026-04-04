from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Any

from flask import Flask, jsonify, render_template, request

from chanakya.agent.profile_files import default_heartbeat_relative_path, ensure_agent_profile_files
from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.config import (
    force_subagents_enabled,
    get_data_dir,
    get_database_url,
    load_local_env,
)
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.debug import debug_log
from chanakya.domain import make_id, now_iso
from chanakya.heartbeat import read_heartbeat, resolve_heartbeat_path
from chanakya.model import AgentProfileModel
from chanakya.seed import load_agent_seeds
from chanakya.services.tool_loader import get_tools_availability
from chanakya.store import ChanakyaStore

BASE_DIR = Path(__file__).resolve().parents[1]


def create_app() -> Flask:
    load_local_env()
    app = Flask(__name__, template_folder=str(BASE_DIR / "chanakya" / "templates"))

    data_dir = get_data_dir()
    database_url = get_database_url()
    agents_dir = data_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

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
        return render_template(
            "index.html",
            force_subagents_enabled=force_subagents_enabled(),
        )

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
        try:
            reply = chat_service.chat(session_id, message)
        except Exception as exc:
            debug_log(
                "api_chat_error",
                {
                    "session_id": session_id,
                    "message": message,
                    "error": str(exc),
                },
            )
            return jsonify({"error": str(exc), "session_id": session_id}), 502
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

    @app.post("/api/tasks/<task_id>/input")
    def api_task_input(task_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        try:
            reply = chat_service.submit_task_input(task_id, message)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(asdict(reply))

    @app.post("/api/tasks/<task_id>/cancel")
    def api_task_cancel(task_id: str) -> Any:
        try:
            result = chat_service.cancel_task(task_id)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/tasks/<task_id>/retry")
    def api_task_retry(task_id: str) -> Any:
        try:
            result = chat_service.retry_task(task_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/tasks/<task_id>/unblock")
    def api_task_unblock(task_id: str) -> Any:
        try:
            result = chat_service.manual_unblock_task(task_id)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

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

    @app.get("/api/tools/availability")
    def api_tools_availability() -> Any:
        tools = get_tools_availability()
        return jsonify({"tools": tools})

    @app.get("/api/subagents")
    def api_subagents() -> Any:
        session_id = request.args.get("session_id")
        request_id = request.args.get("request_id")
        parent_task_id = request.args.get("parent_task_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        subagents = store.list_temporary_agents(
            session_id=session_id,
            request_id=request_id,
            parent_task_id=parent_task_id,
            limit=limit,
        )
        debug_log("api_subagents_request", {"subagent_count": len(subagents)})
        return jsonify({"subagents": subagents})

    @app.post("/api/agents")
    def api_create_agent() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            agent_data = _parse_agent_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        agent_id = _allocate_agent_profile_id(store, agent_data["name"])
        heartbeat_path = agent_data["heartbeat_file_path"] or default_heartbeat_relative_path(
            agent_id
        )
        resolve_heartbeat_path(heartbeat_path, BASE_DIR, agent_id=agent_id)

        profile = AgentProfileModel(
            id=agent_id,
            name=agent_data["name"],
            role=agent_data["role"],
            system_prompt=agent_data["system_prompt"],
            personality=agent_data["personality"],
            tool_ids_json=agent_data["tool_ids"],
            workspace=agent_data["workspace"],
            heartbeat_enabled=agent_data["heartbeat_enabled"],
            heartbeat_interval_seconds=agent_data["heartbeat_interval_seconds"],
            heartbeat_file_path=heartbeat_path,
            is_active=agent_data["is_active"],
            created_at=agent_data["timestamp"],
            updated_at=agent_data["timestamp"],
        )
        store.create_agent_profile(profile)
        ensure_heartbeat_file(profile, BASE_DIR)
        store.log_event(
            "agent_profile_created",
            {"agent_id": profile.id, "role": profile.role, "name": profile.name},
        )
        payload = profile.to_public_dict()
        payload["heartbeat_preview"] = read_heartbeat(profile, BASE_DIR).content_preview
        return jsonify(payload), 201

    @app.put("/api/agents/<agent_id>")
    def api_update_agent(agent_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            agent_data = _parse_agent_payload(payload)
            heartbeat_path = agent_data["heartbeat_file_path"] or default_heartbeat_relative_path(
                agent_id
            )
            resolve_heartbeat_path(heartbeat_path, BASE_DIR, agent_id=agent_id)
            profile = store.update_agent_profile(
                agent_id,
                name=agent_data["name"],
                role=agent_data["role"],
                system_prompt=agent_data["system_prompt"],
                personality=agent_data["personality"],
                tool_ids=agent_data["tool_ids"],
                workspace=agent_data["workspace"],
                heartbeat_enabled=agent_data["heartbeat_enabled"],
                heartbeat_interval_seconds=agent_data["heartbeat_interval_seconds"],
                heartbeat_file_path=heartbeat_path,
                is_active=agent_data["is_active"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404

        ensure_heartbeat_file(profile, BASE_DIR)
        store.log_event(
            "agent_profile_updated",
            {"agent_id": profile.id, "role": profile.role, "name": profile.name},
        )
        payload = profile.to_public_dict()
        payload["heartbeat_preview"] = read_heartbeat(profile, BASE_DIR).content_preview
        return jsonify(payload)

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
        ensure_agent_profile_files(profile, repo_root)
        ensure_heartbeat_file(profile, repo_root)


def ensure_heartbeat_file(profile: AgentProfileModel, repo_root: Path) -> None:
    ensure_agent_profile_files(profile, repo_root)
    file_path = profile.heartbeat_file_path or default_heartbeat_relative_path(profile.id)
    target = resolve_heartbeat_path(file_path, repo_root, agent_id=profile.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
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


def _parse_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = _parse_required_string(payload, "name")
    role = _parse_required_string(payload, "role")
    system_prompt = _parse_required_string(payload, "system_prompt")
    personality = _parse_optional_string(payload, "personality")
    workspace_value = _parse_optional_string(payload, "workspace")
    heartbeat_path_value = _parse_optional_string(payload, "heartbeat_file_path")
    raw_tool_ids = payload.get("tool_ids", [])
    raw_interval = payload.get("heartbeat_interval_seconds", 300)

    if not isinstance(raw_tool_ids, list) or any(
        not isinstance(item, str) for item in raw_tool_ids
    ):
        raise ValueError("tool_ids must be a list of strings")

    try:
        heartbeat_interval_seconds = int(raw_interval)
    except (TypeError, ValueError) as exc:
        raise ValueError("heartbeat_interval_seconds must be a positive integer") from exc
    if heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat_interval_seconds must be a positive integer")

    heartbeat_enabled = _parse_required_bool(payload, "heartbeat_enabled", default=False)
    heartbeat_file_path = heartbeat_path_value or None
    if heartbeat_file_path is not None:
        resolve_heartbeat_path(heartbeat_file_path, BASE_DIR)

    return {
        "name": name,
        "role": role,
        "system_prompt": system_prompt,
        "personality": personality,
        "tool_ids": [item.strip() for item in raw_tool_ids if item.strip()],
        "workspace": workspace_value or None,
        "heartbeat_enabled": heartbeat_enabled,
        "heartbeat_interval_seconds": heartbeat_interval_seconds,
        "heartbeat_file_path": heartbeat_file_path,
        "is_active": _parse_required_bool(payload, "is_active", default=True),
        "timestamp": now_iso(),
    }


def _make_agent_profile_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"agent_{slug or make_id('agent')}"


def _allocate_agent_profile_id(store: ChanakyaStore, name: str) -> str:
    base_id = _make_agent_profile_id(name)
    candidate = base_id
    counter = 2
    while store.has_agent_profile(candidate):
        candidate = f"{base_id}_{counter}"
        counter += 1
    return candidate


def _parse_required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name, "")
    if value is None or not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _parse_optional_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value.strip()


def _parse_required_bool(payload: dict[str, Any], field_name: str, *, default: bool) -> bool:
    if field_name not in payload:
        return default
    value = payload[field_name]
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5123, debug=True)
