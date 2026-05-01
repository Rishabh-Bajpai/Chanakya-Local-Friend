from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, Message, tool
from agent_framework.openai import OpenAIChatClient

from conversation_layer.schemas import ChatRequest, ChatResponse
from conversation_layer.services.agent_interface import AgentInterface
from core_agent_app.services.agent_session_context import (
    SQLAlchemyAgentSessionContextStore,
)
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider
from core_agent_app.services.opencode_discovery import discover_opencode_options
from core_agent_app.services.tools import fetch_url, get_time, search_web


CORE_AGENT_INSTRUCTIONS = (
    "You are the core agent for the Chanakya app. "
    "Answer the user's request directly and use available tools when needed. "
    "When using web information, prefer searching first and then fetching the most relevant URL. "
    "Use recent conversation history to resolve shorthand, elliptical, or referential follow-ups whenever the intended meaning is reasonably clear from context. "
    "Examples include messages like '+6?', 'add 4 to it', 'subtract 2 from that', or similar follow-ups that refer to the most recent result or subject. "
    "Only ask for clarification when the reference is genuinely ambiguous after considering the recent conversation."
)


@tool(description="Get the current UTC time.")
async def get_time_tool() -> str:
    return get_time()


@tool(description="Fetch a URL and return readable page text.")
async def fetch_url_tool(url: str) -> str:
    return fetch_url(url)


@tool(description="Search the web and return concise results with URLs.")
async def search_web_tool(query: str) -> str:
    return search_web(query)


class CoreAgentAdapter(AgentInterface):
    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        raise NotImplementedError


@dataclass(slots=True)
class BackendTargetConfig:
    key: str
    backend: str
    label: str
    description: str
    adapter: CoreAgentAdapter
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentFrameworkCoreAgentAdapter(CoreAgentAdapter):
    model: str
    base_url: str
    api_key: str
    debug: bool
    env_file_path: str
    history_provider: SQLAlchemyHistoryProvider

    def __post_init__(self) -> None:
        self._client = OpenAIChatClient(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            env_file_path=self.env_file_path if os.path.exists(self.env_file_path) else None,
        )
        self._agent = Agent(
            client=self._client,
            name="ChanakyaCoreAgent",
            description="Core agent for the Chanakya app.",
            instructions=CORE_AGENT_INSTRUCTIONS,
            tools=[get_time_tool, search_web_tool, fetch_url_tool],
            context_providers=[self.history_provider],
        )

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        try:
            agent_response = asyncio.run(self._run_agent(chat_request))
            response_text = (
                agent_response.text or ""
            ).strip() or "I couldn't generate a response."
            return ChatResponse(
                session_id=chat_request.session_id,
                response=response_text,
                metadata={
                    "source": "agent_framework",
                    "history_provider": self.history_provider.source_id,
                },
            )
        except Exception as exc:  # pragma: no cover
            if not self.debug:
                raise
            return ChatResponse(
                session_id=chat_request.session_id,
                response=f"Agent framework error: {exc}",
                metadata={
                    "source": "agent_framework_error",
                    "history_provider": self.history_provider.source_id,
                },
            )

    async def _run_agent(self, chat_request: ChatRequest):
        session = self._agent.create_session(session_id=chat_request.session_id)
        prompt = self._build_agent_prompt(chat_request)
        return await self._agent.run([Message("user", [prompt])], session=session)

    def _build_agent_prompt(self, chat_request: ChatRequest) -> str:
        return chat_request.message

    def get_debug_state(self, session_id: str) -> dict:
        return {
            "adapter_name": type(self).__name__,
            "session_id": session_id,
            "framework": "agent_framework",
            "model": self.model,
            "history_provider": self.history_provider.source_id,
        }


@dataclass(slots=True)
class A2ACoreAgentAdapter(CoreAgentAdapter):
    url: str
    debug: bool
    history_provider: SQLAlchemyHistoryProvider
    session_context_store: SQLAlchemyAgentSessionContextStore
    a2a_agent_factory: Any | None = None
    target_key: str = "default"
    target_label: str = "A2A"
    continuity_strategy: str = "auto"
    default_remote_agent: str | None = None
    default_model_provider: str | None = None
    default_model_id: str | None = None

    def __post_init__(self) -> None:
        if self.a2a_agent_factory is None:
            from agent_framework_a2a import A2AAgent

            self.a2a_agent_factory = A2AAgent
        self._agent = self._build_agent()
        self._sessions: dict[str, Any] = {}
        self._last_attempt_state: dict[str, dict[str, Any]] = {}
        self._session_sequence = 0

    def _build_agent(self):
        return self.a2a_agent_factory(
            name="ChanakyaRemoteA2AAgent",
            description="Remote A2A-backed core agent for the Chanakya app.",
            url=self.url,
        )

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        selected_remote_agent = str(
            (chat_request.metadata or {}).get("a2a_remote_agent")
            or self.default_remote_agent
            or ""
        ).strip()
        selected_model_provider = str(
            (chat_request.metadata or {}).get("a2a_model_provider")
            or self.default_model_provider
            or ""
        ).strip()
        selected_model_id = str(
            (chat_request.metadata or {}).get("a2a_model_id")
            or self.default_model_id
            or ""
        ).strip()
        try:
            result = asyncio.run(self._run_agent(chat_request))
            self.session_context_store.save(
                chat_request.session_id,
                backend="a2a",
                remote_context_id=result["remote_context_id"],
                remote_agent_url=self.url,
                target_key=self.target_key,
            )
            self._last_attempt_state[chat_request.session_id] = {
                "attempted": True,
                "fallback_used": result["fallback_used"],
                "failure_reason": result["failure_reason"],
                "continuity_mode": result["continuity_mode"],
                "repaired_continuity": result["repaired_continuity"],
                "response_valid": True,
            }
            return ChatResponse(
                session_id=chat_request.session_id,
                response=result["response_text"],
                metadata={
                    "source": "agent_framework_a2a",
                    "history_provider": self.history_provider.source_id,
                    "remote_context_id": result["remote_context_id"],
                    "core_agent_backend": "a2a",
                    "core_agent_target": self.target_key,
                    "core_agent_target_label": self.target_label,
                    "a2a_attempted": True,
                    "a2a_target": self.target_key,
                    "a2a_remote_url": self.url,
                    "a2a_remote_agent": selected_remote_agent or None,
                    "a2a_model_provider": selected_model_provider or None,
                    "a2a_model_id": selected_model_id or None,
                    "a2a_fallback_used": result["fallback_used"],
                    "a2a_failure_reason": result["failure_reason"],
                    "a2a_continuity_mode": result["continuity_mode"],
                    "a2a_repaired_continuity": result["repaired_continuity"],
                },
            )
        except Exception as exc:  # pragma: no cover
            self._last_attempt_state[chat_request.session_id] = {
                "attempted": True,
                "fallback_used": False,
                "failure_reason": self._classify_a2a_exception(exc),
                "continuity_mode": "failed",
                "repaired_continuity": False,
                "response_valid": False,
            }
            if not self.debug:
                raise
            return ChatResponse(
                session_id=chat_request.session_id,
                response=f"Agent framework A2A error: {exc}",
                metadata={
                    "source": "agent_framework_a2a_error",
                    "history_provider": self.history_provider.source_id,
                    "core_agent_backend": "a2a",
                    "core_agent_target": self.target_key,
                    "core_agent_target_label": self.target_label,
                    "a2a_attempted": True,
                    "a2a_target": self.target_key,
                    "a2a_remote_url": self.url,
                    "a2a_remote_agent": selected_remote_agent or None,
                    "a2a_model_provider": selected_model_provider or None,
                    "a2a_model_id": selected_model_id or None,
                    "a2a_fallback_used": False,
                    "a2a_failure_reason": self._classify_a2a_exception(exc),
                    "a2a_continuity_mode": "failed",
                },
            )

    async def _run_agent(self, chat_request: ChatRequest) -> dict[str, Any]:
        agent = self._agent
        if self.continuity_strategy == "seeded_history":
            agent = self._build_agent()
            self._agent = agent
        session = self._get_session(chat_request.session_id, agent=agent)
        agent_context = self.session_context_store.get(
            chat_request.session_id,
            target_key=self.target_key,
        )
        prompt = self._build_agent_prompt(chat_request)
        stored_context_id = agent_context.get("remote_context_id")
        if self.continuity_strategy == "seeded_history":
            options_header, clean_prompt = self._split_option_header(prompt)
            seeded_prompt = await self._build_seeded_prompt(
                chat_request.session_id,
                clean_prompt,
            )
            if options_header:
                seeded_prompt = f"{options_header}\n{seeded_prompt}"
            response = await agent.run(
                self._build_messages(user_text=seeded_prompt, remote_context_id=None),
                session=session,
            )
            response_text = self._extract_response_text(response)
            return {
                "response_text": response_text,
                "remote_context_id": None,
                "fallback_used": False,
                "failure_reason": None,
                "continuity_mode": "seeded_history",
                "repaired_continuity": False,
            }
        messages = self._build_messages(
            user_text=prompt,
            remote_context_id=stored_context_id,
        )
        fallback_used = False
        failure_reason = None
        repaired_continuity = False
        try:
            response = await agent.run(messages, session=session)
            response_text = self._extract_response_text(response)
            continuity_mode = "remote_context" if stored_context_id else "direct"
        except Exception as exc:
            failure_reason = self._classify_a2a_exception(exc)
            options_header, clean_prompt = self._split_option_header(prompt)
            fallback_prompt = await self._build_seeded_prompt(
                chat_request.session_id,
                clean_prompt,
            )
            if options_header:
                fallback_prompt = f"{options_header}\n{fallback_prompt}"
            fallback_used = True
            response = await agent.run(
                self._build_messages(user_text=fallback_prompt, remote_context_id=None),
                session=session,
            )
            response_text = self._extract_response_text(response)
            continuity_mode = "seeded_history"
            repaired_continuity = bool(stored_context_id)
        remote_context_id = self._extract_context_id(response) or stored_context_id
        return {
            "response_text": response_text,
            "remote_context_id": remote_context_id,
            "fallback_used": fallback_used,
            "failure_reason": failure_reason,
            "continuity_mode": continuity_mode,
            "repaired_continuity": repaired_continuity,
        }

    def _get_session(self, session_id: str, *, agent: Any):
        if self.continuity_strategy == "seeded_history":
            self._session_sequence += 1
            return agent.create_session(
                session_id=(
                    f"{self.target_key}:{session_id}:seeded:{self._session_sequence}"
                )
            )
        session_key = f"{self.target_key}:{session_id}"
        if session_key not in self._sessions:
            self._sessions[session_key] = agent.create_session(session_id=session_key)
        return self._sessions[session_key]

    def _build_messages(
        self, *, user_text: str, remote_context_id: str | None
    ) -> list[Message]:
        additional_properties = {}
        if remote_context_id:
            additional_properties["context_id"] = remote_context_id
        message = Message("user", [user_text])
        if additional_properties:
            message.additional_properties = additional_properties
        return [message]

    async def _build_seeded_prompt(self, session_id: str, user_text: str) -> str:
        history = await self.history_provider.get_messages(session_id)
        chunks = [
            "Continue this conversation using the transcript excerpt below.",
            "Resolve shorthand or referential follow-ups from the transcript when the meaning is reasonably clear, and only ask for clarification if the reference is genuinely ambiguous.",
        ]
        for item in history[-12:]:
            role = "User" if item.role == "user" else "Assistant"
            chunks.append(f"{role}: {item.text}")
        chunks.append(f"User: {user_text}")
        return "\n".join(chunks)

    @staticmethod
    def _extract_context_id(response: Any) -> str | None:
        raw = getattr(response, "raw_representation", None)
        if isinstance(raw, list):
            for item in reversed(raw):
                context_id = getattr(item, "context_id", None)
                if context_id:
                    return context_id
        return getattr(raw, "context_id", None)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        response_text = getattr(response, "text", None) or getattr(
            response, "value", None
        )
        if isinstance(response_text, str) and response_text.strip():
            return response_text
        raise ValueError("empty_or_malformed_a2a_response")

    @staticmethod
    def _classify_a2a_exception(exc: Exception) -> str:
        lowered = type(exc).__name__.lower() + ":" + str(exc).lower()
        if "timeout" in lowered:
            return "timeout"
        if "empty_or_malformed_a2a_response" in lowered or "malformed" in lowered:
            return "malformed_response"
        return "transport_failure"

    def _build_agent_prompt(self, chat_request: ChatRequest) -> str:
        metadata = chat_request.metadata or {}
        remote_agent = str(
            metadata.get("a2a_remote_agent") or self.default_remote_agent or ""
        ).strip()
        model_provider = str(
            metadata.get("a2a_model_provider") or self.default_model_provider or ""
        ).strip()
        model_id = str(
            metadata.get("a2a_model_id") or self.default_model_id or ""
        ).strip()
        header_parts = []
        if remote_agent:
            header_parts.append(f"agent={remote_agent}")
        if model_provider and model_id:
            header_parts.append(f"model_provider={model_provider}")
            header_parts.append(f"model_id={model_id}")
        if self.continuity_strategy == "seeded_history":
            header_parts.append("ephemeral_session=true")
        if not header_parts:
            return chat_request.message
        return f"[[opencode-options:{';'.join(header_parts)}]]\n{chat_request.message}"

    @staticmethod
    def _split_option_header(text: str) -> tuple[str, str]:
        normalized = text or ""
        if not normalized.startswith("[[opencode-options:") or "]]" not in normalized:
            return "", normalized
        header, remainder = normalized.split("]]", 1)
        return f"{header}]]", remainder.lstrip("\n")

    def get_debug_state(self, session_id: str) -> dict:
        return {
            "adapter_name": type(self).__name__,
            "session_id": session_id,
            "framework": "agent_framework_a2a",
            "url": self.url,
            "target_key": self.target_key,
            "target_label": self.target_label,
            "continuity_strategy": self.continuity_strategy,
            "history_provider": self.history_provider.source_id,
            "capability_limitations": {
                "remote_history": "not guaranteed",
                "remote_tools": "not guaranteed",
                "remote_memory": "context_id only when exposed",
                "streaming": False,
            },
            "session_context": self.session_context_store.get(
                session_id,
                target_key=self.target_key,
            ),
            "last_attempt": self._last_attempt_state.get(session_id, {}),
        }


@dataclass(slots=True)
class RoutedCoreAgentAdapter(CoreAgentAdapter):
    targets: dict[tuple[str, str], BackendTargetConfig]
    default_backend: str
    default_target: str

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        target = self._resolve_target(chat_request)
        response = target.adapter.respond(chat_request)
        response.metadata.setdefault("core_agent_backend", target.backend)
        response.metadata.setdefault("core_agent_target", target.key)
        response.metadata.setdefault("core_agent_target_label", target.label)
        if target.metadata.get("url"):
            response.metadata.setdefault(
                "core_agent_target_url", target.metadata["url"]
            )
        return response

    def get_debug_state(self, session_id: str) -> dict:
        return {
            "adapter_name": type(self).__name__,
            "session_id": session_id,
            "default_backend": self.default_backend,
            "default_target": self.default_target,
            "targets": [
                {
                    "backend": config.backend,
                    "key": config.key,
                    "label": config.label,
                    "description": config.description,
                    **config.metadata,
                }
                for _, config in sorted(self.targets.items())
            ],
        }

    def runtime_options(self) -> dict[str, Any]:
        return {
            "default_backend": self.default_backend,
            "default_target": self.default_target,
            "targets": [
                {
                    "backend": config.backend,
                    "key": config.key,
                    "label": config.label,
                    "description": config.description,
                    **self._target_runtime_metadata(config),
                }
                for _, config in sorted(self.targets.items())
            ],
        }

    def _target_runtime_metadata(self, config: BackendTargetConfig) -> dict[str, Any]:
        metadata = dict(config.metadata)
        opencode_http_url = str(metadata.get("opencode_http_url") or "").strip()
        if opencode_http_url:
            try:
                metadata.update(discover_opencode_options(opencode_http_url))
            except Exception:
                pass
        return metadata

    def _resolve_target(self, chat_request: ChatRequest) -> BackendTargetConfig:
        metadata = chat_request.metadata or {}
        backend = str(
            metadata.get("core_agent_backend") or self.default_backend
        ).strip()
        target_key = str(
            metadata.get("core_agent_target") or self.default_target
        ).strip()
        target = self.targets.get((backend, target_key))
        if target is not None:
            return target

        if metadata.get("core_agent_backend") or metadata.get("core_agent_target"):
            available = ", ".join(
                f"{item.backend}:{item.key}" for _, item in sorted(self.targets.items())
            )
            raise ValueError(
                f"Unknown core agent target '{backend}:{target_key}'. Available targets: {available}"
            )

        fallback = self.targets.get((self.default_backend, self.default_target))
        if fallback is None:
            raise ValueError("No default core agent target is configured")
        return fallback
