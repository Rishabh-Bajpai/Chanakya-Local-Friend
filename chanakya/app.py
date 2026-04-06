from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from chanakya.agent.profile_files import default_heartbeat_relative_path, ensure_agent_profile_files
from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.config import (
    get_air_dashboard_url,
    get_air_server_url,
    get_air_status_url,
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
from chanakya.services.sandbox_workspace import get_shared_workspace_root
from chanakya.services.tool_loader import get_tools_availability
from chanakya.store import ChanakyaStore

BASE_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Lightweight in-process event bus for SSE push
# ---------------------------------------------------------------------------
class _EventBus:
    """Fan-out event bus: each SSE client gets its own queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[str]] = []

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        payload = json.dumps({"type": event_type, **(data or {})})
        msg = f"event: update\ndata: {payload}\n\n"
        with self._lock:
            dead: list[queue.Queue[str]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass


event_bus = _EventBus()


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
    sync_default_agent_tools(store)
    ensure_heartbeat_files(store, BASE_DIR)
    get_shared_workspace_root()
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

    # --- Monkey-patch the store to publish SSE events on mutations ----------
    _original_create_task_event = store.create_task_event

    def _patched_create_task_event(**kwargs: Any) -> None:
        _original_create_task_event(**kwargs)
        event_bus.publish("task_event", {"event_type": kwargs.get("event_type")})

    store.create_task_event = _patched_create_task_event  # type: ignore[assignment]

    _original_update_task = store.update_task

    def _patched_update_task(task_id: str, **kwargs: Any) -> None:
        _original_update_task(task_id, **kwargs)
        event_bus.publish("task_updated", {"task_id": task_id})

    store.update_task = _patched_update_task  # type: ignore[assignment]

    _original_create_request = store.create_request

    def _patched_create_request(**kwargs: Any) -> None:
        _original_create_request(**kwargs)
        event_bus.publish("request_created", {"request_id": kwargs.get("request_id")})

    store.create_request = _patched_create_request  # type: ignore[assignment]

    _original_update_request = store.update_request

    def _patched_update_request(request_id: str, **kwargs: Any) -> None:
        _original_update_request(request_id, **kwargs)
        event_bus.publish("request_updated", {"request_id": request_id})

    store.update_request = _patched_update_request  # type: ignore[assignment]

    _original_add_message = store.add_message

    def _patched_add_message(
        session_id: str,
        role: str,
        content: str,
        request_id: str | None = None,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        _original_add_message(session_id, role, content, request_id, route, metadata)
        event_bus.publish("message_added", {"session_id": session_id, "role": role})

    store.add_message = _patched_add_message  # type: ignore[assignment]

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            air_dashboard_url=get_air_dashboard_url(),
            air_server_url=get_air_server_url(),
            air_status_url=get_air_status_url(),
            force_subagents_enabled=force_subagents_enabled(),
        )

    @app.get("/work")
    def work() -> str:
        return render_template(
            "work.html",
            air_dashboard_url=get_air_dashboard_url(),
            air_server_url=get_air_server_url(),
            air_status_url=get_air_status_url(),
            force_subagents_enabled=force_subagents_enabled(),
        )

    @app.post("/api/chat")
    def api_chat() -> Any:
        payload = request.get_json(silent=True) or {}
        raw_work_id = payload.get("work_id")
        work_id = str(raw_work_id).strip() if raw_work_id is not None else None
        if work_id == "":
            work_id = None
        raw_session_id = payload.get("session_id")
        if work_id is not None:
            try:
                work_record = store.get_work(work_id)
            except KeyError as exc:
                message = str(exc.args[0]) if exc.args else str(exc)
                return jsonify({"error": message}), 404
            session_id = store.ensure_work_agent_session(
                work_id=work_id,
                agent_id="agent_chanakya",
                session_id=make_id("session"),
                session_title=f"{work_record.title} - Chanakya",
            )
        else:
            session_id = str(raw_session_id or make_id("session"))
        message = str(payload.get("message", "")).strip()
        raw_model_id = payload.get("model_id")
        model_id = str(raw_model_id).strip() if raw_model_id is not None else None
        if model_id == "":
            model_id = None
        debug_log(
            "api_chat_request",
            {
                "session_id": session_id,
                "work_id": work_id,
                "model_id": model_id,
                "message": message,
                "has_existing_session": bool(payload.get("session_id")),
            },
        )
        if not message:
            return jsonify({"error": "message is required"}), 400
        store.ensure_session(session_id, title=message[:60] or "New chat")
        try:
            reply = chat_service.chat(session_id, message, work_id=work_id, model_id=model_id)
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

    @app.get("/api/sessions/<session_id>/next-message")
    def api_session_next_message(session_id: str) -> Any:
        payload = chat_service.deliver_next_conversation_message(session_id)
        return jsonify(payload)

    @app.post("/api/sessions/<session_id>/pause")
    def api_session_pause(session_id: str) -> Any:
        payload = chat_service._conversation_layer.request_manual_pause(session_id)
        return jsonify({"session_id": session_id, "working_memory": payload})

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

    @app.post("/api/works")
    def api_create_work() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            title = _parse_required_string(payload, "title")
            description = _parse_optional_string(payload, "description") or None
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        work_id = make_id("work")
        store.create_work(
            work_id=work_id,
            title=title,
            description=description,
            status="active",
        )
        active_profiles = [profile for profile in store.list_agent_profiles() if profile.is_active]
        for profile in active_profiles:
            store.ensure_work_agent_session(
                work_id=work_id,
                agent_id=profile.id,
                session_id=make_id("session"),
                session_title=f"{title} - {profile.name}",
            )
        store.log_event(
            "work_created",
            {
                "work_id": work_id,
                "title": title,
                "description": description,
                "agent_session_count": len(active_profiles),
            },
        )
        return jsonify(
            {
                "id": work_id,
                "title": title,
                "description": description,
                "status": "active",
                "agent_session_count": len(active_profiles),
            }
        ), 201

    @app.get("/api/works")
    def api_list_works() -> Any:
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        works = store.list_works(limit=limit)
        return jsonify({"works": works})

    @app.delete("/api/works/<work_id>")
    def api_delete_work(work_id: str) -> Any:
        try:
            deleted_session_ids = store.delete_work(work_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        store.log_event(
            "work_deleted",
            {
                "work_id": work_id,
                "session_count": len(deleted_session_ids),
            },
        )
        return jsonify(
            {"deleted": True, "work_id": work_id, "session_count": len(deleted_session_ids)}
        )

    @app.get("/api/works/<work_id>/sessions")
    def api_work_sessions(work_id: str) -> Any:
        try:
            work_record = store.get_work(work_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        sessions = store.list_work_agent_sessions(work_id)
        return jsonify(
            {
                "work": {
                    "id": work_record.id,
                    "title": work_record.title,
                    "description": work_record.description,
                    "status": work_record.status,
                    "created_at": work_record.created_at,
                    "updated_at": work_record.updated_at,
                },
                "sessions": sessions,
            }
        )

    @app.get("/api/works/<work_id>/history")
    def api_work_history(work_id: str) -> Any:
        try:
            work_record = store.get_work(work_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        raw_task_limit = request.args.get("task_limit", "2000")
        raw_event_limit = request.args.get("event_limit", "5000")
        raw_request_limit = request.args.get("request_limit", "2000")
        try:
            task_limit = int(raw_task_limit)
        except (TypeError, ValueError):
            task_limit = 2000
        try:
            event_limit = int(raw_event_limit)
        except (TypeError, ValueError):
            event_limit = 5000
        try:
            request_limit = int(raw_request_limit)
        except (TypeError, ValueError):
            request_limit = 2000
        task_limit = max(100, min(task_limit, 10000))
        event_limit = max(100, min(event_limit, 20000))
        request_limit = max(100, min(request_limit, 10000))
        mappings = store.list_work_agent_sessions(work_id)
        grouped = []
        mapped_session_ids: list[str] = []
        agent_name_by_id: dict[str, str] = {}
        agent_role_by_id: dict[str, str] = {}
        for mapping in mappings:
            session_id = str(mapping.get("session_id") or "")
            messages = store.list_messages(session_id)
            if session_id:
                mapped_session_ids.append(session_id)
            agent_id = str(mapping.get("agent_id") or "")
            if agent_id:
                agent_name = mapping.get("agent_name")
                agent_role = mapping.get("agent_role")
                if isinstance(agent_name, str) and agent_name.strip():
                    agent_name_by_id[agent_id] = agent_name
                if isinstance(agent_role, str) and agent_role.strip():
                    agent_role_by_id[agent_id] = agent_role
            grouped.append(
                {
                    "agent_id": mapping.get("agent_id"),
                    "agent_name": mapping.get("agent_name"),
                    "agent_role": mapping.get("agent_role"),
                    "session_id": session_id,
                    "message_count": len(messages),
                    "messages": messages,
                }
            )
        for profile in store.list_agent_profiles():
            if profile.id not in agent_name_by_id:
                agent_name_by_id[profile.id] = profile.name
            if profile.id not in agent_role_by_id:
                agent_role_by_id[profile.id] = profile.role
        unique_session_ids = list(dict.fromkeys(mapped_session_ids))
        requests_by_id: dict[str, dict[str, Any]] = {}
        for session_id in unique_session_ids:
            request_records = store.list_requests(session_id=session_id, limit=request_limit)
            for record in request_records:
                request_id = str(record.get("id") or "")
                if request_id and request_id not in requests_by_id:
                    requests_by_id[request_id] = record

        tasks_by_id: dict[str, dict[str, Any]] = {}
        task_flow = []
        for session_id in unique_session_ids:
            tasks = store.list_tasks(session_id=session_id, limit=task_limit)
            for task in tasks:
                task_id = str(task.get("id") or "")
                if not task_id:
                    continue
                if task_id not in tasks_by_id:
                    owner_agent_id = str(task.get("owner_agent_id") or "")
                    task_copy = dict(task)
                    task_copy["owner_agent_name"] = agent_name_by_id.get(owner_agent_id)
                    task_copy["owner_agent_role"] = agent_role_by_id.get(owner_agent_id)
                    tasks_by_id[task_id] = task_copy
            events = store.list_task_events(session_id=session_id, limit=event_limit)
            for event in events:
                task_id = str(event.get("task_id") or "")
                linked_task = tasks_by_id.get(task_id)
                owner_agent_id = ""
                if linked_task is not None:
                    owner_agent_id = str(linked_task.get("owner_agent_id") or "")
                task_flow.append(
                    {
                        "event_id": event.get("id"),
                        "created_at": event.get("created_at"),
                        "event_type": event.get("event_type"),
                        "session_id": event.get("session_id"),
                        "request_id": event.get("request_id"),
                        "task_id": task_id or None,
                        "payload": event.get("payload"),
                        "task_title": None if linked_task is None else linked_task.get("title"),
                        "task_type": None if linked_task is None else linked_task.get("task_type"),
                        "task_status": None if linked_task is None else linked_task.get("status"),
                        "task_parent_id": (
                            None if linked_task is None else linked_task.get("parent_task_id")
                        ),
                        "owner_agent_id": owner_agent_id or None,
                        "owner_agent_name": agent_name_by_id.get(owner_agent_id),
                        "owner_agent_role": agent_role_by_id.get(owner_agent_id),
                    }
                )
        task_flow.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                int(item.get("event_id") or 0),
            )
        )
        task_records = sorted(
            tasks_by_id.values(),
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
            ),
        )
        request_records = sorted(
            requests_by_id.values(),
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
            ),
        )
        return jsonify(
            {
                "work": {
                    "id": work_record.id,
                    "title": work_record.title,
                    "description": work_record.description,
                    "status": work_record.status,
                    "created_at": work_record.created_at,
                    "updated_at": work_record.updated_at,
                },
                "agent_histories": grouped,
                "task_flow": task_flow,
                "tasks": task_records,
                "requests": request_records,
                "limits": {
                    "task_limit": task_limit,
                    "event_limit": event_limit,
                    "request_limit": request_limit,
                },
            }
        )

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

    @app.get("/api/stream")
    def api_stream() -> Response:
        """SSE endpoint: pushes lightweight change notifications to the browser."""

        def generate():
            q = event_bus.subscribe()
            try:
                # send an initial heartbeat so the connection is confirmed
                yield "event: connected\ndata: {}\n\n"
                while True:
                    try:
                        msg = q.get(timeout=25)
                        yield msg
                    except queue.Empty:
                        # send a keep-alive comment to prevent proxy timeouts
                        yield ": keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                event_bus.unsubscribe(q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

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


def sync_default_agent_tools(store: ChanakyaStore) -> None:
    baseline_tools = ["mcp_websearch", "mcp_fetch", "mcp_calculator"]
    code_exec_tool = "mcp_code_execution"
    sandbox_prompt_hint = (
        " Inside the sandbox, host files are readable but read-only, and only the shared "
        "workspace is writable. If you hit a permission-related error, copy the needed file "
        "into the shared workspace and retry there."
    )
    changed_count = 0
    for profile in store.list_agent_profiles():
        required = list(baseline_tools)
        if profile.role in {"developer", "tester"}:
            required.append(code_exec_tool)
        existing = list(profile.tool_ids_json or [])
        merged: list[str] = []
        for tool_id in [*existing, *required]:
            if tool_id and tool_id not in merged:
                merged.append(tool_id)
        prompt = profile.system_prompt
        if profile.role in {"developer", "tester"} and sandbox_prompt_hint.strip() not in prompt:
            prompt = f"{prompt.rstrip()}{sandbox_prompt_hint}"
        if merged == existing and prompt == profile.system_prompt:
            continue
        store.update_agent_profile(
            profile.id,
            name=profile.name,
            role=profile.role,
            system_prompt=prompt,
            personality=profile.personality,
            tool_ids=merged,
            workspace=profile.workspace,
            heartbeat_enabled=profile.heartbeat_enabled,
            heartbeat_interval_seconds=profile.heartbeat_interval_seconds,
            heartbeat_file_path=profile.heartbeat_file_path,
            is_active=profile.is_active,
        )
        changed_count += 1
    if changed_count:
        debug_log("agent_tool_sync_completed", {"updated_profiles": changed_count})


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
