from __future__ import annotations

import asyncio
import re

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatClient

from chanakya.agent.runtime import build_profile_agent, normalize_runtime_backend
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
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.services.sandbox_workspace import delete_shared_workspace
from chanakya.store import ChanakyaStore


_TRIAGE_SYSTEM_PROMPT = (
    "You are a request classifier. Your ONLY job is to output exactly one word.\n"
    'Reply with "direct" if the user\'s message is:\n'
    "- A greeting, small talk, or simple conversational exchange\n"
    "- A simple factual question answerable from general knowledge\n"
    "- A math calculation or unit conversion\n"
    "- A weather check or current conditions lookup\n"
    "- A request to fetch or summarise a single URL or web page\n"
    "- Anything a single assistant with a calculator and web-fetch tool can fully answer in one step\n"
    "\n"
    'Reply with "delegate" if the user\'s message requires:\n'
    "- Multi-step research across multiple sources\n"
    "- Software development, code generation, architecture, or debugging\n"
    "- Complex analysis requiring structured specialist workflows\n"
    "- Coordination between multiple specialists (researcher+writer, developer+tester)\n"
    "\n"
    'Output ONLY the single word "direct" or "delegate". Nothing else.'
)

_COMPLEX_DELEGATION_MARKERS = (
    "implement",
    "code",
    "debug",
    "fix ",
    "fix the bug",
    "refactor",
    "write a function",
    "write a python",
    "write a ",
    "build a",
    "architecture",
    "database",
    "endpoint",
    "api",
    "test ",
    "test this",
    "write tests",
    "research ",
    "research deeply",
    "investigate",
    "analyze in depth",
    "compare and recommend",
    "step by step plan",
    "full report",
    "tell me",
    "give me",
    "biography",
)

_FAST_DIRECT_PATTERNS = (
    "summarize",
    "rephrase",
    "rewrite",
    "translate",
    "shorten",
    "make this",
    "make it",
    "improve this sentence",
    "fix grammar",
    "polish this",
)

_NORMAL_CHAT_DELEGATION_NOTICE = "Transferring your work to an expert. This may take a bit longer."
_WAITING_INPUT_ROUTE = "waiting_input_prompt"
_CLASSIC_ACTIVE_WORK_PREFIX = "cwork"
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
        self._conversation_layer = ConversationLayerSupport()

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
        """Classify a message as 'direct' or 'delegate'."""
        if self._should_handle_directly(message, work_id=work_id):
            debug_log(
                "triage_heuristic",
                {
                    "message": message,
                    "decision": "direct",
                    "reason": (
                        "work_trivial_request"
                        if work_id is not None
                        else "normal_chat_fast_request"
                    ),
                    "work_id": work_id,
                },
            )
            return "direct"
        if self.manager is not None:
            debug_log(
                "triage_heuristic",
                {
                    "message": message,
                    "decision": "delegate",
                    "reason": (
                        "work_non_trivial_request"
                        if work_id is not None
                        else "normal_chat_complex_request"
                    ),
                    "work_id": work_id,
                },
            )
            return "delegate"
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

    @staticmethod
    def _is_simple_direct_request(message: str) -> bool:
        text = message.strip().lower()
        if not text:
            return True
        greeting_markers = {
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "good morning",
            "good evening",
            "how are you",
        }
        if text in greeting_markers:
            return True
        if any(text.startswith(f"{marker} ") for marker in greeting_markers):
            return True
        normalized = text.replace(" ", "")
        if re.fullmatch(r"[0-9+\-*/().]+", normalized):
            return True
        return False

    @classmethod
    def _is_fast_direct_request(cls, message: str) -> bool:
        text = message.strip().lower()
        if cls._is_simple_direct_request(message):
            return True
        if cls._is_complex_request(message):
            return False
        if len(text) <= 220 and any(pattern in text for pattern in _FAST_DIRECT_PATTERNS):
            return True
        if len(text) <= 160 and text.startswith(("what is ", "who is ", "when is ", "where is ")):
            return True
        return False

    @classmethod
    def _is_complex_request(cls, message: str) -> bool:
        text = message.strip().lower()
        if not text:
            return False
        if any(marker in text for marker in _COMPLEX_DELEGATION_MARKERS):
            return True
        if text.count(" and ") >= 2 and len(text) > 120:
            return True
        if len(text) > 280:
            return True
        return False

    @classmethod
    def _should_handle_directly(cls, message: str, *, work_id: str | None) -> bool:
        if work_id is not None:
            return cls._is_simple_direct_request(message)
        if cls._is_fast_direct_request(message):
            return True
        return not cls._is_complex_request(message)

    @staticmethod
    def _summarize_work_title(message: str) -> str:
        cleaned = " ".join(message.strip().split())
        if not cleaned:
            return "Active Task"
        return f"Active Task: {cleaned[:60]}"

    @classmethod
    def _is_related_to_active_work(cls, message: str, active_work: dict[str, str | None]) -> bool:
        lowered = message.strip().lower()
        if not lowered:
            return False
        referential_phrases = (
            "continue",
            "update",
            "revise",
            "rewrite",
            "rephrase",
            "fix that",
            "fix this",
            "the above",
            "the report",
            "the code",
            "add tests",
            "make it",
            "make this",
        )
        if any(marker in lowered for marker in referential_phrases):
            return True
        message_tokens = set(re.findall(r"[a-z0-9]+", lowered))
        if {"it", "this", "that"} & message_tokens:
            return True
        summary = str(active_work.get("summary") or "").lower()
        if not summary:
            return False
        summary_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", summary)
            if len(token) >= 5 and token not in {"which", "their", "there", "about"}
        }
        return len(summary_tokens & message_tokens) >= 2

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
            deleted_session_ids = self.store.delete_work(str(existing["work_id"]))
            for deleted_session_id in deleted_session_ids:
                self.runtime.clear_session_state(deleted_session_id)
            delete_shared_workspace(str(existing["work_id"]))
            self.store.log_event(
                "classic_active_work_replaced",
                {"session_id": session_id, "replaced_work_id": existing["work_id"]},
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
        reply = self._chat_internal(
            str(active_work["work_session_id"]),
            message,
            work_id=str(active_work["work_id"]),
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

            if active_work is not None and self._is_related_to_active_work(message, active_work):
                return self._chat_in_active_work(
                    session_id,
                    message,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                )

            if (
                self.manager is not None
                and self._triage_message(message, work_id=None) == "delegate"
            ):
                active_work = self._replace_classic_active_work(session_id, message)
                return self._chat_in_active_work(
                    session_id,
                    message,
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
                    work_id=work_id,
                    model_id=model_id,
                )
                try:
                    followup_artifacts = self._resolve_targeted_writer_followup_artifacts(
                        session_id=session_id,
                        work_id=work_id,
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
                run_result = self.runtime.run(
                    session_id,
                    message,
                    request_id=request_id,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
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
            response_metadata = {
                "runtime": str(runtime_meta.get("runtime") or "maf_agent"),
                "core_agent_backend": str(runtime_meta.get("backend") or backend or "local"),
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
            }
            run_metadata = getattr(run_result, "metadata", None) if run_result is not None else None
            if isinstance(run_metadata, dict):
                response_metadata.update(run_metadata)
            conversation_result = None
            if manager_result is None and response_mode == "direct_answer":
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
            messages=response_messages,
            metadata=response_metadata,
        )
        self.store.log_event(
            "chat_response",
            {
                "request_id": request_id,
                "session_id": session_id,
                "work_id": work_id,
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
            model_id=(self.store.get_runtime_config() or {}).get("model_id"),
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
