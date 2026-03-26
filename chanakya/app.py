from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from chanakya.chat_service import ChatService
from chanakya.config import get_data_dir, load_local_env
from chanakya.heartbeat import read_heartbeat
from chanakya.maf_runtime import MAFRuntime
from chanakya.models import make_id
from chanakya.seed import load_agent_seeds
from chanakya.store import ChanakyaStore

BASE_DIR = Path(__file__).resolve().parents[1]


def create_app() -> Flask:
    load_local_env()
    app = Flask(__name__, template_folder=str(BASE_DIR / "chanakya" / "templates"))

    data_dir = get_data_dir()
    db_path = data_dir / "chanakya.db"
    heartbeat_dir = data_dir / "heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)

    store = ChanakyaStore(db_path)
    load_agent_seeds(store, BASE_DIR / "chanakya" / "seeds" / "agents.json")
    ensure_heartbeat_files(store, BASE_DIR)

    chanakya_profile = store.get_agent_profile("agent_chanakya")
    runtime = MAFRuntime(chanakya_profile)
    chat_service = ChatService(store, runtime)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/chat")
    def api_chat() -> Any:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or make_id("session"))
        message = str(payload.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        store.ensure_session(session_id, title=message[:60] or "New chat")
        reply = chat_service.chat(session_id, message)
        return jsonify(asdict(reply))

    @app.get("/api/sessions/<session_id>")
    def api_session(session_id: str) -> Any:
        return jsonify({"session_id": session_id, "messages": store.list_messages(session_id)})

    @app.get("/api/events")
    def api_events() -> Any:
        return jsonify({"events": store.list_events(limit=100)})

    @app.get("/api/agents")
    def api_agents() -> Any:
        agents = []
        for profile in store.list_agent_profiles():
            heartbeat = read_heartbeat(profile, BASE_DIR)
            agents.append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "role": profile.role,
                    "personality": profile.personality,
                    "tool_ids": profile.tool_ids,
                    "workspace": profile.workspace,
                    "heartbeat_enabled": profile.heartbeat_enabled,
                    "heartbeat_interval_seconds": profile.heartbeat_interval_seconds,
                    "heartbeat_file_path": profile.heartbeat_file_path,
                    "heartbeat_preview": heartbeat.content_preview,
                    "is_active": profile.is_active,
                }
            )
        return jsonify({"agents": agents})

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


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
