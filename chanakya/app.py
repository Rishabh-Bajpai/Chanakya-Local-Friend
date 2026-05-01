from __future__ import annotations

import atexit
import json
import queue
import re
import signal
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_file

from chanakya.agent.profile_files import default_heartbeat_relative_path, ensure_agent_profile_files
from chanakya.agent.runtime import MAFRuntime, normalize_runtime_backend
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.config import (
    force_subagents_enabled,
    get_a2a_agent_url,
    get_a2a_gui_enabled,
    get_air_dashboard_url,
    get_air_server_url,
    get_air_status_url,
    get_data_dir,
    get_database_url,
    get_long_term_memory_default_owner_id,
    get_ntfy_default_server_url,
    load_local_env,
)
from chanakya.conversation_layer_support import get_conversation_preference_defaults
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.debug import debug_log
from chanakya.domain import make_id, now_iso
from chanakya.heartbeat import read_heartbeat, resolve_heartbeat_path
from chanakya.model import AgentProfileModel
from chanakya.seed import load_agent_seeds
from chanakya.services.a2a_discovery import discover_a2a_options
from chanakya.services.config_loader import get_mcp_config_path
from chanakya.services.mcp_sandbox_exec_server import (
    ensure_sandbox_image,
    prune_stale_work_containers,
    stop_all_work_containers,
    stop_container,
)
from chanakya.services.mcp_work_tools_server import _create_work
from chanakya.services.ntfy import (
    NtfyClient,
    NtfyNotificationDispatcher,
    build_ntfy_qr_svg,
    is_valid_ntfy_topic,
)
from chanakya.services.sandbox_workspace import (
    delete_shared_workspace,
    get_artifact_storage_root,
    get_shared_workspace_root,
)
from chanakya.services.tool_loader import (
    get_configured_tool_ids,
    get_tools_availability,
    reload_all_tools,
)
from chanakya.store import ChanakyaStore

BASE_DIR = Path(__file__).resolve().parents[1]
_SANDBOX_SHUTDOWN_REGISTERED = False
_SANDBOX_SIGNAL_HANDLERS_REGISTERED = False
_PREVIOUS_SIGNAL_HANDLERS: dict[int, Any] = {}


def _cleanup_all_sandbox_containers() -> dict[str, Any]:
    result = stop_all_work_containers()
    debug_log("sandbox_container_shutdown_cleanup", result)
    return result


def _handle_shutdown_signal(signum: int, frame: Any) -> None:
    _cleanup_all_sandbox_containers()
    previous = _PREVIOUS_SIGNAL_HANDLERS.get(signum)
    if callable(previous):
        previous(signum, frame)
        return
    if previous == signal.SIG_IGN:
        return
    raise SystemExit(0)


def _register_sandbox_shutdown_cleanup() -> None:
    global _SANDBOX_SHUTDOWN_REGISTERED, _SANDBOX_SIGNAL_HANDLERS_REGISTERED
    if not _SANDBOX_SHUTDOWN_REGISTERED:
        atexit.register(_cleanup_all_sandbox_containers)
        _SANDBOX_SHUTDOWN_REGISTERED = True
    if _SANDBOX_SIGNAL_HANDLERS_REGISTERED:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            _PREVIOUS_SIGNAL_HANDLERS[signum] = signal.getsignal(signum)
            signal.signal(signum, _handle_shutdown_signal)
    except ValueError as exc:
        debug_log("sandbox_signal_registration_skipped", {"reason": str(exc)})
        return
    _SANDBOX_SIGNAL_HANDLERS_REGISTERED = True


def _execution_trace_has_tool_data(execution_trace: dict[str, Any] | None) -> bool:
    if not isinstance(execution_trace, dict):
        return False
    tool_calls = execution_trace.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        return True
    for step in list(execution_trace.get("call_sequence") or []):
        if not isinstance(step, dict):
            continue
        tool_traces = step.get("tool_traces")
        if isinstance(tool_traces, list) and tool_traces:
            return True
    return False


def _enrich_execution_trace_with_tool_invocations(
    execution_trace: dict[str, Any] | None,
    tool_invocations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(execution_trace, dict) or not tool_invocations:
        return execution_trace
    if _execution_trace_has_tool_data(execution_trace):
        return execution_trace
    enriched = json.loads(json.dumps(execution_trace))
    grouped: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for item in tool_invocations:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip() or None
        agent_name = str(item.get("agent_name") or "Agent").strip() or "Agent"
        grouped.setdefault((agent_id, agent_name), []).append(
            {
                "tool_id": str(item.get("tool_id") or "").strip() or "unknown_tool",
                "tool_name": str(item.get("tool_name") or "").strip() or "unknown_tool",
                "server_name": str(item.get("server_name") or "unknown_server").strip() or "unknown_server",
                "status": str(item.get("status") or "unknown").strip() or "unknown",
                "input_payload": json.dumps(item.get("input"), ensure_ascii=True, default=str)
                if item.get("input")
                else None,
                "output_text": None if item.get("output") is None else str(item.get("output")),
                "error_text": None if item.get("error") is None else str(item.get("error")),
            }
        )
    call_sequence = list(enriched.get("call_sequence") or [])
    tool_calls: list[dict[str, Any]] = []
    for step in call_sequence:
        if not isinstance(step, dict):
            continue
        if str(step.get("kind") or "") != "participant_turn":
            continue
        existing = list(step.get("tool_traces") or [])
        if existing:
            tool_calls.append(
                {
                    "agent_id": step.get("agent_id"),
                    "agent_name": step.get("agent_name"),
                    "agent_role": step.get("agent_role"),
                    "turn_index": step.get("turn_index"),
                    "tool_traces": existing,
                }
            )
            continue
        key = (
            str(step.get("agent_id") or "").strip() or None,
            str(step.get("agent_name") or "Agent").strip() or "Agent",
        )
        traces = grouped.pop(key, [])
        if traces:
            step["tool_traces"] = traces
            tool_calls.append(
                {
                    "agent_id": step.get("agent_id"),
                    "agent_name": step.get("agent_name"),
                    "agent_role": step.get("agent_role"),
                    "turn_index": step.get("turn_index"),
                    "tool_traces": traces,
                }
            )
    for (agent_id, agent_name), traces in grouped.items():
        if not traces:
            continue
        tool_calls.append(
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_role": None,
                "turn_index": None,
                "tool_traces": traces,
            }
        )
    enriched["call_sequence"] = call_sequence
    enriched["tool_calls"] = tool_calls
    return enriched


def _default_runtime_config() -> dict[str, Any]:
    defaults = get_conversation_preference_defaults()
    return {
        "backend": "local",
        "model_id": None,
        "a2a_url": get_a2a_agent_url(),
        "a2a_remote_agent": None,
        "a2a_model_provider": None,
        "a2a_model_id": None,
        "conversation_tone_instruction": defaults["conversation_tone_instruction"],
        "tts_instruction": defaults["tts_instruction"],
    }


def _normalize_runtime_config(record: dict[str, Any] | None) -> dict[str, Any]:
    config = {**_default_runtime_config(), **(record or {})}
    config["backend"] = normalize_runtime_backend(config.get("backend"))
    for key in (
        "model_id",
        "a2a_url",
        "a2a_remote_agent",
        "a2a_model_provider",
        "a2a_model_id",
        "conversation_tone_instruction",
        "tts_instruction",
    ):
        value = config.get(key)
        if value is None:
            config[key] = None
            continue
        normalized = str(value).strip()
        config[key] = normalized or None
    if config["a2a_url"] is None:
        config["a2a_url"] = get_a2a_agent_url()
    if config["backend"] != "a2a":
        config["a2a_remote_agent"] = None
        config["a2a_model_provider"] = None
        config["a2a_model_id"] = None
    return config


def _parse_runtime_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    backend = normalize_runtime_backend(payload.get("backend"))
    model_id = _parse_optional_string(payload, "model_id") or None
    a2a_url = _parse_optional_string(payload, "a2a_url") or get_a2a_agent_url()
    a2a_remote_agent = _parse_optional_string(payload, "a2a_remote_agent") or None
    a2a_model_provider = _parse_optional_string(payload, "a2a_model_provider") or None
    a2a_model_id = _parse_optional_string(payload, "a2a_model_id") or None
    conversation_tone_instruction = (
        _parse_optional_string(payload, "conversation_tone_instruction") or None
    )
    tts_instruction = _parse_optional_string(payload, "tts_instruction") or None
    if backend != "a2a":
        a2a_remote_agent = None
        a2a_model_provider = None
        a2a_model_id = None
    return {
        "backend": backend,
        "model_id": model_id,
        "a2a_url": a2a_url,
        "a2a_remote_agent": a2a_remote_agent,
        "a2a_model_provider": a2a_model_provider,
        "a2a_model_id": a2a_model_id,
        "conversation_tone_instruction": conversation_tone_instruction,
        "tts_instruction": tts_instruction,
    }


def _serialize_artifact_payload(record: dict[str, Any]) -> dict[str, Any]:
    request_id = str(record.get("request_id") or "").strip() or None
    latest_request_id = str(record.get("latest_request_id") or "").strip() or None
    if request_id and latest_request_id and request_id != latest_request_id:
        request_relation = "updated_in_later_request"
    elif latest_request_id:
        request_relation = "created_in_request"
    else:
        request_relation = "unknown"
    return {
        **record,
        "origin_request_id": request_id,
        "request_relation": request_relation,
        "download_url": f"/api/artifacts/{record['id']}/download",
        "detail_url": f"/api/artifacts/{record['id']}",
    }


def _resolve_artifact_file(record: dict[str, Any]) -> Path:
    artifact_root = get_artifact_storage_root(create=False).resolve()
    candidate = (artifact_root / str(record.get("path") or "")).resolve()
    if artifact_root not in candidate.parents and candidate != artifact_root:
        raise PermissionError("Artifact path escapes workspace")
    return candidate


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
    _register_sandbox_shutdown_cleanup()
    sandbox_image_status = ensure_sandbox_image()
    debug_log("sandbox_image_startup_status", sandbox_image_status)
    valid_work_ids = {str(item.get("id") or "").strip() for item in store.list_works(limit=1000)}
    valid_work_ids.discard("")
    sandbox_prune = prune_stale_work_containers(valid_work_ids, remove_running=False)
    debug_log("sandbox_container_startup_prune", sandbox_prune)
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
    ntfy_dispatcher = NtfyNotificationDispatcher(store, NtfyClient())
    try:
        chat_service = ChatService(
            store,
            runtime,
            manager,
            notification_dispatcher=ntfy_dispatcher,
        )
    except TypeError:
        chat_service = ChatService(store, runtime, manager)
    app.extensions["chanakya_store"] = store
    app.extensions["ntfy_dispatcher"] = ntfy_dispatcher

    def get_runtime_config() -> dict[str, Any]:
        return _normalize_runtime_config(store.get_runtime_config())

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
            a2a_agent_url=get_a2a_agent_url(),
            a2a_gui_enabled=get_a2a_gui_enabled(),
            force_subagents_enabled=force_subagents_enabled(),
            conversation_preferences_defaults=get_conversation_preference_defaults(),
        )

    def render_work_page(*, initial_work_id: str | None = None) -> str:
        return render_template(
            "work.html",
            air_dashboard_url=get_air_dashboard_url(),
            air_server_url=get_air_server_url(),
            air_status_url=get_air_status_url(),
            a2a_agent_url=get_a2a_agent_url(),
            a2a_gui_enabled=get_a2a_gui_enabled(),
            force_subagents_enabled=force_subagents_enabled(),
            initial_work_id=initial_work_id,
        )

    @app.get("/work")
    def work() -> str:
        requested_work_id = str(request.args.get("work_id") or "").strip() or None
        return render_work_page(initial_work_id=requested_work_id)

    @app.get("/work/<work_id>")
    def work_detail(work_id: str) -> str:
        return render_work_page(initial_work_id=work_id)

    @app.get("/agent")
    def agent() -> str:
        return render_work_page()

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
        runtime_config = get_runtime_config()
        raw_model_id = payload.get("model_id")
        model_id = (
            str(raw_model_id).strip() if raw_model_id is not None else runtime_config["model_id"]
        )
        if model_id == "":
            model_id = None
        raw_backend = payload.get("backend")
        backend = (
            normalize_runtime_backend(raw_backend)
            if raw_backend is not None
            else str(runtime_config["backend"])
        )
        raw_a2a_url = payload.get("a2a_url")
        a2a_url = str(raw_a2a_url).strip() if raw_a2a_url is not None else runtime_config["a2a_url"]
        if a2a_url == "":
            a2a_url = None
        raw_a2a_remote_agent = payload.get("a2a_remote_agent")
        a2a_remote_agent = (
            str(raw_a2a_remote_agent).strip()
            if raw_a2a_remote_agent is not None
            else runtime_config["a2a_remote_agent"]
        )
        if a2a_remote_agent == "":
            a2a_remote_agent = None
        raw_a2a_model_provider = payload.get("a2a_model_provider")
        a2a_model_provider = (
            str(raw_a2a_model_provider).strip()
            if raw_a2a_model_provider is not None
            else runtime_config["a2a_model_provider"]
        )
        if a2a_model_provider == "":
            a2a_model_provider = None
        raw_a2a_model_id = payload.get("a2a_model_id")
        a2a_model_id = (
            str(raw_a2a_model_id).strip()
            if raw_a2a_model_id is not None
            else runtime_config["a2a_model_id"]
        )
        if a2a_model_id == "":
            a2a_model_id = None
        if backend != "a2a":
            a2a_url = None
            a2a_remote_agent = None
            a2a_model_provider = None
            a2a_model_id = None
        raw_conversation_tone_instruction = payload.get("conversation_tone_instruction")
        conversation_tone_instruction = (
            str(raw_conversation_tone_instruction).strip()
            if raw_conversation_tone_instruction is not None
            else runtime_config["conversation_tone_instruction"]
        )
        if conversation_tone_instruction == "":
            conversation_tone_instruction = None
        raw_tts_instruction = payload.get("tts_instruction")
        tts_instruction = (
            str(raw_tts_instruction).strip()
            if raw_tts_instruction is not None
            else runtime_config["tts_instruction"]
        )
        if tts_instruction == "":
            tts_instruction = None
        raw_message_metadata = payload.get("message_metadata")
        message_metadata = (
            dict(raw_message_metadata)
            if isinstance(raw_message_metadata, dict)
            else None
        )
        debug_log(
            "api_chat_request",
            {
                "session_id": session_id,
                "work_id": work_id,
                "model_id": model_id,
                "backend": backend,
                "a2a_url": a2a_url,
                "a2a_remote_agent": a2a_remote_agent,
                "a2a_model_provider": a2a_model_provider,
                "a2a_model_id": a2a_model_id,
                "conversation_tone_instruction": conversation_tone_instruction,
                "tts_instruction": tts_instruction,
                "message_metadata": message_metadata,
                "message": message,
                "has_existing_session": bool(payload.get("session_id")),
            },
        )
        if not message:
            return jsonify({"error": "message is required"}), 400
        store.ensure_session(session_id, title=message[:60] or "New chat")
        try:
            reply = chat_service.chat(
                session_id,
                message,
                work_id=work_id,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
                conversation_tone_instruction=conversation_tone_instruction,
                tts_instruction=tts_instruction,
                message_metadata=message_metadata,
            )
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

    @app.get("/api/runtime-config")
    def api_runtime_config() -> Any:
        return jsonify(get_runtime_config())

    @app.post("/api/runtime-config")
    def api_set_runtime_config() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            config = _parse_runtime_config_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        persisted = store.set_runtime_config(**config)
        normalized = _normalize_runtime_config(persisted)
        event_bus.publish(
            "runtime_config_updated",
            {
                "backend": normalized["backend"],
                "model_id": normalized["model_id"],
                "a2a_model_id": normalized["a2a_model_id"],
                "conversation_tone_instruction": normalized["conversation_tone_instruction"],
                "tts_instruction": normalized["tts_instruction"],
            },
        )
        return jsonify(normalized)

    @app.get("/api/sessions/<session_id>/next-message")
    def api_session_next_message(session_id: str) -> Any:
        payload = chat_service.deliver_next_conversation_message(session_id)
        return jsonify(payload)

    @app.post("/api/sessions/<session_id>/pause")
    def api_session_pause(session_id: str) -> Any:
        payload = chat_service.request_manual_pause(session_id)
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

    @app.get("/api/sessions/<session_id>/active-work")
    def api_session_active_work(session_id: str) -> Any:
        active_work = store.get_active_classic_work(session_id)
        if active_work is None:
            return jsonify({"session_id": session_id, "active_work": None})
        return jsonify(
            {
                "session_id": session_id,
                "active_work": {
                    "work_id": str(active_work.get("work_id") or ""),
                    "work_session_id": str(active_work.get("work_session_id") or ""),
                    "title": str(active_work.get("title") or ""),
                    "summary": str(active_work.get("summary") or ""),
                    "workflow_type": str(active_work.get("workflow_type") or ""),
                },
            }
        )

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

    @app.get("/api/requests/<request_id>/artifacts")
    def api_request_artifacts(request_id: str) -> Any:
        raw_artifacts = store.list_artifacts_for_request(request_id)
        artifacts = []
        for item in raw_artifacts:
            payload = _serialize_artifact_payload(item)
            origin_request_id = str(item.get("request_id") or "").strip() or None
            latest_request_id = str(item.get("latest_request_id") or "").strip() or None
            if origin_request_id == request_id and latest_request_id == request_id:
                payload["request_relation"] = "created_and_latest_in_request"
            elif origin_request_id == request_id:
                payload["request_relation"] = "created_in_request"
            elif latest_request_id == request_id:
                payload["request_relation"] = "updated_in_request"
            else:
                payload["request_relation"] = "related_via_lineage"
            artifacts.append(payload)
        return jsonify({"request_id": request_id, "artifacts": artifacts})

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

    @app.get("/api/memory")
    def api_memory() -> Any:
        owner_id = (
            str(request.args.get("owner_id") or get_long_term_memory_default_owner_id()).strip()
            or get_long_term_memory_default_owner_id()
        )
        session_id = request.args.get("session_id")
        status = request.args.get("status", "active")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        normalized_status = str(status).strip() if status is not None else "active"
        if normalized_status == "":
            normalized_status = None
        memories = store.list_memories(
            owner_id=owner_id,
            status=normalized_status,
            session_id=session_id,
            limit=limit,
        )
        counts_by_status: dict[str, int] = {}
        counts_by_type: dict[str, int] = {}
        for item in memories:
            status_key = str(item.get("status") or "unknown")
            type_key = str(item.get("type") or "unknown")
            counts_by_status[status_key] = counts_by_status.get(status_key, 0) + 1
            counts_by_type[type_key] = counts_by_type.get(type_key, 0) + 1
        return jsonify(
            {
                "owner_id": owner_id,
                "session_id": session_id,
                "status": normalized_status,
                "count": len(memories),
                "counts_by_status": counts_by_status,
                "counts_by_type": counts_by_type,
                "memories": memories,
            }
        )

    @app.get("/api/memory/events")
    def api_memory_events() -> Any:
        owner_id = (
            str(request.args.get("owner_id") or get_long_term_memory_default_owner_id()).strip()
            or get_long_term_memory_default_owner_id()
        )
        session_id = request.args.get("session_id")
        request_id = request.args.get("request_id")
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        events = store.list_memory_events(
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
            limit=limit,
        )
        counts_by_type: dict[str, int] = {}
        for item in events:
            event_type = str(item.get("event_type") or "unknown")
            counts_by_type[event_type] = counts_by_type.get(event_type, 0) + 1
        return jsonify(
            {
                "owner_id": owner_id,
                "session_id": session_id,
                "request_id": request_id,
                "count": len(events),
                "counts_by_type": counts_by_type,
                "events": events,
            }
        )

    @app.get("/api/sessions/<session_id>/memory")
    def api_session_memory(session_id: str) -> Any:
        owner_id = (
            str(request.args.get("owner_id") or get_long_term_memory_default_owner_id()).strip()
            or get_long_term_memory_default_owner_id()
        )
        raw_limit = request.args.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        memories = store.list_memories(
            owner_id=owner_id,
            status=None,
            session_id=session_id,
            limit=limit,
        )
        events = store.list_memory_events(
            owner_id=owner_id,
            session_id=session_id,
            limit=limit,
        )
        counts_by_status: dict[str, int] = {}
        counts_by_type: dict[str, int] = {}
        event_counts_by_type: dict[str, int] = {}
        latest_retrieval = None
        latest_operations_applied = None
        latest_failure = None
        latest_background_job = None
        for item in memories:
            status_key = str(item.get("status") or "unknown")
            type_key = str(item.get("type") or "unknown")
            counts_by_status[status_key] = counts_by_status.get(status_key, 0) + 1
            counts_by_type[type_key] = counts_by_type.get(type_key, 0) + 1
        for item in events:
            event_type = str(item.get("event_type") or "unknown")
            event_counts_by_type[event_type] = event_counts_by_type.get(event_type, 0) + 1
            if event_type == "memory_retrieved":
                latest_retrieval = item
            elif event_type == "memory_operations_applied":
                latest_operations_applied = item
            elif event_type == "memory_extraction_failed":
                latest_failure = item
            elif event_type == "memory_background_job_finished":
                latest_background_job = item
        return jsonify(
            {
                "owner_id": owner_id,
                "session_id": session_id,
                "memory_count": len(memories),
                "event_count": len(events),
                "counts_by_status": counts_by_status,
                "counts_by_type": counts_by_type,
                "event_counts_by_type": event_counts_by_type,
                "latest_retrieval": latest_retrieval,
                "latest_operations_applied": latest_operations_applied,
                "latest_failure": latest_failure,
                "latest_background_job": latest_background_job,
                "memories": memories,
                "events": events,
            }
        )

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

    @app.post("/api/tools/reload")
    def api_tools_reload() -> Any:
        tools = reload_all_tools()
        available_count = sum(1 for item in tools if str(item.get("status") or "") == "available")
        return jsonify(
            {
                "ok": True,
                "tools": tools,
                "tool_count": len(tools),
                "available_count": available_count,
            }
        )

    @app.get("/api/tools/config")
    def api_tools_config() -> Any:
        config_path = get_mcp_config_path()
        if config_path.exists():
            raw_text = config_path.read_text(encoding="utf-8")
        else:
            raw_text = json.dumps({"mcpServers": {}}, indent=2) + "\n"
        try:
            parsed = json.loads(raw_text)
            servers = _extract_mcp_servers(parsed)
        except ValueError:
            servers = {}
        return jsonify(
            {
                "config_path": str(config_path),
                "raw_text": raw_text,
                "server_ids": sorted(servers.keys()),
                "server_count": len(servers),
            }
        )

    @app.put("/api/tools/config")
    def api_put_tools_config() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            config_text = _parse_mcp_config_text(payload)
            parsed = json.loads(config_text)
            servers = _extract_mcp_servers(parsed)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        config_path = get_mcp_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_text, encoding="utf-8")
        should_reload = bool(payload.get("reload", False))
        tools = reload_all_tools() if should_reload else get_tools_availability()
        available_count = sum(1 for item in tools if str(item.get("status") or "") == "available")
        return jsonify(
            {
                "ok": True,
                "config_path": str(config_path),
                "server_ids": sorted(servers.keys()),
                "server_count": len(servers),
                "raw_text": config_text,
                "reloaded": should_reload,
                "tools": tools,
                "tool_count": len(tools),
                "available_count": available_count,
            }
        )

    @app.get("/api/notifications/ntfy")
    def api_get_ntfy_settings() -> Any:
        return jsonify(ntfy_dispatcher.get_settings_payload())

    @app.put("/api/notifications/ntfy")
    def api_put_ntfy_settings() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            settings = _parse_ntfy_settings_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(ntfy_dispatcher.save_settings(**settings))

    @app.delete("/api/notifications/ntfy")
    def api_delete_ntfy_settings() -> Any:
        return jsonify(ntfy_dispatcher.delete_settings())

    @app.post("/api/notifications/ntfy/test")
    def api_test_ntfy_settings() -> Any:
        try:
            result = ntfy_dispatcher.send_test_notification()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not result.ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "status": result.status,
                        "error": result.error or "Notification publish failed",
                    }
                ),
                502,
            )
        return jsonify({"ok": True, "status": result.status})

    @app.get("/api/notifications/ntfy/qr.svg")
    def api_ntfy_qr_svg() -> Response:
        server_url = (request.args.get("server_url") or get_ntfy_default_server_url()).strip()
        topic = (request.args.get("topic") or "").strip()
        if not server_url.startswith("https://"):
            return Response(
                "server_url must start with https://", status=400, mimetype="text/plain"
            )
        if not is_valid_ntfy_topic(topic):
            return Response(
                "topic must be 6-128 chars and use only letters, numbers, dot, underscore, or dash",
                status=400,
                mimetype="text/plain",
            )
        return Response(
            build_ntfy_qr_svg(server_url=server_url, topic=topic),
            mimetype="image/svg+xml",
        )

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
        result = _create_work(store, title=title, description=description)
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "Work creation failed"}), 400
        return jsonify({key: value for key, value in result.items() if key != "ok"}), 201

    @app.get("/api/works")
    def api_list_works() -> Any:
        raw_limit = request.args.get("limit", "100")
        raw_status = request.args.get("status")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        status = str(raw_status).strip() if raw_status is not None else None
        if status == "":
            status = None
        works = store.list_works(limit=limit, status=status)
        return jsonify({"works": works})

    @app.get("/api/works/<work_id>/artifacts")
    def api_work_artifacts(work_id: str) -> Any:
        try:
            store.get_work(work_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        artifacts = [
            _serialize_artifact_payload(item)
            for item in store.list_artifacts_for_work(work_id)
        ]
        return jsonify({"work_id": work_id, "artifacts": artifacts})

    @app.get("/api/artifacts/<artifact_id>")
    def api_artifact_detail(artifact_id: str) -> Any:
        try:
            artifact = store.get_artifact(artifact_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        return jsonify(
            _serialize_artifact_payload(
                {
                    "id": artifact.id,
                    "request_id": artifact.request_id,
                    "session_id": artifact.session_id,
                    "work_id": artifact.work_id,
                    "name": artifact.name,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "path": artifact.path,
                    "mime_type": artifact.mime_type,
                    "kind": artifact.kind,
                    "size_bytes": artifact.size_bytes,
                    "source_agent_id": artifact.source_agent_id,
                    "source_agent_name": artifact.source_agent_name,
                    "latest_request_id": artifact.latest_request_id,
                    "supersedes_artifact_id": artifact.supersedes_artifact_id,
                    "created_at": artifact.created_at,
                    "updated_at": artifact.updated_at,
                }
            )
        )

    @app.get("/api/artifacts/<artifact_id>/download")
    def api_artifact_download(artifact_id: str) -> Any:
        try:
            artifact = store.get_artifact(artifact_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        artifact_payload = {
            "id": artifact.id,
            "request_id": artifact.request_id,
            "session_id": artifact.session_id,
            "work_id": artifact.work_id,
            "name": artifact.name,
            "title": artifact.title,
            "summary": artifact.summary,
            "path": artifact.path,
            "mime_type": artifact.mime_type,
            "kind": artifact.kind,
            "size_bytes": artifact.size_bytes,
            "source_agent_id": artifact.source_agent_id,
            "source_agent_name": artifact.source_agent_name,
            "latest_request_id": artifact.latest_request_id,
            "supersedes_artifact_id": artifact.supersedes_artifact_id,
            "created_at": artifact.created_at,
            "updated_at": artifact.updated_at,
        }
        try:
            artifact_file = _resolve_artifact_file(artifact_payload)
        except (PermissionError, ValueError):
            return jsonify({"error": "Artifact path is invalid"}), 400
        if not artifact_file.is_file():
            return jsonify({"error": "Artifact file not found"}), 404
        return send_file(
            artifact_file,
            as_attachment=True,
            download_name=artifact.name,
            mimetype=artifact.mime_type or "application/octet-stream",
        )

    @app.delete("/api/works/<work_id>")
    def api_delete_work(work_id: str) -> Any:
        try:
            deleted_session_ids, deleted_artifact_ids = store.delete_work(work_id)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            return jsonify({"error": message}), 404
        for session_id in deleted_session_ids:
            runtime.clear_session_state(session_id)
        container_cleanup = stop_container(work_id)
        workspace_cleanup = delete_shared_workspace(work_id)
        artifact_root = get_artifact_storage_root(create=False)
        for artifact_id in deleted_artifact_ids:
            artifact_dir = artifact_root / artifact_id
            if artifact_dir.exists():
                for p in sorted(artifact_dir.rglob("*"), reverse=True):
                    p.unlink() if p.is_file() else p.rmdir()
                artifact_dir.rmdir()
        store.log_event(
            "work_deleted",
            {
                "work_id": work_id,
                "session_count": len(deleted_session_ids),
                "artifact_count": len(deleted_artifact_ids),
                "container_cleanup_ok": bool(container_cleanup.get("ok")),
                "workspace_cleanup_ok": bool(workspace_cleanup.get("ok")),
            },
        )
        response = {
            "deleted": True,
            "work_id": work_id,
            "session_count": len(deleted_session_ids),
            "container": container_cleanup,
        }
        warning: dict[str, Any] | None = None
        if not container_cleanup.get("ok") and not workspace_cleanup.get("ok"):
            warning = {
                "code": "cleanup_failed",
                "message": "Work deleted, but sandbox container and workspace cleanup failed.",
                "container": container_cleanup,
                "workspace": workspace_cleanup,
            }
            store.log_event(
                "work_cleanup_failed",
                {
                    "work_id": work_id,
                    "session_count": len(deleted_session_ids),
                    "container": container_cleanup,
                    "workspace": workspace_cleanup,
                },
            )
        elif not container_cleanup.get("ok"):
            warning = {
                "code": "container_cleanup_failed",
                "message": "Work deleted, but sandbox container cleanup failed.",
                "container": container_cleanup,
            }
            store.log_event(
                "work_container_cleanup_failed",
                {
                    "work_id": work_id,
                    "session_count": len(deleted_session_ids),
                    "container": container_cleanup,
                },
            )
        elif not workspace_cleanup.get("ok"):
            warning = {
                "code": "workspace_cleanup_failed",
                "message": "Work deleted, but sandbox workspace cleanup failed.",
                "workspace": workspace_cleanup,
            }
            store.log_event(
                "work_workspace_cleanup_failed",
                {
                    "work_id": work_id,
                    "session_count": len(deleted_session_ids),
                    "workspace": workspace_cleanup,
                },
            )
        if warning is not None:
            response["warning"] = warning
        return jsonify(response)

    @app.get("/api/a2a/options")
    def api_a2a_options() -> Any:
        a2a_url = str(request.args.get("url") or "").strip()
        if not a2a_url:
            return jsonify({"error": "Missing required query parameter: url"}), 400
        try:
            options = discover_a2a_options(a2a_url)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(options)

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
        chanakya_session_id = ""
        conversation_messages: list[dict[str, Any]] = []
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
            mirrored_count = 0
            visible_count = 0
            private_count = 0
            assistant_count = 0
            user_count = 0
            latest_preview = ""
            latest_created_at = None
            for message in messages:
                metadata = dict(message.get("metadata") or {})
                if message.get("role") == "assistant":
                    assistant_count += 1
                elif message.get("role") == "user":
                    user_count += 1
                if metadata.get("mirrored_from_work_session"):
                    mirrored_count += 1
                elif metadata.get("visible_agent_name") or metadata.get("group_chat_visible"):
                    visible_count += 1
                else:
                    private_count += 1
                latest_preview = str(message.get("content") or "").replace("\n", " ").strip()[:180]
                latest_created_at = message.get("created_at")
            if agent_id == "agent_chanakya":
                chanakya_session_id = session_id
                conversation_messages = messages
            grouped.append(
                {
                    "agent_id": mapping.get("agent_id"),
                    "agent_name": mapping.get("agent_name"),
                    "agent_role": mapping.get("agent_role"),
                    "session_id": session_id,
                    "message_count": len(messages),
                    "message_stats": {
                        "user_count": user_count,
                        "assistant_count": assistant_count,
                        "mirrored_count": mirrored_count,
                        "visible_count": visible_count,
                        "private_count": private_count,
                    },
                    "latest_message_preview": latest_preview,
                    "latest_created_at": latest_created_at,
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
        tasks_by_request_id: dict[str, list[dict[str, Any]]] = {}
        for task in task_records:
            request_id = str(task.get("request_id") or "")
            if not request_id:
                continue
            tasks_by_request_id.setdefault(request_id, []).append(task)

        group_chat_runs: list[dict[str, Any]] = []
        if not hasattr(manager, "_group_chat_participant_profiles") or not hasattr(
            manager, "build_group_chat_execution_trace"
        ):
            participant_profiles = []
        else:
            try:
                participant_profiles = manager._group_chat_participant_profiles()
            except KeyError:
                participant_profiles = []
        for request_record in request_records:
            request_id = str(request_record.get("id") or "")
            request_message = str(request_record.get("user_message") or "")
            request_created_at = str(request_record.get("created_at") or "")
            request_tasks = tasks_by_request_id.get(request_id, [])
            manager_task = next(
                (
                    item
                    for item in request_tasks
                    if str(item.get("task_type") or "") == "manager_group_chat_orchestration"
                ),
                None,
            )
            if manager_task is None:
                continue
            manager_result = manager_task.get("result")
            if not isinstance(manager_result, dict):
                manager_result = {}
            execution_trace = manager_result.get("execution_trace")
            request_tool_invocations = store.list_tool_invocations(request_id=request_id, limit=500)
            if not isinstance(execution_trace, dict):
                visible_messages = manager_result.get("visible_messages")
                if not isinstance(visible_messages, list):
                    visible_messages = []
                completion_payload = manager_result.get("completion")
                if not isinstance(completion_payload, dict):
                    completion_payload = {
                        "status": str(manager_task.get("status") or "unknown").lower(),
                        "summary": str(manager_result.get("summary") or "").strip(),
                    }
                prior_messages = [
                    item
                    for item in conversation_messages
                    if str(item.get("created_at") or "") < request_created_at
                ]
                seeded_conversation = manager.build_group_chat_seed_conversation_from_records(
                    prior_messages
                )
                if participant_profiles:
                    execution_trace = manager.build_group_chat_execution_trace(
                        request_message=request_message,
                        participant_profiles=participant_profiles,
                        seeded_conversation=seeded_conversation,
                        visible_messages=visible_messages,
                        completion_payload=completion_payload,
                        work_id=work_record.id,
                    )
                else:
                    execution_trace = {
                        "workflow_type": "work_group_chat",
                        "request_message": request_message,
                        "seeded_context": [
                            {
                                "role": str(item.role or "assistant"),
                                "author_name": str(item.author_name or "").strip() or None,
                                "text": str(item.text or ""),
                            }
                            for item in seeded_conversation
                        ],
                        "orchestrator": None,
                        "participants": [],
                        "call_sequence": [],
                        "completion": completion_payload,
                        "prompt_refs": {},
                    }
            execution_trace = _enrich_execution_trace_with_tool_invocations(
                execution_trace,
                request_tool_invocations,
            )
            group_chat_runs.append(
                {
                    "request_id": request_id,
                    "created_at": request_record.get("created_at"),
                    "status": manager_task.get("status"),
                    "route": request_record.get("route"),
                    "user_message": request_message,
                    "root_task_id": request_record.get("root_task_id"),
                    "manager_task_id": manager_task.get("id"),
                    "child_task_ids": manager_result.get("child_task_ids") if isinstance(manager_result, dict) else [],
                    "execution_trace": execution_trace,
                }
            )
        latest_root_task = next((item for item in reversed(task_records) if item.get("is_root")), None)
        active_runtime: dict[str, Any] | None = None
        if latest_root_task is not None:
            latest_input = dict(latest_root_task.get("input") or {})
            pending_interaction = latest_input.get("work_pending_interaction")
            if not isinstance(pending_interaction, dict):
                pending_interaction = None
            group_chat_state = latest_input.get("work_group_chat_state")
            if not isinstance(group_chat_state, dict):
                group_chat_state = None
            active_runtime = {
                "root_task_id": latest_root_task.get("id"),
                "request_id": latest_root_task.get("request_id"),
                "task_status": latest_root_task.get("status"),
                "workflow_type": (
                    None
                    if group_chat_state is None
                    else group_chat_state.get("workflow_type")
                ),
                "pending_interaction": pending_interaction,
                "group_chat_state": group_chat_state,
                "reload_reproducible": bool(group_chat_state or pending_interaction),
            }
        work_artifacts = [
            _serialize_artifact_payload(item)
            for item in store.list_artifacts_for_work(work_record.id)
        ]
        conversation_assistant_count = sum(
            1 for item in conversation_messages if str(item.get("role") or "") == "assistant"
        )
        conversation_user_count = sum(
            1 for item in conversation_messages if str(item.get("role") or "") == "user"
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
                "conversation": {
                    "session_id": chanakya_session_id or None,
                    "message_count": len(conversation_messages),
                    "assistant_count": conversation_assistant_count,
                    "user_count": conversation_user_count,
                    "messages": conversation_messages,
                },
                "agent_histories": grouped,
                "group_chat_inspector": {
                    "workflow_type": "work_group_chat",
                    "run_count": len(group_chat_runs),
                    "runs": group_chat_runs,
                },
                "active_runtime": active_runtime,
                "artifacts": work_artifacts,
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

    @app.get("/api/works/pending-messages")
    def api_pending_messages() -> Any:
        work_id = request.args.get("work_id")
        since = request.args.get("since")
        include_acknowledged = request.args.get("include_acknowledged", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        notifications = store.work_notifications.list_pending(
            work_id=work_id if work_id else None,
            include_acknowledged=include_acknowledged,
            since=since if since else None,
        )
        return jsonify({"notifications": notifications})

    @app.post("/api/works/pending-messages/<message_id>/ack")
    def api_ack_pending_message(message_id: str) -> Any:
        success = store.work_notifications.acknowledge(message_id)
        if not success:
            return jsonify({"error": "Notification not found"}), 404
        return jsonify({"ok": True, "id": message_id})

    @app.post("/api/agents")
    def api_create_agent() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            agent_data = _parse_agent_payload(payload)
            _validate_agent_tool_ids(agent_data["tool_ids"])
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
            _validate_agent_tool_ids(agent_data["tool_ids"])
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
    memory_agent_tool = "mcp_memory_agent"
    configured_tool_ids = get_configured_tool_ids()
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
        if profile.id == "agent_chanakya" and memory_agent_tool in configured_tool_ids:
            required.append(memory_agent_tool)
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


def _validate_agent_tool_ids(tool_ids: list[str]) -> None:
    configured_tool_ids = get_configured_tool_ids()
    if not configured_tool_ids:
        return
    unknown = [tool_id for tool_id in tool_ids if tool_id not in configured_tool_ids]
    if unknown:
        raise ValueError(
            "Unknown tool_ids: " + ", ".join(sorted(dict.fromkeys(unknown)))
        )


def _extract_mcp_servers(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("MCP config must be a JSON object")
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("MCP config must contain an object field named mcpServers")
    for server_id, details in servers.items():
        if not isinstance(server_id, str) or not server_id.strip():
            raise ValueError("Each MCP server id must be a non-empty string")
        if not isinstance(details, dict):
            raise ValueError(f"Invalid MCP config for {server_id}: expected an object")
    return servers


def _parse_mcp_config_text(payload: dict[str, Any]) -> str:
    raw_text = payload.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("raw_text must be a non-empty JSON string")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MCP config JSON: {exc.msg}") from exc
    _extract_mcp_servers(parsed)
    return json.dumps(parsed, indent=2, ensure_ascii=True) + "\n"


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


def _parse_ntfy_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    server_url = _parse_optional_string(payload, "server_url") or get_ntfy_default_server_url()
    if not server_url.startswith("https://"):
        raise ValueError("server_url must start with https://")
    topic = _parse_optional_string(payload, "topic")
    enabled = _parse_required_bool(payload, "enabled", default=False)
    include_message_preview = _parse_required_bool(
        payload,
        "include_message_preview",
        default=True,
    )
    if enabled and not topic:
        raise ValueError("topic is required when notifications are enabled")
    if topic and not is_valid_ntfy_topic(topic):
        raise ValueError(
            "topic must be 6-128 chars and use only letters, numbers, dot, underscore, or dash"
        )
    return {
        "server_url": server_url.rstrip("/"),
        "topic": topic,
        "enabled": enabled,
        "include_message_preview": include_message_preview,
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
