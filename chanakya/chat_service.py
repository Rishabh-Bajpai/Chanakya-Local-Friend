from __future__ import annotations

from chanakya.debug import debug_log
from chanakya.domain import ChatReply, make_id
from chanakya.maf_runtime import MAFRuntime
from chanakya.store import ChanakyaStore


class ChatService:
    def __init__(self, store: ChanakyaStore, runtime: MAFRuntime) -> None:
        self.store = store
        self.runtime = runtime

    def chat(self, session_id: str, message: str) -> ChatReply:
        request_id = make_id("req")
        route = "direct"
        runtime_meta = self.runtime.runtime_metadata()
        prior_messages = self.store.list_messages(session_id)[-8:]
        prompt = self._build_prompt(message, prior_messages)

        debug_log(
            "chat_service_input",
            {
                "session_id": session_id,
                "request_id": request_id,
                "route": route,
                "message": message,
                "prior_message_count": len(prior_messages),
                "history": prior_messages,
                "prompt": prompt,
                "runtime_meta": runtime_meta,
            },
        )

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

        response_text = self.runtime.run_chat(session_id, prompt)
        debug_log(
            "chat_service_model_response",
            {
                "session_id": session_id,
                "request_id": request_id,
                "response": response_text,
            },
        )
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
        debug_log(
            "chat_service_persisted",
            {
                "session_id": session_id,
                "request_id": request_id,
                "stored_user_and_assistant_messages": True,
            },
        )
        return reply

    @staticmethod
    def _build_prompt(message: str, prior_messages: list[dict[str, object]]) -> str:
        if not prior_messages:
            return message

        transcript_lines = [
            (
                "Use the recent conversation to resolve references like "
                "'it', 'that', or follow-up conversation, otherwise answer based on the message."
            ),
            "Recent conversation:",
        ]
        for item in prior_messages:
            role = str(item.get("role", "user")).capitalize()
            content = str(item.get("content", "")).strip()
            if content:
                transcript_lines.append(f"{role}: {content}")
        transcript_lines.extend(
            [
                "",
                f"User: {message}",
                "Assistant:",
            ]
        )
        return "\n".join(transcript_lines)
