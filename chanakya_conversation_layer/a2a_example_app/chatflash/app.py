from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import get_settings
from .maf_chat import MAFChatService
from .models import MessageRecord
from .store import SessionStore


class CreateSessionBody(BaseModel):
    agent_id: str = Field(default="opencode:build")


class SendMessageBody(BaseModel):
    content: str
    agent_id: str


def serialize_message(message: MessageRecord) -> dict[str, str]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
    }


def serialize_dataclass(instance: object) -> dict[str, object]:
    return asdict(instance)


def create_app() -> FastAPI:
    settings = get_settings()
    store = SessionStore(settings.database_path)
    maf_chat = MAFChatService(settings)

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.store = store
    app.state.maf_chat = maf_chat

    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/agents")
    async def list_agents() -> list[dict[str, object]]:
        return [serialize_dataclass(agent) for agent in await maf_chat.list_agents()]

    @app.post("/api/sessions")
    async def create_session(body: CreateSessionBody) -> dict[str, object]:
        agent_ids = {agent.id for agent in await maf_chat.list_agents()}
        if body.agent_id not in agent_ids:
            raise HTTPException(
                status_code=404, detail=f"Unknown agent: {body.agent_id}"
            )
        session = store.create_session(body.agent_id)
        return {
            "session": serialize_dataclass(session),
            "messages": [],
        }

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, object]:
        try:
            session = store.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        messages = [serialize_message(item) for item in store.get_messages(session_id)]
        return {"session": serialize_dataclass(session), "messages": messages}

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, body: SendMessageBody) -> dict[str, object]:
        text = body.content.strip()
        if not text:
            raise HTTPException(
                status_code=400, detail="Message content cannot be empty."
            )

        try:
            session = store.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        agent_ids = {agent.id for agent in await maf_chat.list_agents()}
        if body.agent_id not in agent_ids:
            raise HTTPException(
                status_code=404, detail=f"Unknown agent: {body.agent_id}"
            )
        history = store.get_messages(session_id)
        if session.agent_id != body.agent_id:
            session = store.update_session_agent(session_id, body.agent_id)

        user_message = store.add_message(session_id, "user", text)
        store.maybe_set_title_from_message(session_id, text)

        try:
            assistant_text, remote_context_id = await maf_chat.send_message(
                agent_id=body.agent_id,
                session_id=session_id,
                history=history,
                user_text=text,
                remote_context_id=session.remote_context_id,
            )
            if remote_context_id != session.remote_context_id:
                session = store.set_remote_context(session_id, remote_context_id)
        except Exception as exc:
            error_text = f"Chat error: {exc}"
            assistant_message = store.add_message(session_id, "assistant", error_text)
            session = store.get_session(session_id)
            return {
                "session": serialize_dataclass(session),
                "user_message": serialize_message(user_message),
                "assistant_message": serialize_message(assistant_message),
                "error": str(exc),
            }

        assistant_message = store.add_message(session_id, "assistant", assistant_text)
        session = store.get_session(session_id)
        return {
            "session": serialize_dataclass(session),
            "user_message": serialize_message(user_message),
            "assistant_message": serialize_message(assistant_message),
            "error": None,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        sessions = store.list_sessions()
        agents = await maf_chat.list_agents()
        default_agent_id = agents[0].id if agents else "opencode:build"
        selected = sessions[0] if sessions else store.create_session(default_agent_id)
        messages = store.get_messages(selected.id)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "app_name": settings.app_name,
                "sessions": [
                    serialize_dataclass(session) for session in store.list_sessions()
                ],
                "selected_session": serialize_dataclass(selected),
                "messages": [serialize_message(item) for item in messages],
                "agents": [serialize_dataclass(agent) for agent in agents],
                "opencode_a2a_url": settings.opencode_a2a_url,
                "active_backend": maf_chat.active.backend,
            },
        )

    @app.exception_handler(404)
    async def not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app
