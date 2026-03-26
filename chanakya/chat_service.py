from __future__ import annotations

from chanakya.maf_runtime import MAFRuntime
from chanakya.models import ChatReply, make_id
from chanakya.store import ChanakyaStore


class ChatService:
    def __init__(self, store: ChanakyaStore, runtime: MAFRuntime) -> None:
        self.store = store
        self.runtime = runtime

    def chat(self, session_id: str, message: str) -> ChatReply:
        request_id = make_id("req")
        route = "direct"
        runtime_meta = self.runtime.runtime_metadata()

        self.store.add_message(
            session_id=session_id,
            role="user",
            content=message,
            request_id=request_id,
            route=route,
            metadata={"route": route},
        )
        self.store.log_event(
            "route_decision",
            {
                "request_id": request_id,
                "session_id": session_id,
                "route": route,
                "message": message,
            },
        )

        response_text = self.runtime.run_direct(message)
        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            route=route,
            message=response_text,
            model=(
                runtime_meta.get("model") if isinstance(runtime_meta.get("model"), str) else None
            ),
            endpoint=(
                runtime_meta.get("endpoint")
                if isinstance(runtime_meta.get("endpoint"), str)
                else None
            ),
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
        )
        self.store.add_message(
            session_id=session_id,
            role="assistant",
            content=reply.message,
            request_id=request_id,
            route=route,
            metadata={
                "agent_name": reply.agent_name,
                "runtime": reply.runtime,
                "model": reply.model,
                "endpoint": reply.endpoint,
            },
        )
        self.store.log_event(
            "chat_response",
            {
                "request_id": request_id,
                "session_id": session_id,
                "route": route,
                "runtime": reply.runtime,
                "agent_name": reply.agent_name,
                "model": reply.model,
                "endpoint": reply.endpoint,
            },
        )
        return reply
