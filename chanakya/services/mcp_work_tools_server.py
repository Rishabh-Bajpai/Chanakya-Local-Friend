"""MCP server exposing work-management tools for classic chat.

Tools let the Chanakya assistant list works, check status,
send messages into work sessions, and read pending notifications
— all without automatic delegation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.chat_service import ChatService
from chanakya.config import get_database_url
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import make_id
from chanakya.seed import load_agent_seeds
from chanakya.store import ChanakyaStore


def _build_store() -> tuple[ChanakyaStore, sessionmaker[Session]]:
    engine = build_engine(get_database_url())
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory), session_factory


def _ensure_agent_profiles(store: ChanakyaStore) -> None:
    try:
        store.get_agent_profile("agent_chanakya")
        store.get_agent_profile("agent_manager")
        return
    except KeyError:
        repo_root = Path(__file__).resolve().parents[2]
        load_agent_seeds(store, repo_root / "chanakya" / "seeds" / "agents.json")


def _build_chat_service(
    store: ChanakyaStore, session_factory: sessionmaker[Session]
) -> ChatService:
    _ensure_agent_profiles(store)
    chanakya_profile = store.get_agent_profile("agent_chanakya")
    manager_profile = store.get_agent_profile("agent_manager")
    runtime = MAFRuntime(chanakya_profile, session_factory)
    manager = AgentManager(store, session_factory, manager_profile)
    return ChatService(store, runtime, manager)


def _send_message_to_work(
    store: ChanakyaStore,
    chat_service: Any,
    *,
    work_id: str,
    message: str,
) -> dict[str, Any]:
    cleaned_message = message.strip()
    if not cleaned_message:
        return {"ok": False, "error": "message is required"}

    try:
        work = store.get_work(work_id)
    except KeyError:
        return {"ok": False, "error": f"Work not found: {work_id}"}

    session_id = store.ensure_work_agent_session(
        work_id=work_id,
        agent_id="agent_chanakya",
        session_id=make_id("session"),
        session_title=f"{work.title} - Chanakya",
    )
    reply = chat_service.chat(session_id, cleaned_message, work_id=work_id)
    return {
        "ok": True,
        "work_id": work_id,
        "session_id": session_id,
        "request_id": reply.request_id,
        "root_task_id": reply.root_task_id,
        "task_status": reply.root_task_status,
        "requires_input": reply.requires_input,
        "input_prompt": reply.input_prompt,
        "message": "Message delivered to work and processing started.",
        "result_preview": reply.message,
    }


def _build_work_tools_server() -> FastMCP:
    mcp = FastMCP("Chanakya Work Tools", json_response=True)
    store, session_factory = _build_store()
    chat_service = _build_chat_service(store, session_factory)

    @mcp.tool()
    def list_works(limit: int = 20) -> dict[str, Any]:
        """List all work items with their current status.

        Returns a list of works sorted by creation time (newest first).
        Each work includes id, title, description, status, and timestamps.
        """
        bounded = max(1, min(limit, 100))
        works = store.list_works(limit=bounded)
        return {"ok": True, "works": works, "count": len(works)}

    @mcp.tool()
    def get_work_status(work_id: str) -> dict[str, Any]:
        """Get detailed status of a specific work item.

        Returns the work metadata, associated agent sessions,
        and recent tasks with their statuses.
        """
        try:
            work = store.get_work(work_id)
        except KeyError:
            return {"ok": False, "error": f"Work not found: {work_id}"}

        sessions = store.list_work_agent_sessions(work_id)
        session_ids = store.list_session_ids_for_work(work_id)

        tasks: list[dict[str, Any]] = []
        for sid in session_ids:
            tasks.extend(store.list_tasks(session_id=sid, limit=20))

        task_summary = [
            {
                "id": t["id"],
                "title": t.get("title", ""),
                "status": t.get("status", ""),
                "type": t.get("task_type", ""),
            }
            for t in tasks
        ]

        return {
            "ok": True,
            "work": {
                "id": work.id,
                "title": work.title,
                "description": work.description,
                "status": work.status,
                "created_at": work.created_at,
                "updated_at": work.updated_at,
            },
            "agent_sessions": sessions,
            "tasks": task_summary,
        }

    @mcp.tool()
    def send_message_to_work(work_id: str, message: str) -> dict[str, Any]:
        """Send a message into an existing work's chat session.

        The message is appended to the Chanakya agent session for
        this work. The work's agent will process it on the next run.

        Use this when the user explicitly asks to communicate with
        an ongoing work item from classic chat.
        """
        return _send_message_to_work(
            store,
            chat_service,
            work_id=work_id,
            message=message,
        )

    @mcp.tool()
    def get_pending_work_messages(
        work_id: str = "",
        include_acknowledged: bool = False,
    ) -> dict[str, Any]:
        """Get pending notification messages from work items.

        Returns unacknowledged work notifications (completed tasks,
        input-required prompts, failures). Optionally filter by work_id.
        """
        notifications = store.work_notifications.list_pending(
            work_id=work_id if work_id.strip() else None,
            include_acknowledged=include_acknowledged,
        )
        return {
            "ok": True,
            "notifications": notifications,
            "count": len(notifications),
        }

    return mcp


def main() -> None:
    mcp = _build_work_tools_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
