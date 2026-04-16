from __future__ import annotations

import json
import threading
from typing import Any

from chanakya.agent.runtime import normalize_runtime_backend
from chanakya.config import get_agent_request_timeout_seconds
from chanakya.conversation_layer_support import ConversationLayerResult, ConversationLayerSupport
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
from chanakya.model import AgentProfileModel
from chanakya.services.sandbox_workspace import delete_shared_workspace, resolve_shared_workspace
from chanakya.services.ntfy import NtfyNotificationDispatcher, summarize_notification_text
from chanakya.store import ChanakyaStore


_NORMAL_CHAT_DELEGATION_NOTICE = "Transferring your work to an expert. This may take a bit longer."
_WAITING_INPUT_ROUTE = "waiting_input_prompt"
_CLASSIC_ACTIVE_WORK_PREFIX = "cwork"
_CLASSIC_WORK_COMPLETION_ROUTE = "classic_work_completion"
_CLASSIC_CHAT_RUNTIME_PROMPT_ADDENDUM = (
    "Optimize for speed and direct completion. "
    "Handle as much work yourself as possible using your own available tools, and give concise "
    "direct answers when you can finish the task reliably. Delegate only when you can't do it using tools or without using tools or the request is "
    "clearly complex, long-running, multi-step, or specialist-heavy."
    "Note: delegating the work is takes time so it should be avoided unless it is really necessary."
)
_WORK_MODE_RUNTIME_PROMPT_ADDENDUM = (
    "Optimize for deliberate accuracy and completeness over speed. "
    "Trivial requests can still be handled directly, but for non-trivial work prefer specialist "
    "coordination and gather downstream inputs before presenting the final answer."
)
_WAITING_INPUT_CANCEL_MARKERS = (
    "never mind",
    "nevermind",
    "don't do anything",
    "do not do anything",
    "stop",
    "cancel",
    "forget about it",
    "forgot about it",
    "leave it",
    "ignore it",
)
_CLASSIC_ROUTER_MIN_DELEGATION_CONFIDENCE = 0.25
_CLASSIC_ROUTER_HISTORY_WINDOW = 12
_CLASSIC_ROUTER_SYSTEM_PROMPT = (
    "You are a routing planner for Chanakya classic chat. "
    "Given a user message, recent chat history, active work context, and Chanakya's direct capabilities, "
    "decide exactly ONE action.\n\n"
    "Output valid JSON ONLY with this schema:\n"
    '{"action":"direct|continue_active_work|create_new_work","confidence":0.0-1.0,"reason":"...","handoff_message":"..."}\n\n'
    "## Actions\n\n"
    "### direct\n"
    "Handle the message yourself in a single turn. Choose this for:\n"
    "- Greetings, small talk, thanks, social niceties\n"
    '- Simple factual questions ("What is the capital of France?")\n'
    '- Short math/arithmetic ("What is 7400 times 57?")\n'
    "- Quick rewrites, rephrasing, grammar fixes, translations on short text\n"
    "- Jokes, riddles, simple entertainment\n"
    "- Any request that Chanakya can fully answer in one turn with its direct capabilities\n"
    "For direct, set handoff_message to empty string.\n\n"
    "### continue_active_work\n"
    "Route the message to the EXISTING active work session. Choose this when:\n"
    "- The user is continuing, refining, or following up on the active task\n"
    '- The user modifies parameters of the same task type (e.g. "now do it for range 2-300" '
    "after finding primes in range 74-534). CRITICAL: parameter changes to the same fundamental "
    "task are follow-ups, NOT new tasks.\n"
    '- The user refers to active work output ("add a conclusion", "fix that bug", "update the report")\n'
    '- Pronoun references ("it", "this", "that") that clearly refer to the active work\n'
    "- The user asks to revise, extend, or iterate on the active work result\n"
    "For continue_active_work, handoff_message should include enough context for the worker "
    "to understand the follow-up instruction.\n\n"
    "### create_new_work\n"
    "Create a brand-new delegated work session. Choose this when:\n"
    "- The user asks for something FUNDAMENTALLY DIFFERENT from the active task "
    "(different domain, different goal, different deliverable type)\n"
    "- There is no active work and the request requires multi-step specialist work "
    "(software, research, reports, complex analysis)\n"
    '- The user explicitly says "new task", "something else", "different task"\n'
    "- The user explicitly asks to delegate, hand off, or involve Agent Manager\n"
    "For create_new_work, handoff_message MUST be fully self-contained: the receiving agent "
    "has NO knowledge of prior conversation. Include all relevant details, context, "
    "and requirements from the chat history.\n\n"
    "## Critical Rules\n"
    "- If active work exists and the user's request is a variation/continuation of that work, "
    "ALWAYS choose continue_active_work, NOT create_new_work.\n"
    "- If a request involves software/code generation, implementation, debugging, testing, "
    "multi-step research, or specialist coordination, do NOT choose direct.\n"
    "- If the user explicitly asks to delegate or hand off, NEVER choose direct.\n"
    "- confidence must be a number from 0.0 to 1.0.\n"
    "- No markdown, no code fences, no extra keys."
)


class ChatService:
    def __init__(
        self,
        store: ChanakyaStore,
        runtime: MAFRuntime,
        manager: AgentManager | None = None,
        notification_dispatcher: NtfyNotificationDispatcher | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.manager = manager
        self.notification_dispatcher = notification_dispatcher
        self.classic_async_enabled = isinstance(runtime, MAFRuntime)
        self.classic_router_runner: Any | None = None
        self.classic_background_launcher: Any | None = None
        self._classic_router_runtime: MAFRuntime | None = None
        self._classic_background_lock = threading.Lock()
        self._classic_background_work_ids: set[str] = set()
        session_factory = getattr(runtime, "session_factory", None)
        if session_factory is not None:
            router_profile = AgentProfileModel(
                id="agent_classic_router",
                name="Classic Router",
                role="router",
                system_prompt=_CLASSIC_ROUTER_SYSTEM_PROMPT,
                personality="deterministic",
                tool_ids_json=[],
                workspace=None,
                heartbeat_enabled=False,
                heartbeat_interval_seconds=300,
                heartbeat_file_path=None,
                is_active=True,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            self._classic_router_runtime = MAFRuntime(router_profile, session_factory)
        self._conversation_layer = ConversationLayerSupport()

    def _launch_background_call(self, target: Any, *args: Any, **kwargs: Any) -> None:
        launcher = self.classic_background_launcher
        if launcher is not None:
            launcher(target, *args, **kwargs)
            return
        if not self.classic_async_enabled:
            target(*args, **kwargs)
            return
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()

    def _mark_classic_work_running(self, work_id: str) -> None:
        with self._classic_background_lock:
            self._classic_background_work_ids.add(work_id)

    def _mark_classic_work_finished(self, work_id: str) -> None:
        with self._classic_background_lock:
            self._classic_background_work_ids.discard(work_id)

    def _classic_work_running(self, work_id: str | None) -> bool:
        if not work_id:
            return False
        with self._classic_background_lock:
            return work_id in self._classic_background_work_ids

    @staticmethod
    def _runtime_snapshot_from_metadata(runtime_meta: dict[str, object]) -> dict[str, str | None]:
        backend = normalize_runtime_backend(runtime_meta.get("backend"))
        return {
            "backend": backend,
            "model_id": str(runtime_meta.get("model") or "").strip() or None,
            "a2a_url": str(runtime_meta.get("endpoint") or "").strip() or None
            if backend == "a2a"
            else None,
            "a2a_remote_agent": str(runtime_meta.get("a2a_remote_agent") or "").strip() or None,
            "a2a_model_provider": str(runtime_meta.get("a2a_model_provider") or "").strip() or None,
            "a2a_model_id": str(runtime_meta.get("a2a_model_id") or "").strip() or None,
        }

    @staticmethod
    def _runtime_snapshot_from_task_input(
        input_json: dict[str, object] | None,
    ) -> dict[str, str | None]:
        payload = dict((input_json or {}).get("runtime_config") or {})
        backend = normalize_runtime_backend(payload.get("backend"))
        return {
            "backend": backend,
            "model_id": str(payload.get("model_id") or "").strip() or None,
            "a2a_url": str(payload.get("a2a_url") or "").strip() or None,
            "a2a_remote_agent": str(payload.get("a2a_remote_agent") or "").strip() or None,
            "a2a_model_provider": str(payload.get("a2a_model_provider") or "").strip() or None,
            "a2a_model_id": str(payload.get("a2a_model_id") or "").strip() or None,
        }

    def _runtime_metadata(self, model_id: str | None = None) -> dict[str, Any]:
        try:
            return self.runtime.runtime_metadata(model_id=model_id)
        except TypeError:
            return self.runtime.runtime_metadata()

    def _runtime_run(
        self,
        session_id: str,
        message: str,
        *,
        request_id: str,
        model_id: str | None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        prompt_addendum: str | None = None,
    ) -> Any:
        try:
            return self.runtime.run(
                session_id,
                message,
                request_id=request_id,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
                prompt_addendum=prompt_addendum,
            )
        except TypeError:
            try:
                return self.runtime.run(
                    session_id,
                    message,
                    request_id=request_id,
                    model_id=model_id,
                )
            except TypeError:
                return self.runtime.run(session_id, message, request_id=request_id)

    @staticmethod
    def _runtime_prompt_addendum_for_mode(*, work_id: str | None) -> str:
        if work_id is not None:
            return _WORK_MODE_RUNTIME_PROMPT_ADDENDUM
        return _CLASSIC_CHAT_RUNTIME_PROMPT_ADDENDUM

    def _notify_root_task_outcome(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        task_status: str,
        work_id: str | None,
        summary: str | None,
    ) -> None:
        if self.notification_dispatcher is None:
            return
        if task_status not in {TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_WAITING_INPUT}:
            return
        normalized_summary = summarize_notification_text(summary or "")
        if not normalized_summary:
            normalized_summary = (
                "A request finished successfully."
                if task_status == TASK_STATUS_DONE
                else "A request failed before producing a summary."
                if task_status == TASK_STATUS_FAILED
                else "Chanakya is waiting for your input to continue."
            )
        self.notification_dispatcher.notify_root_task_outcome(
            session_id=session_id,
            request_id=request_id,
            root_task_id=root_task_id,
            task_status=task_status,
            summary=normalized_summary,
            work_id=work_id,
        )

    def _build_conversation_layer_result(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        model_id: str | None,
        request_id: str | None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> ConversationLayerResult | None:
        if not assistant_message.strip():
            return None
        if not self._conversation_layer.enabled:
            return None
        conversation_runtime = dict(runtime_metadata or {})
        selected_backend = normalize_runtime_backend(
            conversation_runtime.get("core_agent_backend") or conversation_runtime.get("backend")
        )
        selected_model_id = model_id
        if selected_backend == "a2a":
            selected_model_id = (
                str(
                    conversation_runtime.get("a2a_model_id")
                    or conversation_runtime.get("model")
                    or ""
                ).strip()
                or None
            )
        try:
            result = self._conversation_layer.wrap_reply(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                model_id=selected_model_id,
                backend=selected_backend,
                a2a_url=str(
                    conversation_runtime.get("a2a_remote_url")
                    or conversation_runtime.get("endpoint")
                    or ""
                ).strip()
                or None,
                a2a_remote_agent=str(conversation_runtime.get("a2a_remote_agent") or "").strip()
                or None,
                a2a_model_provider=str(conversation_runtime.get("a2a_model_provider") or "").strip()
                or None,
                a2a_model_id=str(conversation_runtime.get("a2a_model_id") or "").strip() or None,
                metadata={
                    **conversation_runtime,
                    "source": "chanakya_conversation_layer",
                },
            )
        except Exception as exc:
            debug_log(
                "conversation_layer_error",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": str(exc),
                },
            )
            return None
        debug_log(
            "conversation_layer_applied",
            {
                "session_id": session_id,
                "request_id": request_id,
                "original_length": len(assistant_message),
                "immediate_message_count": len(result.messages),
                "pending_delivery_count": result.metadata.get("pending_delivery_count", 0),
            },
        )
        return result

    @staticmethod
    def _conversation_message_content(messages: list[dict[str, Any]], fallback: str) -> str:
        if messages:
            return (
                "\n\n".join(
                    str(message.get("text") or "").strip()
                    for message in messages
                    if str(message.get("text") or "").strip()
                ).strip()
                or fallback
            )
        return fallback

    def _persist_conversation_messages(
        self,
        *,
        session_id: str,
        request_id: str | None,
        route: str,
        base_metadata: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        for index, message in enumerate(messages):
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            self.store.add_message(
                session_id,
                "assistant",
                text,
                request_id=request_id,
                route=route,
                metadata={
                    **base_metadata,
                    "conversation_layer_applied": True,
                    "conversation_layer_message_index": index,
                    "conversation_layer_delay_ms": int(message.get("delay_ms") or 0),
                },
            )

    def deliver_next_conversation_message(self, session_id: str) -> dict[str, Any]:
        payload = self._conversation_layer.deliver_next_message(session_id)
        message = payload.get("message")
        if isinstance(message, dict):
            memory = payload.get("working_memory") or {}
            self.store.add_message(
                session_id,
                "assistant",
                str(message.get("text") or ""),
                route="conversation_layer_followup",
                metadata={
                    "conversation_layer_applied": True,
                    "conversation_layer_followup": True,
                    "conversation_layer_delay_ms": int(message.get("delay_ms") or 0),
                    "conversation_layer_pending_delivery_count": len(
                        memory.get("pending_messages") or []
                    ),
                },
            )
        return payload

    def request_manual_pause(self, session_id: str) -> dict[str, Any]:
        return self._conversation_layer.request_manual_pause(session_id)

    def _triage_message(self, message: str, *, work_id: str | None = None) -> str:
        """Classify a message as 'direct' or 'delegate'.

        Used ONLY for /work mode (work_id is not None). In /work mode we always
        delegate when a manager is available — the manager orchestrates specialist
        work inside the work session.
        """
        if self.manager is not None:
            debug_log(
                "triage_heuristic",
                {
                    "message": message,
                    "decision": "delegate",
                    "reason": "work_delegate_manager_available",
                    "work_id": work_id,
                },
            )
            return "delegate"
        debug_log(
            "triage_heuristic",
            {
                "message": message,
                "decision": "direct",
                "reason": "no_manager_available",
                "work_id": work_id,
            },
        )
        return "direct"

    @staticmethod
    def _summarize_work_title(message: str) -> str:
        cleaned = " ".join(message.strip().split())
        if not cleaned:
            return "Active Task"
        return f"Active Task: {cleaned[:60]}"

    @staticmethod
    def _parse_json_object_relaxed(raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        decoder = json.JSONDecoder()
        start = text.find("{")
        while start != -1:
            try:
                parsed, _ = decoder.raw_decode(text[start:])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            start = text.find("{", start + 1)
        return None

    @staticmethod
    def _format_capability_summary(tool_ids: list[str]) -> str:
        listed_tools = ", ".join(sorted(set(tool_ids))) if tool_ids else "none"
        return (
            "Direct capabilities: concise chat, arithmetic, quick factual answers, rewrites, and tool usage. "
            f"Available tools: {listed_tools}."
        )

    @staticmethod
    def _format_router_history(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "(no prior messages)"
        lines: list[str] = []
        for item in messages[-_CLASSIC_ROUTER_HISTORY_WINDOW:]:
            role = str(item.get("role") or "assistant")
            route = str(item.get("route") or "")
            content = " ".join(str(item.get("content") or "").split())
            if len(content) > 260:
                content = f"{content[:257]}..."
            prefix = f"{role}"
            if route:
                prefix = f"{prefix} ({route})"
            lines.append(f"- {prefix}: {content}")
        return "\n".join(lines)

    def _build_classic_router_prompt(
        self,
        *,
        session_id: str,
        message: str,
        active_work: dict[str, Any] | None,
    ) -> str:
        history = self._format_router_history(self.store.list_messages(session_id))
        active_work_block = "none"
        if active_work is not None:
            active_work_block = (
                f"work_id={active_work.get('work_id')}\n"
                f"title={str(active_work.get('title') or '').strip()}\n"
                f"summary={str(active_work.get('summary') or '').strip()}\n"
                f"workflow_type={str(active_work.get('workflow_type') or '').strip() or 'unknown'}"
            )
        tool_ids = getattr(self.runtime.profile, "tool_ids", None)
        if not isinstance(tool_ids, list):
            raw_tool_ids = getattr(self.runtime.profile, "tool_ids_json", None)
            tool_ids = raw_tool_ids if isinstance(raw_tool_ids, list) else []
        capabilities = self._format_capability_summary(tool_ids)
        return (
            f"Current user message:\n{message}\n\n"
            f"Recent chat history (latest up to {_CLASSIC_ROUTER_HISTORY_WINDOW}):\n{history}\n\n"
            f"Active delegated work context:\n{active_work_block}\n\n"
            f"Chanakya direct capabilities:\n{capabilities}\n\n"
            "Decide the routing action based on the system prompt rules. "
            "If active work exists, carefully check whether the user's message is a continuation "
            "or refinement of that work before choosing create_new_work."
        )

    def _run_classic_router_prompt(
        self,
        prompt: str,
        *,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> str:
        if self.classic_router_runner is not None:
            return str(self.classic_router_runner(prompt))
        if self._classic_router_runtime is None:
            return json.dumps(
                {
                    "action": "direct",
                    "confidence": 1.0,
                    "reason": "router_runtime_unavailable",
                    "handoff_message": "",
                }
            )

        router_session_id = make_id("router")
        router_request_id = make_id("router_req")
        try:
            result = self._classic_router_runtime.run(
                router_session_id,
                prompt,
                request_id=router_request_id,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )
            return str(result.text).strip()
        finally:
            self._classic_router_runtime.clear_session_state(router_session_id)

    @staticmethod
    def _validate_classic_router_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        action = str(payload.get("action") or "").strip()
        if action not in {"direct", "continue_active_work", "create_new_work"}:
            return None
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        reason = str(payload.get("reason") or "").strip()
        handoff_message = str(payload.get("handoff_message") or "").strip()
        if action == "direct":
            handoff_message = ""
        return {
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "handoff_message": handoff_message,
        }

    def _classic_router_fallback_decision(
        self,
        *,
        session_id: str,
        message: str,
        active_work: dict[str, Any] | None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "action": "direct",
            "confidence": 0.5,
            "reason": "router_fallback_direct",
            "handoff_message": "",
            "source": "fallback",
            "router_failed": True,
            "router_diagnostics": diagnostics or {},
        }

    def _decide_classic_execution(
        self,
        *,
        session_id: str,
        message: str,
        active_work: dict[str, Any] | None,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> dict[str, Any]:
        if self.manager is None:
            return {
                "action": "direct",
                "confidence": 1.0,
                "reason": "manager_unavailable",
                "handoff_message": "",
                "source": "fallback",
            }
        prompt = self._build_classic_router_prompt(
            session_id=session_id,
            message=message,
            active_work=active_work,
        )
        parsed: dict[str, Any] | None = None
        last_raw = ""
        errors: list[str] = []
        for attempt in range(2):
            try:
                attempt_prompt = prompt
                if attempt == 1 and last_raw:
                    attempt_prompt = (
                        f"{prompt}\n\n"
                        "Your previous output was invalid JSON.\n"
                        f"Previous output:\n{last_raw}\n"
                        "Return ONLY a valid JSON object matching schema."
                    )
                raw = self._run_classic_router_prompt(
                    attempt_prompt,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                )
                last_raw = raw
                payload = self._parse_json_object_relaxed(raw)
                if payload is None:
                    continue
                parsed = self._validate_classic_router_payload(payload)
                if parsed is not None:
                    break
                errors.append("invalid_router_schema")
            except Exception as exc:
                errors.append(str(exc))
                debug_log(
                    "classic_router_error",
                    {
                        "session_id": session_id,
                        "error": str(exc),
                        "attempt": attempt + 1,
                    },
                )
        if parsed is None:
            diagnostics = {
                "router_input": prompt,
                "router_output": last_raw,
                "errors": errors,
            }
            return self._classic_router_fallback_decision(
                session_id=session_id,
                message=message,
                active_work=active_work,
                diagnostics=diagnostics,
            )
        if (
            parsed.get("action") in {"continue_active_work", "create_new_work"}
            and float(parsed.get("confidence") or 0.0) < _CLASSIC_ROUTER_MIN_DELEGATION_CONFIDENCE
        ):
            try:
                retry_prompt = (
                    f"{prompt}\n\n"
                    f"Previous decision: {json.dumps(parsed)}\n"
                    "The previous delegation confidence is low. Re-evaluate and return JSON only. "
                    "If uncertain, choose direct."
                )
                retry_raw = self._run_classic_router_prompt(
                    retry_prompt,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                )
                retry_payload = self._parse_json_object_relaxed(retry_raw)
                retry_parsed = (
                    self._validate_classic_router_payload(retry_payload)
                    if isinstance(retry_payload, dict)
                    else None
                )
                if retry_parsed is not None:
                    parsed = retry_parsed
                    last_raw = retry_raw
                    errors.append("low_confidence_retry_applied")
                else:
                    errors.append("low_confidence_retry_invalid_schema")
            except Exception as exc:
                errors.append(f"low_confidence_retry_error:{exc}")
        if (
            parsed.get("action") in {"continue_active_work", "create_new_work"}
            and float(parsed.get("confidence") or 0.0) < _CLASSIC_ROUTER_MIN_DELEGATION_CONFIDENCE
        ):
            diagnostics = {
                "router_input": prompt,
                "router_output": last_raw,
                "errors": [
                    *errors,
                    f"low_confidence_delegation:{parsed.get('confidence')}",
                    f"proposed_action:{parsed.get('action')}",
                    f"reason:{parsed.get('reason')}",
                ],
            }
            return self._classic_router_fallback_decision(
                session_id=session_id,
                message=message,
                active_work=active_work,
                diagnostics=diagnostics,
            )
        decision = {
            **parsed,
            "source": "llm",
            "router_trace": {
                "input": prompt,
                "output": last_raw,
                "errors": errors,
            },
        }
        if decision["action"] == "continue_active_work" and active_work is None:
            decision["action"] = "create_new_work"
        if decision["action"] != "direct" and not decision.get("handoff_message"):
            decision["handoff_message"] = message
        debug_log(
            "classic_router_decision",
            {
                "session_id": session_id,
                "action": decision["action"],
                "confidence": decision["confidence"],
                "source": decision["source"],
                "reason": decision.get("reason"),
            },
        )
        return decision

    def _ensure_classic_active_work(self, session_id: str, message: str) -> dict[str, str | None]:
        active_work = self.store.get_active_classic_work(session_id)
        if active_work is not None:
            return active_work
        work_id = make_id(_CLASSIC_ACTIVE_WORK_PREFIX)
        title = self._summarize_work_title(message)
        work_session_id = make_id("session")
        self.store.create_work(
            work_id=work_id,
            title=title,
            description="Classic chat active delegated workspace",
            status="active",
        )
        resolve_shared_workspace(work_id)
        active_profiles = [
            profile for profile in self.store.list_agent_profiles() if profile.is_active
        ]
        for profile in active_profiles:
            mapped_session_id = (
                work_session_id if profile.id == self.runtime.profile.id else make_id("session")
            )
            self.store.ensure_work_agent_session(
                work_id=work_id,
                agent_id=profile.id,
                session_id=mapped_session_id,
                session_title=f"{title} - {profile.name}",
            )
        active_work = {
            "chat_session_id": session_id,
            "work_id": work_id,
            "work_session_id": work_session_id,
            "root_request_id": None,
            "title": title,
            "summary": message,
            "workflow_type": None,
        }
        self.store.set_active_classic_work(
            chat_session_id=session_id,
            work_id=work_id,
            work_session_id=work_session_id,
            root_request_id=None,
            title=title,
            summary=message,
            workflow_type=None,
        )
        self.store.log_event(
            "classic_active_work_created",
            {"session_id": session_id, "work_id": work_id, "work_session_id": work_session_id},
        )
        return active_work

    def _replace_classic_active_work(self, session_id: str, message: str) -> dict[str, str | None]:
        existing = self.store.get_active_classic_work(session_id)
        if existing is not None:
            replaced_work_id = str(existing["work_id"])
            if self._classic_work_running(replaced_work_id):
                self.store.log_event(
                    "classic_active_work_replaced_while_running",
                    {"session_id": session_id, "replaced_work_id": replaced_work_id},
                )
            else:
                deleted_session_ids = self.store.delete_work(replaced_work_id)
                for deleted_session_id in deleted_session_ids:
                    self.runtime.clear_session_state(deleted_session_id)
                delete_shared_workspace(replaced_work_id)
                self.store.log_event(
                    "classic_active_work_replaced",
                    {"session_id": session_id, "replaced_work_id": replaced_work_id},
                )
        return self._ensure_classic_active_work(session_id, message)

    def _update_classic_active_work_from_reply(
        self,
        *,
        session_id: str,
        active_work: dict[str, str | None],
        reply: ChatReply,
        summary: str,
    ) -> None:
        self.store.set_active_classic_work(
            chat_session_id=session_id,
            work_id=str(active_work["work_id"]),
            work_session_id=str(active_work["work_session_id"]),
            root_request_id=reply.request_id,
            title=str(active_work["title"]),
            summary=summary,
            workflow_type=reply.response_mode,
        )

    @staticmethod
    def _workspace_has_saved_files(work_id: str) -> bool:
        try:
            workspace = resolve_shared_workspace(work_id, create=False)
        except (ValueError, PermissionError):
            return False
        if not workspace.exists():
            return False
        return any(path.is_file() for path in workspace.rglob("*"))

    @staticmethod
    def _trim_sentence(text: str, *, limit: int = 220) -> str:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        if len(normalized) <= limit:
            return normalized
        boundary = max(normalized.rfind(". ", 0, limit), normalized.rfind("; ", 0, limit))
        if boundary >= 40:
            return normalized[: boundary + 1].strip()
        return normalized[: limit - 3].rstrip() + "..."

    def _build_classic_completion_message(
        self,
        *,
        work_id: str,
        task_status: str | None,
        manager_message: str,
    ) -> str:
        summary = self._trim_sentence(manager_message, limit=220)
        if task_status == TASK_STATUS_WAITING_INPUT:
            return "BTW, I got an update on the delegated work. The expert needs one more detail from you before they can continue."
        if task_status == TASK_STATUS_FAILED:
            if summary:
                return (
                    "BTW, I got an update on the delegated work. It hit a problem before it could finish. "
                    f"{summary}"
                )
            return "BTW, I got an update on the delegated work. It hit a problem before it could finish."
        workspace_note = (
            " The files are saved in the workspace."
            if self._workspace_has_saved_files(work_id)
            else ""
        )
        if summary:
            return (
                "BTW, I received the report on the work you asked for. The work is done."
                f"{workspace_note} {summary}"
            ).strip()
        return (
            "BTW, I received the report on the work you asked for. The work is done."
            f"{workspace_note}"
        ).strip()

    def _mirror_classic_background_result(
        self,
        *,
        classic_session_id: str,
        active_work: dict[str, str | None],
        user_message: str,
        reply: ChatReply,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> None:
        current_active_work = self.store.get_active_classic_work(classic_session_id)
        if current_active_work is None or str(current_active_work.get("work_id") or "") != str(
            active_work.get("work_id") or ""
        ):
            return
        base_metadata = {
            "runtime": reply.runtime,
            "response_mode": reply.response_mode,
            "root_task_id": reply.root_task_id,
            "task_status": reply.root_task_status,
            "active_work_id": active_work["work_id"],
            "active_work_session_id": active_work["work_session_id"],
            "mirrored_from_work": True,
            "waiting_task_id": reply.waiting_task_id,
            "input_prompt": reply.input_prompt,
            "awaiting_user_input": reply.requires_input,
            "classic_background_completion": True,
        }
        if reply.requires_input and reply.input_prompt:
            self.store.add_message(
                classic_session_id,
                "assistant",
                reply.input_prompt,
                request_id=reply.request_id,
                route=_WAITING_INPUT_ROUTE,
                metadata=base_metadata,
            )
        else:
            assistant_message = self._build_classic_completion_message(
                work_id=str(active_work["work_id"]),
                task_status=reply.root_task_status,
                manager_message=reply.message,
            )
            runtime_metadata = {
                **dict(reply.metadata or {}),
                "runtime": reply.runtime,
                "model": reply.model,
                "endpoint": reply.endpoint,
                "core_agent_backend": normalize_runtime_backend(backend),
                "a2a_remote_url": a2a_url,
                "a2a_remote_agent": a2a_remote_agent,
                "a2a_model_provider": a2a_model_provider,
                "a2a_model_id": a2a_model_id,
            }
            conversation_result = self._build_conversation_layer_result(
                session_id=classic_session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                model_id=model_id,
                request_id=reply.request_id,
                runtime_metadata=runtime_metadata,
            )
            response_metadata = {
                **base_metadata,
                **dict(reply.metadata or {}),
            }
            if conversation_result is not None:
                response_metadata = {**response_metadata, **conversation_result.metadata}
                messages = conversation_result.messages or [
                    {"text": assistant_message, "delay_ms": 0}
                ]
                self._persist_conversation_messages(
                    session_id=classic_session_id,
                    request_id=reply.request_id,
                    route=_CLASSIC_WORK_COMPLETION_ROUTE,
                    base_metadata=response_metadata,
                    messages=messages,
                )
            else:
                self.store.add_message(
                    classic_session_id,
                    "assistant",
                    assistant_message,
                    request_id=reply.request_id,
                    route=_CLASSIC_WORK_COMPLETION_ROUTE,
                    metadata=response_metadata,
                )
        self._update_classic_active_work_from_reply(
            session_id=classic_session_id,
            active_work=active_work,
            reply=reply,
            summary=user_message,
        )

    def _complete_classic_active_work_sync(
        self,
        *,
        classic_session_id: str,
        active_work: dict[str, str | None],
        message: str,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> ChatReply:
        reply = self._chat_internal(
            str(active_work["work_session_id"]),
            message,
            work_id=str(active_work["work_id"]),
            force_manager_execution=True,
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )
        base_metadata = {
            "runtime": reply.runtime,
            "response_mode": reply.response_mode,
            "root_task_id": reply.root_task_id,
            "task_status": reply.root_task_status,
            "active_work_id": active_work["work_id"],
            "active_work_session_id": active_work["work_session_id"],
            "mirrored_from_work": True,
            "waiting_task_id": reply.waiting_task_id,
            "input_prompt": reply.input_prompt,
            "awaiting_user_input": reply.requires_input,
        }
        if reply.requires_input and reply.input_prompt:
            self.store.add_message(
                classic_session_id,
                "assistant",
                reply.input_prompt,
                request_id=reply.request_id,
                route=_WAITING_INPUT_ROUTE,
                metadata=base_metadata,
            )
            classic_reply_messages: list[dict[str, Any]] = []
            classic_reply_metadata = dict(reply.metadata or {})
        else:
            classic_reply_messages = reply.messages or [{"text": reply.message, "delay_ms": 0}]
            classic_reply_metadata = dict(reply.metadata or {})
            if classic_reply_metadata.get("source") == "conversation_layer":
                classic_conversation_result = self._build_conversation_layer_result(
                    session_id=classic_session_id,
                    user_message=message,
                    assistant_message=str(
                        classic_reply_metadata.get("core_agent_response") or reply.message
                    ),
                    model_id=model_id,
                    request_id=reply.request_id,
                    runtime_metadata=classic_reply_metadata,
                )
                if classic_conversation_result is not None:
                    classic_reply_messages = (
                        classic_conversation_result.messages or classic_reply_messages
                    )
                    classic_reply_metadata = {
                        **classic_reply_metadata,
                        **classic_conversation_result.metadata,
                    }
            self._persist_conversation_messages(
                session_id=classic_session_id,
                request_id=reply.request_id,
                route=reply.route,
                base_metadata={
                    **base_metadata,
                    **classic_reply_metadata,
                },
                messages=classic_reply_messages,
            )
        classic_visible_message = self._conversation_message_content(
            classic_reply_messages, reply.message
        )
        self._update_classic_active_work_from_reply(
            session_id=classic_session_id,
            active_work=active_work,
            reply=reply,
            summary=message,
        )
        return ChatReply(
            request_id=reply.request_id,
            session_id=classic_session_id,
            work_id=str(active_work["work_id"]),
            route=reply.route,
            message=classic_visible_message,
            model=reply.model,
            endpoint=reply.endpoint,
            runtime=reply.runtime,
            agent_name=reply.agent_name,
            request_status=reply.request_status,
            root_task_id=reply.root_task_id,
            root_task_status=reply.root_task_status,
            response_mode=reply.response_mode,
            tool_calls_used=reply.tool_calls_used,
            tool_trace_ids=reply.tool_trace_ids,
            requires_input=reply.requires_input,
            waiting_task_id=reply.waiting_task_id,
            input_prompt=reply.input_prompt,
            messages=classic_reply_messages,
            metadata=classic_reply_metadata,
        )

    def _run_classic_active_work_background(
        self,
        *,
        classic_session_id: str,
        active_work: dict[str, str | None],
        message: str,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> None:
        work_id = str(active_work["work_id"])
        try:
            reply = self._chat_internal(
                str(active_work["work_session_id"]),
                message,
                work_id=work_id,
                force_manager_execution=True,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )
            self._mirror_classic_background_result(
                classic_session_id=classic_session_id,
                active_work=active_work,
                user_message=message,
                reply=reply,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )
        except Exception as exc:
            failure_text = (
                "BTW, I got an update on the delegated work. It failed before completion. "
                + self._trim_sentence(str(exc), limit=180)
            ).strip()
            current_active_work = self.store.get_active_classic_work(classic_session_id)
            if (
                current_active_work is not None
                and str(current_active_work.get("work_id") or "") == work_id
            ):
                self.store.add_message(
                    classic_session_id,
                    "assistant",
                    failure_text,
                    route=_CLASSIC_WORK_COMPLETION_ROUTE,
                    metadata={
                        "runtime": "maf_agent",
                        "response_mode": "failed",
                        "active_work_id": work_id,
                        "active_work_session_id": active_work["work_session_id"],
                        "classic_background_completion": True,
                        "task_status": TASK_STATUS_FAILED,
                    },
                )
            debug_log(
                "classic_background_work_failed",
                {
                    "session_id": classic_session_id,
                    "work_id": work_id,
                    "error": str(exc),
                },
            )
        finally:
            self._mark_classic_work_finished(work_id)

    @staticmethod
    def _format_chanakya_input_prompt(question: str) -> str:
        cleaned = " ".join(question.strip().split())
        if not cleaned:
            return "I need one detail before I can continue."
        if cleaned.endswith(("?", ".", "!")):
            return f"I need one detail before I can continue: {cleaned}"
        return f"I need one detail before I can continue: {cleaned}?"

    @classmethod
    def _is_waiting_input_cancel_intent(cls, message: str) -> bool:
        lowered = message.strip().lower()
        return any(marker in lowered for marker in _WAITING_INPUT_CANCEL_MARKERS)

    def _cancel_waiting_task_via_chat(
        self,
        *,
        visible_session_id: str,
        task_id: str,
        user_message: str,
        work_id: str | None,
        active_work_session_id: str | None = None,
    ) -> ChatReply:
        task = self.store.get_task(task_id)
        request = self.store.get_request(task.request_id)
        self.cancel_task(task_id)
        self.store.add_message(
            visible_session_id,
            "user",
            user_message,
            request_id=request.id,
            route="active_work_user_message" if work_id is not None else None,
            metadata={
                "input_submission": True,
                "cancel_waiting_task": True,
                "active_work_id": work_id,
                "active_work_session_id": active_work_session_id,
            },
        )
        final_message = "Stopped that task. I won't continue it unless you ask me to restart it."
        self.store.add_message(
            visible_session_id,
            "assistant",
            final_message,
            request_id=request.id,
            route="task_cancelled",
            metadata={
                "runtime": "maf_agent",
                "task_status": TASK_STATUS_CANCELLED,
                "cancelled_task_id": task_id,
                "active_work_id": work_id,
                "active_work_session_id": active_work_session_id,
            },
        )
        return ChatReply(
            request_id=request.id,
            session_id=visible_session_id,
            work_id=work_id,
            route="task_cancelled",
            message=final_message,
            model=None,
            endpoint=None,
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
            request_status=REQUEST_STATUS_CANCELLED,
            root_task_id=request.root_task_id,
            root_task_status=TASK_STATUS_CANCELLED,
            response_mode="cancelled",
            tool_calls_used=0,
            tool_trace_ids=[],
            requires_input=False,
            waiting_task_id=None,
            input_prompt=None,
        )

    def _chat_in_active_work(
        self,
        classic_session_id: str,
        message: str,
        *,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> ChatReply:
        active_work = self.store.get_active_classic_work(classic_session_id)
        if active_work is None:
            active_work = self._ensure_classic_active_work(classic_session_id, message)
        if self._classic_work_running(str(active_work["work_id"])):
            busy_message = (
                "The expert is still working on that delegated task. I can keep chatting here, "
                "and once that run finishes I will deliver the update."
            )
            self.store.add_message(
                classic_session_id,
                "user",
                message,
                route="active_work_user_message",
                metadata={
                    "active_work_id": active_work["work_id"],
                    "active_work_session_id": active_work["work_session_id"],
                    "mirrored_from": "classic_chat",
                },
            )
            self.store.add_message(
                classic_session_id,
                "assistant",
                busy_message,
                route="delegation_notice",
                metadata={
                    "runtime": "maf_agent",
                    "delegation_notice": True,
                    "active_work_id": active_work["work_id"],
                    "active_work_session_id": active_work["work_session_id"],
                    "delegated_background_busy": True,
                },
            )
            return ChatReply(
                request_id=make_id("req"),
                session_id=classic_session_id,
                work_id=str(active_work["work_id"]),
                route="delegated_manager",
                message=busy_message,
                model=model_id,
                endpoint=a2a_url if normalize_runtime_backend(backend) == "a2a" else None,
                runtime="maf_agent",
                agent_name=self.runtime.profile.name,
                request_status=REQUEST_STATUS_IN_PROGRESS,
                root_task_status=TASK_STATUS_IN_PROGRESS,
                response_mode="delegated_background",
                messages=[{"text": busy_message, "delay_ms": 0}],
                metadata={
                    "runtime": "maf_agent",
                    "response_mode": "delegated_background",
                    "active_work_id": active_work["work_id"],
                    "active_work_session_id": active_work["work_session_id"],
                    "delegated_background": True,
                    "delegated_background_busy": True,
                },
            )
        self.store.add_message(
            classic_session_id,
            "user",
            message,
            route="active_work_user_message",
            metadata={
                "active_work_id": active_work["work_id"],
                "active_work_session_id": active_work["work_session_id"],
                "mirrored_from": "classic_chat",
            },
        )
        self.store.add_message(
            classic_session_id,
            "assistant",
            _NORMAL_CHAT_DELEGATION_NOTICE,
            route="delegation_notice",
            metadata={
                "runtime": "maf_agent",
                "delegation_notice": True,
                "active_work_id": active_work["work_id"],
                "active_work_session_id": active_work["work_session_id"],
            },
        )
        self.store.create_task_event(
            session_id=str(active_work["work_session_id"]),
            event_type="delegation_notice_persisted",
            payload={"message": _NORMAL_CHAT_DELEGATION_NOTICE},
        )
        if not self.classic_async_enabled:
            return self._complete_classic_active_work_sync(
                classic_session_id=classic_session_id,
                active_work=active_work,
                message=message,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )
        work_id = str(active_work["work_id"])
        self._mark_classic_work_running(work_id)
        self._launch_background_call(
            self._run_classic_active_work_background,
            classic_session_id=classic_session_id,
            active_work=active_work,
            message=message,
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )
        classic_reply_metadata = {
            "runtime": "maf_agent",
            "response_mode": "delegated_background",
            "active_work_id": active_work["work_id"],
            "active_work_session_id": active_work["work_session_id"],
            "delegated_background": True,
            "pending_classic_work": True,
        }
        classic_reply_messages = [{"text": _NORMAL_CHAT_DELEGATION_NOTICE, "delay_ms": 0}]
        classic_visible_message = _NORMAL_CHAT_DELEGATION_NOTICE
        return ChatReply(
            request_id=make_id("req"),
            session_id=classic_session_id,
            work_id=work_id,
            route="delegated_manager",
            message=classic_visible_message,
            model=model_id,
            endpoint=a2a_url if normalize_runtime_backend(backend) == "a2a" else None,
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
            request_status=REQUEST_STATUS_IN_PROGRESS,
            root_task_id=None,
            root_task_status=TASK_STATUS_IN_PROGRESS,
            response_mode="delegated_background",
            tool_calls_used=0,
            tool_trace_ids=[],
            requires_input=False,
            waiting_task_id=None,
            input_prompt=None,
            messages=classic_reply_messages,
            metadata=classic_reply_metadata,
        )

    def chat(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> ChatReply:
        backend = normalize_runtime_backend(backend)
        classic_router_failure: dict[str, Any] | None = None
        classic_router_decision: dict[str, Any] | None = None
        if work_id is None:
            active_work = self.store.get_active_classic_work(session_id)
            active_work_session_id = (
                str(active_work["work_session_id"]) if active_work is not None else None
            )
            if active_work_session_id is not None:
                resumable_task = self.store.find_waiting_input_task(active_work_session_id)
                if resumable_task is not None:
                    if self._is_waiting_input_cancel_intent(message):
                        return self._cancel_waiting_task_via_chat(
                            visible_session_id=session_id,
                            task_id=str(resumable_task["id"]),
                            user_message=message,
                            work_id=str(active_work["work_id"]),
                            active_work_session_id=active_work_session_id,
                        )
                    reply = self.submit_task_input(str(resumable_task["id"]), message)
                    self.store.add_message(
                        session_id,
                        "user",
                        message,
                        request_id=reply.request_id,
                        route="active_work_user_message",
                        metadata={
                            "active_work_id": active_work["work_id"],
                            "active_work_session_id": active_work_session_id,
                            "mirrored_from": "classic_chat",
                            "input_submission": True,
                        },
                    )
                    if reply.input_prompt:
                        self.store.add_message(
                            session_id,
                            "assistant",
                            reply.message,
                            request_id=reply.request_id,
                            route=_WAITING_INPUT_ROUTE,
                            metadata={
                                "active_work_id": active_work["work_id"],
                                "active_work_session_id": active_work_session_id,
                                "mirrored_from_work": True,
                                "waiting_task_id": reply.waiting_task_id,
                                "input_prompt": reply.input_prompt,
                                "awaiting_user_input": True,
                            },
                        )
                    elif reply.message:
                        self.store.add_message(
                            session_id,
                            "assistant",
                            reply.message,
                            request_id=reply.request_id,
                            route=reply.route,
                            metadata={
                                "active_work_id": active_work["work_id"],
                                "active_work_session_id": active_work_session_id,
                                "mirrored_from_work": True,
                            },
                        )
                    self._update_classic_active_work_from_reply(
                        session_id=session_id,
                        active_work=active_work,
                        reply=reply,
                        summary=message,
                    )
                    return ChatReply(
                        request_id=reply.request_id,
                        session_id=session_id,
                        work_id=str(active_work["work_id"]),
                        route=reply.route,
                        message=reply.message,
                        model=reply.model,
                        endpoint=reply.endpoint,
                        runtime=reply.runtime,
                        agent_name=reply.agent_name,
                        request_status=reply.request_status,
                        root_task_id=reply.root_task_id,
                        root_task_status=reply.root_task_status,
                        response_mode=reply.response_mode,
                        tool_calls_used=reply.tool_calls_used,
                        tool_trace_ids=reply.tool_trace_ids,
                        requires_input=reply.requires_input,
                        waiting_task_id=reply.waiting_task_id,
                        input_prompt=reply.input_prompt,
                    )

            if self.manager is not None:
                decision = self._decide_classic_execution(
                    session_id=session_id,
                    message=message,
                    active_work=active_work,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                )
                action = str(decision.get("action") or "direct")
                handoff_message = str(decision.get("handoff_message") or "").strip() or message
                classic_router_decision = {
                    "action": action,
                    "confidence": decision.get("confidence"),
                    "reason": decision.get("reason"),
                    "source": decision.get("source"),
                    "trace": decision.get("router_trace"),
                }
                self.store.log_event(
                    "classic_router_decision",
                    {
                        "session_id": session_id,
                        "action": action,
                        "confidence": decision.get("confidence"),
                        "reason": decision.get("reason"),
                        "source": decision.get("source"),
                    },
                )
                if bool(decision.get("router_failed")):
                    diagnostics = decision.get("router_diagnostics")
                    classic_router_failure = diagnostics if isinstance(diagnostics, dict) else {}

                if action in {"continue_active_work", "create_new_work"}:
                    if action == "create_new_work":
                        active_work = self._replace_classic_active_work(session_id, handoff_message)
                    if handoff_message.strip() != message.strip():
                        self.store.add_message(
                            session_id,
                            "user",
                            message,
                            route="delegation_control",
                            metadata={
                                "delegation_control": True,
                                "delegation_target_message": handoff_message,
                                "router_reason": decision.get("reason"),
                                "router_source": decision.get("source"),
                            },
                        )
                    return self._chat_in_active_work(
                        session_id,
                        handoff_message,
                        model_id=model_id,
                        backend=backend,
                        a2a_url=a2a_url,
                        a2a_remote_agent=a2a_remote_agent,
                        a2a_model_provider=a2a_model_provider,
                        a2a_model_id=a2a_model_id,
                    )

        resumable_task = self.store.find_waiting_input_task(session_id)
        if resumable_task is not None:
            if self._is_waiting_input_cancel_intent(message):
                return self._cancel_waiting_task_via_chat(
                    visible_session_id=session_id,
                    task_id=str(resumable_task["id"]),
                    user_message=message,
                    work_id=work_id,
                )
            return self.submit_task_input(str(resumable_task["id"]), message)

        return self._chat_internal(
            session_id,
            message,
            work_id=work_id,
            allow_manager_delegation=work_id is not None,
            classic_router_failure=classic_router_failure,
            classic_router_decision=classic_router_decision,
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )

    def _chat_internal(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        allow_manager_delegation: bool = True,
        force_manager_execution: bool = False,
        classic_router_failure: dict[str, Any] | None = None,
        classic_router_decision: dict[str, Any] | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> ChatReply:
        request_id = make_id("req")
        root_task_id = make_id("task")
        runtime_meta = self.runtime.runtime_metadata(
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )
        runtime_snapshot = self._runtime_snapshot_from_metadata(runtime_meta)
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
            input_json={"message": message, "runtime_config": runtime_snapshot},
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
        resolved_work_id = work_id

        try:
            use_manager = False
            manager_invoked = False
            if self.manager is not None and allow_manager_delegation:
                if force_manager_execution:
                    use_manager = True
                    self.store.create_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        task_id=root_task_id,
                        event_type="triage_completed",
                        payload={
                            "decision": "forced_delegate",
                            "use_manager": True,
                        },
                    )
                else:
                    triage = self._triage_message(message, work_id=work_id)
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
                manager_invoked = True
                manager_work_id = work_id
                if manager_work_id is None:
                    active_classic_work = self.store.get_active_classic_work(session_id)
                    if active_classic_work is not None:
                        manager_work_id = (
                            str(active_classic_work.get("work_id") or "").strip() or None
                        )
                resolved_work_id = manager_work_id
                if work_id is None:
                    self.store.add_message(
                        session_id,
                        "assistant",
                        _NORMAL_CHAT_DELEGATION_NOTICE,
                        request_id=request_id,
                        route="delegation_notice",
                        metadata={
                            "runtime": "maf_agent",
                            "delegation_notice": True,
                            "request_status": REQUEST_STATUS_IN_PROGRESS,
                            "root_task_id": root_task_id,
                        },
                    )
                    self.store.create_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        task_id=root_task_id,
                        event_type="delegation_notice_persisted",
                        payload={"message": _NORMAL_CHAT_DELEGATION_NOTICE},
                    )
                context_tokens = self.manager.bind_execution_context(
                    session_id=session_id,
                    work_id=manager_work_id,
                    model_id=model_id,
                    backend=runtime_snapshot["backend"],
                    a2a_url=runtime_snapshot["a2a_url"],
                    a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
                    a2a_model_provider=runtime_snapshot["a2a_model_provider"],
                    a2a_model_id=runtime_snapshot["a2a_model_id"],
                )
                try:
                    followup_artifacts = self._resolve_targeted_writer_followup_artifacts(
                        session_id=session_id,
                        work_id=manager_work_id,
                        message=message,
                    )
                    if followup_artifacts is not None:
                        self.store.create_task_event(
                            session_id=session_id,
                            request_id=request_id,
                            task_id=root_task_id,
                            event_type="work_followup_detected",
                            payload={
                                "intent": "writer_modification",
                                "targeted_stage": "writer",
                                "source_request_id": followup_artifacts["source_request_id"],
                            },
                        )
                        manager_result = self.manager.execute_targeted_writer_followup(
                            session_id=session_id,
                            request_id=request_id,
                            root_task_id=root_task_id,
                            message=message,
                            previous_writer_output=followup_artifacts["writer_output"],
                            previous_research_handoff=followup_artifacts.get("research_handoff"),
                            source_request_id=followup_artifacts.get("source_request_id"),
                        )
                    else:
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
                run_result = self._runtime_run(
                    session_id,
                    message,
                    request_id=request_id,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                    prompt_addendum=self._runtime_prompt_addendum_for_mode(work_id=work_id),
                )
                manager_invoked = False
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
            self._notify_root_task_outcome(
                session_id=session_id,
                request_id=request_id,
                root_task_id=root_task_id,
                task_status=TASK_STATUS_FAILED,
                work_id=resolved_work_id,
                summary=str(exc),
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
            input_prompt = (
                self._format_chanakya_input_prompt(manager_result.input_prompt)
                if manager_result.input_prompt
                else None
            )
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
        response_metadata: dict[str, Any] = {}
        response_messages: list[dict[str, Any]] = []
        if task_status == TASK_STATUS_WAITING_INPUT and input_prompt:
            self.store.add_message(
                session_id,
                "assistant",
                input_prompt,
                request_id=request_id,
                route=_WAITING_INPUT_ROUTE,
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
                    "awaiting_user_input": True,
                },
            )
        elif task_status != TASK_STATUS_WAITING_INPUT:
            actual_runtime_meta = (
                runtime_meta
                if manager_result is None
                else self.runtime.runtime_metadata(
                    model_id=runtime_snapshot["model_id"],
                    backend=runtime_snapshot["backend"],
                    a2a_url=runtime_snapshot["a2a_url"],
                    a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
                    a2a_model_provider=runtime_snapshot["a2a_model_provider"],
                    a2a_model_id=runtime_snapshot["a2a_model_id"],
                )
            )
            response_metadata = {
                "runtime": str(actual_runtime_meta.get("runtime") or "maf_agent"),
                "core_agent_backend": str(actual_runtime_meta.get("backend") or "local"),
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
                "manager_invoked": manager_invoked,
                "execution_path": "manager" if manager_invoked else "direct_runtime",
            }
            if classic_router_failure and work_id is None:
                response_metadata["router_failure"] = {
                    "input": str(classic_router_failure.get("router_input") or ""),
                    "output": str(classic_router_failure.get("router_output") or ""),
                    "errors": list(classic_router_failure.get("errors") or []),
                }
            if classic_router_decision and work_id is None:
                response_metadata["router_decision"] = {
                    "action": classic_router_decision.get("action"),
                    "confidence": classic_router_decision.get("confidence"),
                    "reason": classic_router_decision.get("reason"),
                    "source": classic_router_decision.get("source"),
                    "trace": classic_router_decision.get("trace"),
                }
            if manager_result is not None:
                for key in (
                    "model",
                    "endpoint",
                    "a2a_remote_agent",
                    "a2a_model_provider",
                    "a2a_model_id",
                ):
                    if key in actual_runtime_meta:
                        response_metadata[key] = actual_runtime_meta.get(key)
            run_metadata = getattr(run_result, "metadata", None) if run_result is not None else None
            if isinstance(run_metadata, dict):
                response_metadata.update(run_metadata)
            conversation_result = None
            if (
                manager_result is None
                and response_mode == "direct_answer"
                and (
                    isinstance(self.runtime, MAFRuntime)
                    or not isinstance(self._conversation_layer, ConversationLayerSupport)
                )
            ):
                conversation_result = self._build_conversation_layer_result(
                    session_id=session_id,
                    user_message=message,
                    assistant_message=final_message,
                    model_id=model_id,
                    request_id=request_id,
                    runtime_metadata=response_metadata,
                )
            if conversation_result is not None:
                response_metadata = {**response_metadata, **conversation_result.metadata}
                messages = conversation_result.messages or [{"text": final_message, "delay_ms": 0}]
                response_messages = messages
                self._persist_conversation_messages(
                    session_id=session_id,
                    request_id=request_id,
                    route=route,
                    base_metadata=response_metadata,
                    messages=messages,
                )
                final_message = self._conversation_message_content(
                    messages, conversation_result.response
                )
            else:
                response_messages = [{"text": final_message, "delay_ms": 0}]
                self.store.add_message(
                    session_id,
                    "assistant",
                    final_message,
                    request_id=request_id,
                    route=route,
                    metadata=response_metadata,
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
        self._notify_root_task_outcome(
            session_id=session_id,
            request_id=request_id,
            root_task_id=root_task_id,
            task_status=task_status,
            work_id=resolved_work_id,
            summary=input_prompt if task_status == TASK_STATUS_WAITING_INPUT else final_message,
        )

        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            work_id=resolved_work_id,
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
            messages=response_messages,
            metadata=response_metadata,
        )
        self.store.log_event(
            "chat_response",
            {
                "request_id": request_id,
                "session_id": session_id,
                "work_id": resolved_work_id,
                "route": route,
                "runtime": reply.runtime,
                "core_agent_backend": response_metadata.get("core_agent_backend"),
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

    @staticmethod
    def _is_writer_modification_message(message: str) -> bool:
        lowered = message.strip().lower()
        if not lowered:
            return False
        software_markers = {
            "implement",
            "code",
            "api",
            "database",
            "bug",
            "test",
            "refactor",
            "endpoint",
            "function",
            "class",
        }
        if any(marker in lowered for marker in software_markers):
            return False
        modification_markers = {
            "make it",
            "make this",
            "update",
            "revise",
            "rewrite",
            "rephrase",
            "shorter",
            "longer",
            "more formal",
            "less formal",
            "tone",
            "improve wording",
            "fix grammar",
            "add section",
            "remove section",
            "expand",
            "condense",
            "polish",
            "refine",
        }
        referential_markers = {"it", "this", "that", "above", "draft", "response", "report"}
        return any(marker in lowered for marker in modification_markers) and any(
            token in lowered for token in referential_markers
        )

    def _resolve_targeted_writer_followup_artifacts(
        self,
        *,
        session_id: str,
        work_id: str | None,
        message: str,
    ) -> dict[str, str] | None:
        if work_id is None:
            return None
        if self.manager is None:
            return None
        if not self._is_writer_modification_message(message):
            return None
        tasks = self.store.list_tasks(session_id=session_id, limit=400)
        writer_task = None
        for task in reversed(tasks):
            if task.get("task_type") != "writer_execution":
                continue
            if task.get("status") != TASK_STATUS_DONE:
                continue
            written = str((task.get("result") or {}).get("written_response") or "").strip()
            if not written:
                continue
            writer_task = task
            break
        if writer_task is None:
            return None
        source_request_id = str(writer_task.get("request_id") or "").strip() or None
        research_handoff = None
        if source_request_id:
            related = self.store.list_tasks(request_id=source_request_id, limit=60)
            for task in reversed(related):
                if task.get("task_type") != "researcher_execution":
                    continue
                handoff = str((task.get("result") or {}).get("handoff") or "").strip()
                if handoff:
                    research_handoff = handoff
                    break
        return {
            "writer_output": str((writer_task.get("result") or {}).get("written_response") or ""),
            "research_handoff": research_handoff or "",
            "source_request_id": source_request_id or "",
        }

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
        runtime_meta = self._runtime_metadata()
        root_task_id = request.root_task_id
        if root_task_id is None:
            raise RuntimeError("Waiting task request is missing a root task")
        root_task = self.store.get_task(root_task_id)
        runtime_snapshot = self._runtime_snapshot_from_task_input(root_task.input_json)
        runtime_meta = self.runtime.runtime_metadata(
            model_id=runtime_snapshot["model_id"],
            backend=runtime_snapshot["backend"],
            a2a_url=runtime_snapshot["a2a_url"],
            a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
            a2a_model_provider=runtime_snapshot["a2a_model_provider"],
            a2a_model_id=runtime_snapshot["a2a_model_id"],
        )
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
            model_id=runtime_snapshot["model_id"],
            backend=runtime_snapshot["backend"],
            a2a_url=runtime_snapshot["a2a_url"],
            a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
            a2a_model_provider=runtime_snapshot["a2a_model_provider"],
            a2a_model_id=runtime_snapshot["a2a_model_id"],
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
        if result.task_status == TASK_STATUS_WAITING_INPUT and result.input_prompt:
            self.store.add_message(
                session_id,
                "assistant",
                result.input_prompt,
                request_id=request.id,
                route=_WAITING_INPUT_ROUTE,
                metadata={
                    "runtime": "maf_agent",
                    "core_agent_backend": str(runtime_meta.get("backend") or "local"),
                    "response_mode": result.workflow_type,
                    "tool_calls_used": 0,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": result.task_status,
                    "workflow_type": result.workflow_type,
                    "child_task_ids": result.child_task_ids,
                    "waiting_task_id": result.waiting_task_id,
                    "input_prompt": result.input_prompt,
                    "awaiting_user_input": True,
                },
            )
        elif result.task_status != TASK_STATUS_WAITING_INPUT:
            self.store.add_message(
                session_id,
                "assistant",
                result.text,
                request_id=request.id,
                route="delegated_manager",
                metadata={
                    "runtime": "maf_agent",
                    "core_agent_backend": str(runtime_meta.get("backend") or "local"),
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
        self._notify_root_task_outcome(
            session_id=session_id,
            request_id=request.id,
            root_task_id=root_task_id,
            task_status=result.task_status,
            work_id=work_id,
            summary=result.input_prompt
            if result.task_status == TASK_STATUS_WAITING_INPUT
            else result.text,
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
