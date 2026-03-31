from __future__ import annotations

from chanakya.debug import debug_log
from chanakya.domain import (
    ChatReply,
    REQUEST_STATUS_COMPLETED,
    REQUEST_STATUS_CREATED,
    REQUEST_STATUS_FAILED,
    REQUEST_STATUS_IN_PROGRESS,
    TASK_STATUS_CREATED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    make_id,
    now_iso,
)
from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.store import ChanakyaStore


class ChatService:
    def __init__(
        self,
        store: ChanakyaStore,
        runtime: MAFRuntime,
        manager: AgentManager | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.manager = manager

    def chat(self, session_id: str, message: str) -> ChatReply:
        request_id = make_id("req")
        root_task_id = make_id("task")
        runtime_meta = self.runtime.runtime_metadata()
        prior_messages = self.store.list_messages(session_id)[-8:]
        self.store.add_message(session_id, "user", message, request_id=request_id)
        self.store.create_request(
            request_id=request_id,
            session_id=session_id,
            user_message=message,
            status=REQUEST_STATUS_CREATED,
            root_task_id=root_task_id,
        )
        self.store.create_task(
            task_id=root_task_id,
            request_id=request_id,
            parent_task_id=None,
            title=message[:80] or "User request",
            summary=message,
            status=TASK_STATUS_CREATED,
            owner_agent_id=self.runtime.profile.id,
            task_type="chat_request",
            input_json={"message": message},
        )

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
                "root_task_id": root_task_id,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            event_type="request_received",
            task_id=root_task_id,
            payload={
                "message": message,
                "request_status": REQUEST_STATUS_CREATED,
                "task_status": TASK_STATUS_CREATED,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_created",
            payload={
                "title": message[:80] or "User request",
                "owner_agent_id": self.runtime.profile.id,
                "task_type": "chat_request",
            },
        )
        self.store.update_request(request_id, status=REQUEST_STATUS_IN_PROGRESS)
        started_at = now_iso()
        self.store.update_task(
            root_task_id,
            status=TASK_STATUS_IN_PROGRESS,
            started_at=started_at,
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_CREATED,
                "to_status": TASK_STATUS_IN_PROGRESS,
                "request_status": REQUEST_STATUS_IN_PROGRESS,
                "started_at": started_at,
            },
        )

        should_delegate = (
            self.manager.should_delegate(message) if self.manager is not None else False
        )

        try:
            if should_delegate and self.manager is not None:
                manager_result = self.manager.execute(
                    session_id=session_id,
                    request_id=request_id,
                    root_task_id=root_task_id,
                    message=message,
                )
                run_result = None
            else:
                manager_result = None
                run_result = self.runtime.run(
                    session_id,
                    message,
                    request_id=request_id,
                )
        except Exception as exc:
            finished_at = now_iso()
            self.store.update_request(request_id, status=REQUEST_STATUS_FAILED)
            self.store.update_task(
                root_task_id,
                status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="task_status_changed",
                payload={
                    "from_status": TASK_STATUS_IN_PROGRESS,
                    "to_status": TASK_STATUS_FAILED,
                    "request_status": REQUEST_STATUS_FAILED,
                    "error": str(exc),
                    "finished_at": finished_at,
                },
            )
            self.store.log_event(
                "chat_response_failed",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "root_task_id": root_task_id,
                    "error": str(exc),
                },
            )
            raise

        if run_result is not None:
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
        if run_result is not None:
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
                self.store.create_task_event(
                    session_id=session_id,
                    request_id=request_id,
                    task_id=root_task_id,
                    event_type="tool_trace_recorded",
                    payload={
                        "invocation_id": invocation_id,
                        "tool_id": trace.tool_id,
                        "tool_name": trace.tool_name,
                        "server_name": trace.server_name,
                        "status": trace.status,
                    },
                )

        route = "delegated_manager" if manager_result is not None else run_result.response_mode
        finished_at = now_iso()
        final_message = manager_result.text if manager_result is not None else run_result.text
        response_mode = (
            manager_result.workflow_type if manager_result is not None else run_result.response_mode
        )
        task_status = manager_result.task_status if manager_result is not None else TASK_STATUS_DONE
        self.store.add_message(
            session_id,
            "assistant",
            final_message,
            request_id=request_id,
            route=route,
            metadata={
                "runtime": "maf_agent",
                "response_mode": response_mode,
                "tool_calls_used": len(run_result.tool_traces) if run_result is not None else 0,
                "root_task_id": root_task_id,
                "request_status": REQUEST_STATUS_COMPLETED,
                "task_status": task_status,
                "workflow_type": manager_result.workflow_type
                if manager_result is not None
                else None,
                "child_task_ids": manager_result.child_task_ids
                if manager_result is not None
                else [],
            },
        )
        self.store.update_request(
            request_id,
            status=REQUEST_STATUS_COMPLETED,
            route=route,
        )
        self.store.update_task(
            root_task_id,
            status=task_status,
            result_json=(
                manager_result.result_json
                if manager_result is not None
                else {
                    "message": run_result.text,
                    "response_mode": run_result.response_mode,
                    "tool_calls_used": len(run_result.tool_traces),
                }
            ),
            finished_at=finished_at,
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="response_persisted",
            payload={
                "route": route,
                "response_mode": response_mode,
                "tool_calls_used": len(run_result.tool_traces) if run_result is not None else 0,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_IN_PROGRESS,
                "to_status": task_status,
                "request_status": REQUEST_STATUS_COMPLETED,
                "finished_at": finished_at,
            },
        )

        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            route=route,
            message=final_message,
            request_status=REQUEST_STATUS_COMPLETED,
            root_task_id=root_task_id,
            root_task_status=task_status,
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
            response_mode=response_mode,
            tool_calls_used=len(run_result.tool_traces) if run_result is not None else 0,
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
                "response_mode": response_mode,
                "tool_calls_used": len(run_result.tool_traces) if run_result is not None else 0,
                "root_task_id": root_task_id,
                "request_status": REQUEST_STATUS_COMPLETED,
                "task_status": task_status,
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
