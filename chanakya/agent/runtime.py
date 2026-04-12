from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import Agent, AgentResponse, Message
from agent_framework.openai import OpenAIChatClient
from sqlalchemy.orm import Session, sessionmaker

from chanakya.agent.prompt import inject_tools_into_prompt
from chanakya.agent.profile_files import load_agent_prompt
from chanakya.config import (
    get_a2a_agent_url,
    get_agent_request_timeout_seconds,
    get_core_agent_backend,
    get_openai_compatible_config,
)
from chanakya.debug import debug_log
from chanakya.history_provider import SQLAlchemyHistoryProvider
from chanakya.mcp_runtime import ToolExecutionTrace, extract_tool_execution_traces
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import AgentSessionContextRepository
from chanakya.services.tool_loader import get_cached_tools, get_tools_availability


@dataclass(slots=True)
class ProfileAgentConfig:
    system_prompt: str
    cached_tools: list[Any]
    availability: list[dict[str, str]]


def normalize_runtime_backend(backend: str | None) -> str:
    value = str(backend or "").strip().lower()
    if value in {"local", "a2a"}:
        return value
    return "local"


def create_openai_chat_client(
    *,
    model_id: str | None = None,
    env_file_path: str = ".env",
) -> OpenAIChatClient:
    cfg = get_openai_compatible_config()
    return OpenAIChatClient(
        model_id=model_id or cfg.get("model"),
        api_key=cfg.get("api_key"),
        base_url=cfg.get("base_url"),
        env_file_path=env_file_path,
    )


def build_profile_agent_config(profile: AgentProfileModel) -> ProfileAgentConfig:
    return build_profile_agent_config_for_usage(
        profile,
        usage_text="",
        repo_root=Path(__file__).resolve().parents[2],
    )


def build_profile_agent_config_for_usage(
    profile: AgentProfileModel,
    *,
    usage_text: str = "",
    repo_root: Path | None = None,
) -> ProfileAgentConfig:
    availability = get_tools_availability()
    all_cached = get_cached_tools()
    allowed_ids = list(profile.tool_ids_json or [])
    cached_tools = [t for t in all_cached if getattr(t, "name", None) in allowed_ids]
    root = repo_root or Path(__file__).resolve().parents[2]
    profile_prompt = load_agent_prompt(profile, repo_root=root, usage_text=usage_text)
    system_prompt = inject_tools_into_prompt(profile, cached_tools, base_prompt=profile_prompt)
    return ProfileAgentConfig(
        system_prompt=system_prompt,
        cached_tools=cached_tools,
        availability=availability,
    )


def build_profile_agent(
    profile: AgentProfileModel,
    session_factory: sessionmaker[Session],
    *,
    client: OpenAIChatClient | None = None,
    env_file_path: str = ".env",
    include_history: bool = False,
    store_inputs: bool = True,
    store_outputs: bool = True,
    usage_text: str = "",
    repo_root: Path | None = None,
) -> tuple[Agent, ProfileAgentConfig]:
    config = build_profile_agent_config_for_usage(
        profile,
        usage_text=usage_text,
        repo_root=repo_root,
    )
    context_providers = None
    if include_history:
        context_providers = [
            SQLAlchemyHistoryProvider(
                session_factory=session_factory,
                load_messages=True,
                store_inputs=store_inputs,
                store_outputs=store_outputs,
            )
        ]
    agent = Agent(
        client=client or create_openai_chat_client(env_file_path=env_file_path),
        name=profile.name,
        instructions=config.system_prompt,
        tools=config.cached_tools or None,
        context_providers=context_providers,
    )
    return agent, config


class RunResult:
    """Container for the output of a single agent run."""

    __slots__ = ("text", "tool_traces", "availability", "response_mode", "metadata")

    def __init__(
        self,
        *,
        text: str,
        tool_traces: list[ToolExecutionTrace],
        availability: list[dict[str, str]],
        response_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.text = text
        self.tool_traces = tool_traces
        self.availability = availability
        self.response_mode = response_mode
        self.metadata = metadata or {}


class MAFRuntime:
    """Unified MAF runtime executing on persistent shared background MCP connections."""

    def __init__(
        self,
        profile: AgentProfileModel,
        session_factory: sessionmaker[Session],
        env_file_path: str = ".env",
        a2a_agent_factory: Any | None = None,
    ) -> None:
        self.profile = profile
        self.repo_root = Path(__file__).resolve().parents[2]
        self.env_file_path = env_file_path
        self.client = OpenAIChatClient(env_file_path=env_file_path)
        self.session_factory = session_factory
        self.history_provider = SQLAlchemyHistoryProvider(
            session_factory=session_factory,
            load_messages=True,
            store_inputs=False,
            store_outputs=False,
        )
        self.session_context_store = AgentSessionContextRepository(session_factory)
        self.default_backend = get_core_agent_backend()
        self.a2a_agent_url = get_a2a_agent_url()
        self.a2a_agent_factory = a2a_agent_factory
        self._a2a_agent: Any | None = None
        self._a2a_sessions: dict[str, Any] = {}
        self._a2a_remote_context_by_session: dict[str, str] = {}
        self._a2a_session_sequence = 0
        self.agent, config = build_profile_agent(
            profile,
            session_factory,
            client=self.client,
            env_file_path=env_file_path,
            include_history=True,
            store_inputs=False,
            store_outputs=False,
            usage_text="",
            repo_root=self.repo_root,
        )
        self.availability = config.availability
        self.cached_tools = config.cached_tools

        debug_log(
            "maf_runtime_initialized",
            {
                "agent_name": profile.name,
                "history_provider": "sqlalchemy",
                "role": profile.role,
                "model": self.runtime_metadata().get("model"),
                "endpoint": self.runtime_metadata().get("endpoint"),
                "backend": self.default_backend,
                "tool_specs": list(profile.tool_ids_json or []),
            },
        )

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> RunResult:
        """Run the agent, bridging Sync Flask to Background Async Event Loop."""
        return run_in_maf_loop(
            self._run_async_in_loop(
                session_id,
                text,
                request_id=request_id,
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )
        )

    def clear_session_state(self, session_id: str) -> None:
        self.session_context_store.delete(session_id)
        for key in list(self._a2a_remote_context_by_session.keys()):
            if key.endswith(f":{session_id}"):
                self._a2a_remote_context_by_session.pop(key, None)
        for key in list(self._a2a_sessions.keys()):
            if key.endswith(f":{session_id}"):
                self._a2a_sessions.pop(key, None)

    async def _run_async_in_loop(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> RunResult:
        selected_backend = normalize_runtime_backend(backend or self.default_backend)
        if selected_backend == "a2a":
            return await self._run_async_a2a_in_loop(
                session_id,
                text,
                request_id=request_id,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
            )

        return await self._run_async_local_in_loop(
            session_id,
            text,
            request_id=request_id,
            model_id=model_id,
        )

    async def _run_async_local_in_loop(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
    ) -> RunResult:
        tool_traces: list[ToolExecutionTrace] = []

        run_client = self.client if not model_id else create_openai_chat_client(model_id=model_id)

        debug_log(
            "maf_runtime_before_run",
            {
                "session_id": session_id,
                "request_id": request_id,
                "input": text,
                "tool_count": len(self.cached_tools),
            },
        )

        try:
            response = await self._run_local_agent(
                session_id=session_id,
                request_id=request_id,
                prompt_text=text,
                client=run_client,
                include_history=True,
                history_query_text=text,
            )
            local_fallback_used = False
        except Exception as exc:
            if not self._is_missing_user_query_error(exc):
                raise
            seeded_prompt = await self._build_seeded_history_prompt(
                session_id=session_id,
                user_text=text,
            )
            response = await self._run_local_agent(
                session_id=session_id,
                request_id=request_id,
                prompt_text=seeded_prompt,
                client=run_client,
                include_history=False,
                history_query_text=text,
            )
            local_fallback_used = True

        # Mock up specs format to satisfy legacy extractor
        class _MockSpec:
            def __init__(self, t):
                self.id = getattr(t, "name")
                self.name = getattr(t, "name")
                self.server_name = getattr(
                    t, "server_name", getattr(t, "name", "cached_mcp_server")
                )

        mock_specs = [_MockSpec(t) for t in self.cached_tools]

        tool_traces = extract_tool_execution_traces(response, mock_specs)

        reply_text = str(response).strip()
        debug_log(
            "maf_runtime_after_run",
            {
                "session_id": session_id,
                "request_id": request_id,
                "raw_result": reply_text,
                "tool_trace_count": len(tool_traces),
            },
        )

        response_mode = "tool_assisted" if tool_traces else "direct_answer"

        return RunResult(
            text=reply_text,
            tool_traces=tool_traces,
            availability=self.availability,
            response_mode=response_mode,
            metadata={
                "core_agent_backend": "local",
                "local_seeded_history_fallback": local_fallback_used,
            },
        )

    async def _run_local_agent(
        self,
        *,
        session_id: str,
        request_id: str,
        prompt_text: str,
        client: OpenAIChatClient,
        include_history: bool,
        history_query_text: str,
    ) -> AgentResponse[Any]:
        run_agent, _ = build_profile_agent(
            self.profile,
            self.session_factory,
            client=client,
            include_history=include_history,
            store_inputs=False,
            store_outputs=False,
            usage_text=prompt_text,
            repo_root=self.repo_root,
        )
        session = run_agent.create_session(session_id=session_id)
        session.state["request_id"] = request_id
        session.state["history_query_text"] = history_query_text
        return await asyncio.wait_for(
            run_agent.run(
                Message(
                    role="user",
                    text=prompt_text,
                    additional_properties={"request_id": request_id},
                ),
                session=session,
                options={"store": True},
            ),
            timeout=get_agent_request_timeout_seconds(),
        )

    async def _run_async_a2a_in_loop(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> RunResult:
        selected_url = str(a2a_url or self.a2a_agent_url or "").strip()
        agent = self._get_a2a_agent(selected_url)
        session = self._create_a2a_ephemeral_session(session_id, selected_url)
        target_key = self._a2a_target_key(selected_url)
        prompt = self._build_a2a_prompt(
            text=text,
            remote_agent=a2a_remote_agent,
            model_provider=a2a_model_provider,
            model_id=a2a_model_id,
            ephemeral_session=True,
        )
        option_header, clean_prompt = self._split_a2a_option_header(prompt)
        prompt_for_run = await self._build_seeded_history_prompt(
            session_id=session_id,
            user_text=clean_prompt,
        )
        if option_header:
            prompt_for_run = f"{option_header}\n{prompt_for_run}"

        debug_log(
            "maf_runtime_before_a2a_run",
            {
                "session_id": session_id,
                "request_id": request_id,
                "input": text,
                "has_remote_context": False,
                "a2a_url": selected_url,
                "a2a_remote_agent": a2a_remote_agent,
                "a2a_model_provider": a2a_model_provider,
                "a2a_model_id": a2a_model_id,
                "a2a_session_id": getattr(session, "session_id", None),
            },
        )

        response = await asyncio.wait_for(
            agent.run(
                self._build_a2a_messages(text=prompt_for_run, remote_context_id=None),
                session=session,
            ),
            timeout=get_agent_request_timeout_seconds(),
        )
        continuity_mode = "seeded_history"
        fallback_used = False

        reply_text = self._extract_a2a_response_text(response)
        new_context_id = self._extract_a2a_context_id(response)
        self.session_context_store.save(
            session_id,
            backend="a2a",
            remote_context_id=new_context_id,
            remote_agent_url=selected_url,
            target_key=target_key,
        )

        debug_log(
            "maf_runtime_after_a2a_run",
            {
                "session_id": session_id,
                "request_id": request_id,
                "raw_result": reply_text,
                "remote_context_id": new_context_id,
                "continuity_mode": continuity_mode,
                "fallback_used": fallback_used,
                "a2a_url": selected_url,
                "a2a_session_id": getattr(session, "session_id", None),
            },
        )

        return RunResult(
            text=reply_text,
            tool_traces=[],
            availability=self.availability,
            response_mode="direct_answer",
            metadata={
                "core_agent_backend": "a2a",
                "remote_context_id": new_context_id,
                "a2a_continuity_mode": continuity_mode,
                "a2a_fallback_used": fallback_used,
                "a2a_url": selected_url,
                "a2a_remote_agent": str(a2a_remote_agent or "").strip() or None,
                "a2a_model_provider": str(a2a_model_provider or "").strip() or None,
                "a2a_model_id": str(a2a_model_id or "").strip() or None,
                "a2a_session_id": getattr(session, "session_id", None),
            },
        )

    def _get_a2a_agent(self, selected_url: str) -> Any:
        if not selected_url:
            raise RuntimeError("A2A backend selected but A2A_AGENT_URL is not configured")
        if not isinstance(self._a2a_agent, dict):
            self._a2a_agent = {}
        cached = self._a2a_agent.get(selected_url)
        if cached is not None:
            return cached
        factory = self.a2a_agent_factory
        if factory is None:
            from agent_framework_a2a import A2AAgent

            factory = A2AAgent
        self._a2a_agent[selected_url] = factory(
            name=f"{self.profile.name} A2A",
            description="Remote A2A-backed Chanakya agent.",
            url=selected_url,
        )
        return self._a2a_agent[selected_url]

    def _create_a2a_ephemeral_session(self, session_id: str, selected_url: str) -> Any:
        self._a2a_session_sequence += 1
        scoped_session_id = f"a2a:{selected_url}:{session_id}:seeded:{self._a2a_session_sequence}"
        return self._get_a2a_agent(selected_url).create_session(session_id=scoped_session_id)

    @staticmethod
    def _a2a_context_key(session_id: str, selected_url: str) -> str:
        return f"{selected_url}:{session_id}"

    @staticmethod
    def _a2a_target_key(selected_url: str) -> str:
        return f"a2a::{selected_url}"

    @staticmethod
    def _build_a2a_messages(*, text: str, remote_context_id: str | None) -> list[Message]:
        additional_properties: dict[str, Any] = {}
        if remote_context_id:
            additional_properties["context_id"] = remote_context_id
        return [Message(role="user", text=text, additional_properties=additional_properties)]

    @staticmethod
    def _build_a2a_prompt(
        *,
        text: str,
        remote_agent: str | None,
        model_provider: str | None,
        model_id: str | None,
        ephemeral_session: bool = False,
    ) -> str:
        selected_remote_agent = str(remote_agent or "").strip()
        selected_model_provider = str(model_provider or "").strip()
        selected_model_id = str(model_id or "").strip()
        header_parts: list[str] = []
        if selected_remote_agent:
            header_parts.append(f"agent={selected_remote_agent}")
        if selected_model_provider and selected_model_id:
            header_parts.append(f"model_provider={selected_model_provider}")
            header_parts.append(f"model_id={selected_model_id}")
        if ephemeral_session:
            header_parts.append("ephemeral_session=true")
        if not header_parts:
            return text
        return f"[[opencode-options:{';'.join(header_parts)}]]\n{text}"

    @staticmethod
    def _split_a2a_option_header(text: str) -> tuple[str, str]:
        normalized = text or ""
        if not normalized.startswith("[[opencode-options:") or "]]" not in normalized:
            return "", normalized
        header, remainder = normalized.split("]]", 1)
        return f"{header}]]", remainder.lstrip("\n")

    async def _build_seeded_history_prompt(self, *, session_id: str, user_text: str) -> str:
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
    def _is_missing_user_query_error(exc: Exception) -> bool:
        return "no user query found in messages" in str(exc).lower()

    @staticmethod
    def _extract_a2a_response_text(response: Any) -> str:
        text = str(getattr(response, "text", "") or "").strip()
        if text:
            return text
        value = str(getattr(response, "value", "") or "").strip()
        if value:
            return value
        raw = getattr(response, "raw_representation", None)
        extracted = MAFRuntime._extract_text_from_a2a_payload(raw)
        if extracted:
            return extracted
        return str(response).strip()

    @staticmethod
    def _extract_text_from_a2a_payload(payload: Any) -> str:
        texts: list[str] = []

        def collect(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    texts.append(stripped)
                return
            if isinstance(value, dict):
                part_type = str(value.get("type") or "").strip().lower()
                if part_type == "text":
                    collect(value.get("text"))
                    return
                if "text" in value and len(value) == 1:
                    collect(value.get("text"))
                    return
                for key in ("parts", "artifacts", "root", "content", "message", "messages"):
                    if key in value:
                        collect(value.get(key))
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)
                return
            for attr in ("parts", "artifacts", "root", "content", "message", "messages", "text"):
                nested = getattr(value, attr, None)
                if nested is not None and nested is not value:
                    collect(nested)

        collect(payload)
        return "\n".join(dict.fromkeys(texts)).strip()

    @staticmethod
    def _extract_a2a_context_id(response: Any) -> str | None:
        raw = getattr(response, "raw_representation", None)
        if isinstance(raw, list):
            for item in reversed(raw):
                context_id = getattr(item, "context_id", None)
                if context_id:
                    return str(context_id)
        if isinstance(raw, dict):
            context_id = raw.get("context_id")
            if context_id:
                return str(context_id)
        context_id = getattr(raw, "context_id", None)
        if context_id:
            return str(context_id)
        return None

    @staticmethod
    def runtime_metadata(
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> dict[str, str | None]:
        selected_backend = normalize_runtime_backend(backend or get_core_agent_backend())
        cfg = get_openai_compatible_config()
        if selected_backend == "a2a":
            return {
                "model": str(a2a_model_id or "").strip() or None,
                "a2a_model_id": str(a2a_model_id or "").strip() or None,
                "endpoint": str(a2a_url or get_a2a_agent_url() or "").strip() or None,
                "runtime": "maf_agent",
                "backend": "a2a",
                "a2a_remote_agent": str(a2a_remote_agent or "").strip() or None,
                "a2a_model_provider": str(a2a_model_provider or "").strip() or None,
            }
        return {
            "model": model_id or cfg.get("model"),
            "endpoint": cfg.get("base_url"),
            "runtime": "maf_agent",
            "backend": "local",
        }
