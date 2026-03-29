from __future__ import annotations

from chanakya.debug import debug_log
from chanakya.domain import ChatReply, make_id
from chanakya.agent.runtime import MAFRuntime
from chanakya.store import ChanakyaStore


class ChatService:
    def __init__(self, store: ChanakyaStore, runtime: MAFRuntime) -> None:
        self.store = store
        self.runtime = runtime

    def chat(self, session_id: str, message: str) -> ChatReply:
        request_id = make_id("req")
        runtime_meta = self.runtime.runtime_metadata()
        prior_messages = self.store.list_messages(session_id)[-8:]

        debug_log(
            "chat_service_input",
            {
                "session_id": session_id,
                "request_id": request_id,
                "message": message,
                "prior_message_count": len(prior_messages),
                "history": prior_messages,
                "runtime_meta": runtime_meta,
            },
        )
        self.store.log_event(
            "chat_request",
            {
                "request_id": request_id,
                "session_id": session_id,
                "message": message,
            },
        )

        # ---- unified runtime call (agent decides tool usage) ----
        run_result = self.runtime.run(
            session_id,
            message,
            request_id=request_id,
        )

        debug_log(
            "chat_service_model_response",
            {
                "session_id": session_id,
                "request_id": request_id,
                "response": run_result.text,
                "response_mode": run_result.response_mode,
                "tool_trace_count": len(run_result.tool_traces),
            },
        )

        # ---- persist tool invocation traces ----
        tool_trace_ids: list[str] = []
        for trace in run_result.tool_traces:
            invocation_id = make_id("tinv")
            tool_trace_ids.append(invocation_id)
            self.store.create_tool_invocation(
                invocation_id=invocation_id,
                request_id=request_id,
                session_id=session_id,
                agent_id=self.runtime.profile.id,
                agent_name=self.runtime.profile.name,
                tool_id=trace.tool_id,
                tool_name=trace.tool_name,
                server_name=trace.server_name,
                status=trace.status,
                input_json={"raw": trace.input_payload} if trace.input_payload else {},
            )
            self.store.finish_tool_invocation(
                invocation_id,
                status=trace.status,
                output_text=trace.output_text,
                error_text=trace.error_text,
            )

        route = run_result.response_mode  # "direct_answer" or "tool_assisted"

        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            route=route,
            message=run_result.text,
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
            response_mode=run_result.response_mode,
            tool_calls_used=len(run_result.tool_traces),
            tool_trace_ids=tool_trace_ids,
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
                "response_mode": run_result.response_mode,
                "tool_calls_used": len(run_result.tool_traces),
            },
        )
        debug_log(
            "chat_service_persisted",
            {
                "session_id": session_id,
                "request_id": request_id,
                "stored_user_and_assistant_messages": True,
                "tool_trace_ids": tool_trace_ids,
            },
        )
        return reply
