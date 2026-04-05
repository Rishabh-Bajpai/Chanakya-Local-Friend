from __future__ import annotations

import asyncio

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatClient

from chanakya.agent.runtime import build_profile_agent
from chanakya.config import get_agent_request_timeout_seconds
from chanakya.debug import debug_log
from chanakya.domain import (
    ChatReply,
    REQUEST_STATUS_CANCELLED,
    REQUEST_STATUS_COMPLETED,
    REQUEST_STATUS_CREATED,
    REQUEST_STATUS_FAILED,
    REQUEST_STATUS_IN_PROGRESS,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CREATED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_WAITING_INPUT,
    make_id,
    now_iso,
)
from chanakya.agent.runtime import MAFRuntime
from chanakya.agent_manager import AgentManager
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import ChanakyaStore


_TRIAGE_SYSTEM_PROMPT = (
    "You are a request classifier. Your ONLY job is to output exactly one word.\n"
    "Reply with \"direct\" if the user's message is:\n"
    "- A greeting, small talk, or simple conversational exchange\n"
    "- A simple factual question answerable from general knowledge\n"
    "- A math calculation or unit conversion\n"
    "- A weather check or current conditions lookup\n"
    "- A request to fetch or summarise a single URL or web page\n"
    "- Anything a single assistant with a calculator and web-fetch tool can fully answer in one step\n"
    "\n"
    "Reply with \"delegate\" if the user's message requires:\n"
    "- Multi-step research across multiple sources\n"
    "- Software development, code generation, architecture, or debugging\n"
    "- Complex analysis requiring structured specialist workflows\n"
    "- Coordination between multiple specialists (researcher+writer, developer+tester)\n"
    "\n"
    "Output ONLY the single word \"direct\" or \"delegate\". Nothing else."
)


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
        self._triage_client = OpenAIChatClient(env_file_path=".env")

    def _triage_message(self, message: str) -> str:
        """Lightweight LLM call to classify a message as 'direct' or 'delegate'."""
        try:
            triage_agent = Agent(
                client=self._triage_client,
                name="triage_classifier",
                instructions=_TRIAGE_SYSTEM_PROMPT,
            )

            async def _classify() -> str:
                response = await asyncio.wait_for(
                    triage_agent.run(
                        Message(role="user", text=message),
                        options={"store": False},
                    ),
                    timeout=15,
                )
                return str(response).strip().lower()

            raw = run_in_maf_loop(_classify())
            decision = "direct" if "direct" in raw else "delegate"
            debug_log(
                "triage_decision",
                {"message": message, "raw_response": raw, "decision": decision},
            )
            return decision
        except Exception as exc:
            debug_log(
                "triage_fallback",
                {"message": message, "error": str(exc), "decision": "delegate"},
            )
            return "delegate"

    def chat(self, session_id: str, message: str, *, work_id: str | None = None) -> ChatReply:
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

        try:
            use_manager = False
            if self.manager is not None:
                triage = self._triage_message(message)
                use_manager = triage == "delegate"
                self.store.create_task_event(
                    session_id=session_id,
                    request_id=request_id,
                    task_id=root_task_id,
                    event_type="triage_completed",
                    payload={
                        "decision": triage,
                        "use_manager": use_manager,
                    },
                )

            if use_manager:
                context_tokens = self.manager.bind_execution_context(
                    session_id=session_id,
                    work_id=work_id,
                )
                try:
                    manager_result = self.manager.execute(
                        session_id=session_id,
                        request_id=request_id,
                        root_task_id=root_task_id,
                        message=message,
                    )
                finally:
                    self.manager.reset_execution_context(context_tokens)
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

        if manager_result is not None:
            route = "delegated_manager"
            final_message = manager_result.text
            response_mode = manager_result.workflow_type
            task_status = manager_result.task_status
            direct_tool_calls_used = 0
            result_json = manager_result.result_json
            waiting_task_id = manager_result.waiting_task_id
            input_prompt = manager_result.input_prompt
        else:
            assert run_result is not None
            direct_run_result = run_result
            route = direct_run_result.response_mode
            final_message = direct_run_result.text
            response_mode = direct_run_result.response_mode
            task_status = TASK_STATUS_DONE
            direct_tool_calls_used = len(direct_run_result.tool_traces)
            result_json = {
                "message": direct_run_result.text,
                "response_mode": direct_run_result.response_mode,
                "tool_calls_used": len(direct_run_result.tool_traces),
            }
            waiting_task_id = None
            input_prompt = None
        finished_at = None if task_status == TASK_STATUS_WAITING_INPUT else now_iso()
        request_status = self._request_status_from_task_status(task_status)
        if task_status != TASK_STATUS_WAITING_INPUT:
            self.store.add_message(
                session_id,
                "assistant",
                final_message,
                request_id=request_id,
                route=route,
                metadata={
                    "runtime": "maf_agent",
                    "response_mode": response_mode,
                    "tool_calls_used": direct_tool_calls_used,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": task_status,
                    "workflow_type": manager_result.workflow_type
                    if manager_result is not None
                    else None,
                    "child_task_ids": manager_result.child_task_ids
                    if manager_result is not None
                    else [],
                    "waiting_task_id": waiting_task_id,
                    "input_prompt": input_prompt,
                },
            )
        self.store.update_request(
            request_id,
            status=request_status,
            route=route,
        )
        self.store.update_task(
            root_task_id,
            status=task_status,
            result_json=result_json,
            finished_at=finished_at,
        )
        if task_status != TASK_STATUS_WAITING_INPUT:
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="response_persisted",
                payload={
                    "route": route,
                    "response_mode": response_mode,
                    "tool_calls_used": direct_tool_calls_used,
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
                "request_status": request_status,
                "finished_at": finished_at,
            },
        )

        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            work_id=work_id,
            route=route,
            message=final_message,
            request_status=request_status,
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
            tool_calls_used=direct_tool_calls_used,
            tool_trace_ids=tool_trace_ids,
            requires_input=task_status == TASK_STATUS_WAITING_INPUT,
            waiting_task_id=waiting_task_id,
            input_prompt=input_prompt,
        )
        self.store.log_event(
            "chat_response",
            {
                "request_id": request_id,
                "session_id": session_id,
                "work_id": work_id,
                "route": route,
                "runtime": reply.runtime,
                "agent_name": reply.agent_name,
                "model": reply.model,
                "endpoint": reply.endpoint,
                "response_mode": response_mode,
                "tool_calls_used": direct_tool_calls_used,
                "root_task_id": root_task_id,
                "request_status": request_status,
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

    def submit_task_input(self, task_id: str, message: str) -> ChatReply:
        if self.manager is None:
            raise RuntimeError("Task input submission requires an active manager")
        task = self.store.get_task(task_id)
        if task.status != TASK_STATUS_WAITING_INPUT:
            raise ValueError("Task is not currently waiting for input")
        if not (task.input_json or {}).get("maf_pending_request_id"):
            raise ValueError("Only the blocked worker task can accept user input")
        request = self.store.get_request(task.request_id)
        session_id = request.session_id
        work_id = self.store.find_work_id_by_session(
            agent_id=self.runtime.profile.id,
            session_id=session_id,
        )
        runtime_meta = self.runtime.runtime_metadata()
        root_task_id = request.root_task_id
        if root_task_id is None:
            raise RuntimeError("Waiting task request is missing a root task")
        root_task = self.store.get_task(root_task_id)
        if root_task.status == TASK_STATUS_WAITING_INPUT:
            resumed_at = now_iso()
            self.store.update_task(root_task_id, status=TASK_STATUS_IN_PROGRESS, finished_at=None)
            self.store.update_request(request.id, status=REQUEST_STATUS_IN_PROGRESS)
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="task_status_changed",
                payload={
                    "from_status": TASK_STATUS_WAITING_INPUT,
                    "to_status": TASK_STATUS_IN_PROGRESS,
                    "request_status": REQUEST_STATUS_IN_PROGRESS,
                    "started_at": resumed_at,
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="task_resumed",
                payload={
                    "from_status": TASK_STATUS_WAITING_INPUT,
                    "to_status": TASK_STATUS_IN_PROGRESS,
                },
            )
        self.store.add_message(
            session_id,
            "user",
            message,
            request_id=request.id,
            metadata={"input_target_task_id": task_id, "input_submission": True},
        )
        context_tokens = self.manager.bind_execution_context(
            session_id=session_id,
            work_id=work_id,
        )
        try:
            result = self.manager.resume_waiting_input(
                session_id=session_id,
                task_id=task_id,
                message=message,
            )
        finally:
            self.manager.reset_execution_context(context_tokens)
        request_status = self._request_status_from_task_status(result.task_status)
        finished_at = None if result.task_status == TASK_STATUS_WAITING_INPUT else now_iso()
        if result.task_status != TASK_STATUS_WAITING_INPUT:
            self.store.add_message(
                session_id,
                "assistant",
                result.text,
                request_id=request.id,
                route="delegated_manager",
                metadata={
                    "runtime": "maf_agent",
                    "response_mode": result.workflow_type,
                    "tool_calls_used": 0,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": result.task_status,
                    "workflow_type": result.workflow_type,
                    "child_task_ids": result.child_task_ids,
                    "waiting_task_id": result.waiting_task_id,
                    "input_prompt": result.input_prompt,
                },
            )
        self.store.update_request(request.id, status=request_status, route="delegated_manager")
        self.store.update_task(
            root_task_id,
            status=result.task_status,
            result_json=result.result_json,
            finished_at=finished_at,
        )
        if result.task_status != TASK_STATUS_WAITING_INPUT:
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="response_persisted",
                payload={
                    "route": "delegated_manager",
                    "response_mode": result.workflow_type,
                    "tool_calls_used": 0,
                },
            )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request.id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_WAITING_INPUT,
                "to_status": result.task_status,
                "request_status": request_status,
                "finished_at": finished_at,
            },
        )
        return ChatReply(
            request_id=request.id,
            session_id=session_id,
            work_id=work_id,
            route="delegated_manager",
            message=result.text,
            request_status=request_status,
            root_task_id=root_task_id,
            root_task_status=result.task_status,
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
            response_mode=result.workflow_type,
            tool_calls_used=0,
            tool_trace_ids=[],
            requires_input=result.task_status == TASK_STATUS_WAITING_INPUT,
            waiting_task_id=result.waiting_task_id,
            input_prompt=result.input_prompt,
        )

    def cancel_task(self, task_id: str) -> dict[str, str]:
        task = self.store.get_task(task_id)
        if task.status in {TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}:
            raise ValueError(f"Cannot cancel task {task_id!r} from status {task.status!r}")
        request = self.store.get_request(task.request_id)
        cancelled_at = now_iso()
        active_statuses = {
            TASK_STATUS_CREATED,
            TASK_STATUS_IN_PROGRESS,
            TASK_STATUS_WAITING_INPUT,
            TASK_STATUS_BLOCKED,
        }
        cancel_ids: list[str] = []
        seen_ids: set[str] = set()

        current_id: str | None = task_id
        while current_id:
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)
            current_task = self.store.get_task(current_id)
            cancel_ids.append(current_id)
            current_id = current_task.parent_task_id

        root_task_id = request.root_task_id
        if root_task_id and root_task_id not in seen_ids:
            cancel_ids.append(root_task_id)

        for cancel_id in cancel_ids:
            cancel_task = self.store.get_task(cancel_id)
            if cancel_task.status not in active_statuses:
                continue
            self.store.update_task(
                cancel_id,
                status=TASK_STATUS_CANCELLED,
                finished_at=cancelled_at,
            )
            self.store.create_task_event(
                session_id=request.session_id,
                request_id=request.id,
                task_id=cancel_id,
                event_type="task_status_changed",
                payload={
                    "from_status": cancel_task.status,
                    "to_status": TASK_STATUS_CANCELLED,
                    "request_status": REQUEST_STATUS_CANCELLED,
                    "finished_at": cancelled_at,
                },
            )
            self.store.create_task_event(
                session_id=request.session_id,
                request_id=request.id,
                task_id=cancel_id,
                event_type="task_cancelled",
                payload={
                    "task_id": cancel_id,
                    "scope": "direct" if cancel_id == task_id else "cascade",
                },
            )
        self.store.update_request(request.id, status=REQUEST_STATUS_CANCELLED)
        if self.manager is not None:
            self.manager.cancel_waiting_task(task_id)
        return {"task_id": task_id, "status": TASK_STATUS_CANCELLED}

    def retry_task(self, task_id: str) -> dict[str, str | None]:
        if self.manager is None:
            raise RuntimeError("Retry requires an active manager")
        retry_info = self.manager.retry_task(task_id)
        message = retry_info.get("message", "").strip()
        session_id = retry_info.get("session_id", "").strip()
        if not message or not session_id:
            raise RuntimeError("Retry metadata is incomplete")
        reply = self.chat(session_id, message)
        return {
            "task_id": task_id,
            "status": reply.root_task_status,
            "retry_request_id": reply.request_id,
            "retry_root_task_id": reply.root_task_id,
        }

    def manual_unblock_task(self, task_id: str) -> dict[str, str]:
        task = self.store.get_task(task_id)
        if task.status != TASK_STATUS_BLOCKED:
            raise ValueError(
                f"Cannot manually unblock task {task_id!r} from status {task.status!r}"
            )
        request = self.store.get_request(task.request_id)
        resumed_at = task.started_at or now_iso()
        self.store.update_task(
            task_id,
            status=TASK_STATUS_IN_PROGRESS,
            started_at=resumed_at,
        )
        self.store.create_task_event(
            session_id=request.session_id,
            request_id=request.id,
            task_id=task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_BLOCKED,
                "to_status": TASK_STATUS_IN_PROGRESS,
                "request_status": REQUEST_STATUS_IN_PROGRESS,
                "started_at": resumed_at,
            },
        )
        self.store.create_task_event(
            session_id=request.session_id,
            request_id=request.id,
            task_id=task_id,
            event_type="task_manual_unblocked",
            payload={
                "task_id": task_id,
                "from_status": TASK_STATUS_BLOCKED,
                "to_status": TASK_STATUS_IN_PROGRESS,
            },
        )
        return {"task_id": task_id, "status": TASK_STATUS_IN_PROGRESS}

    @staticmethod
    def _request_status_from_task_status(task_status: str) -> str:
        if task_status == TASK_STATUS_FAILED:
            return REQUEST_STATUS_FAILED
        if task_status == TASK_STATUS_CANCELLED:
            return REQUEST_STATUS_CANCELLED
        if task_status == TASK_STATUS_WAITING_INPUT:
            return REQUEST_STATUS_IN_PROGRESS
        return REQUEST_STATUS_COMPLETED
