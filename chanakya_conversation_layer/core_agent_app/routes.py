from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from conversation_layer.schemas import ChatRequest


def register_routes(app: Flask) -> None:
    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/health")
    def health() -> tuple[dict, int]:
        return {"status": "ok"}, 200

    @app.get("/runtime/options")
    def runtime_options() -> tuple[dict, int]:
        raw_agent = app.extensions.get("raw_agent")
        conversation_wrapper = app.extensions.get("conversation_wrapper")
        core_agent_options = (
            raw_agent.runtime_options()
            if raw_agent is not None and hasattr(raw_agent, "runtime_options")
            else app.extensions.get("core_agent_runtime_options") or {}
        )
        orchestration_options = (
            app.extensions.get("conversation_orchestration_runtime_options") or {}
        )
        return jsonify(
            {
                "core_agent": core_agent_options,
                "conversation_orchestration": orchestration_options,
                "conversation_layer": (
                    conversation_wrapper.runtime_options()
                    if conversation_wrapper is not None
                    and hasattr(conversation_wrapper, "runtime_options")
                    else {}
                ),
            }
        ), 200

    @app.post("/chat")
    def chat() -> tuple[dict, int]:
        return _handle_chat(app, "conversation_wrapper")

    @app.get("/sessions")
    def sessions() -> tuple[dict, int]:
        history_provider = app.extensions["history_provider"]
        return jsonify({"sessions": history_provider.list_sessions()}), 200

    @app.delete("/sessions/<session_id>")
    def delete_session(session_id: str) -> tuple[dict, int]:
        history_provider = app.extensions["history_provider"]
        conversation_wrapper = app.extensions["conversation_wrapper"]
        session_context_store = app.extensions.get("agent_session_context_store")
        history_provider.delete_session(session_id)
        conversation_wrapper.state_store.clear(session_id)
        if session_context_store is not None and hasattr(
            session_context_store, "delete"
        ):
            session_context_store.delete(session_id)
        return jsonify({"session_id": session_id, "deleted": True}), 200

    @app.get("/sessions/<session_id>/history")
    def session_history(session_id: str) -> tuple[dict, int]:
        history_provider = app.extensions["history_provider"]
        return jsonify(
            {
                "session_id": session_id,
                "messages": history_provider.list_messages(session_id),
            }
        ), 200

    @app.get("/sessions/<session_id>/working-memory")
    def session_working_memory(session_id: str) -> tuple[dict, int]:
        conversation_wrapper = app.extensions["conversation_wrapper"]
        return jsonify(
            {
                "session_id": session_id,
                "working_memory": conversation_wrapper.list_debug_view(session_id),
            }
        ), 200

    @app.post("/sessions/<session_id>/pause")
    def pause_session_delivery(session_id: str) -> tuple[dict, int]:
        conversation_wrapper = app.extensions["conversation_wrapper"]
        return jsonify(
            {
                "session_id": session_id,
                "working_memory": conversation_wrapper.request_manual_pause(session_id),
            }
        ), 200

    @app.get("/sessions/<session_id>/next-message")
    def session_next_message(session_id: str) -> tuple[dict, int]:
        conversation_wrapper = app.extensions["conversation_wrapper"]
        payload = conversation_wrapper.deliver_next_message(session_id)
        payload["session_id"] = session_id
        return jsonify(payload), 200

    @app.get("/sessions/<session_id>/debug-state")
    def session_debug_state(session_id: str) -> tuple[dict, int]:
        history_provider = app.extensions["history_provider"]
        conversation_wrapper = app.extensions["conversation_wrapper"]
        raw_agent = app.extensions["raw_agent"]
        return jsonify(
            {
                "session_id": session_id,
                "core_agent_backend": app.extensions.get("core_agent_backend"),
                "conversation_layer_debug_state": conversation_wrapper.get_agent_debug_state(
                    session_id
                ),
                "raw_agent_debug_state": raw_agent.get_debug_state(session_id)
                if hasattr(raw_agent, "get_debug_state")
                else {},
                "history": history_provider.list_messages(session_id),
                "working_memory": conversation_wrapper.list_debug_view(session_id),
            }
        ), 200


def _handle_chat(app: Flask, extension_key: str) -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    chat_request = ChatRequest(
        session_id=str(payload.get("session_id", "")).strip(),
        message=str(payload.get("message", "")).strip(),
        metadata=payload.get("metadata") or {},
    )
    try:
        handler = app.extensions[extension_key]
        chat_request.metadata["conversation_layer_path"] = extension_key
        response = handler.handle(chat_request)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    response.metadata.setdefault(
        "core_agent_backend", app.extensions.get("core_agent_backend")
    )
    response.metadata.setdefault("conversation_layer_path", extension_key)
    return jsonify(response.to_dict()), 200
