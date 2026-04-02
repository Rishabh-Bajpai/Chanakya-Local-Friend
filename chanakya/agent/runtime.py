from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agent_framework import Agent, AgentResponse, Message
from agent_framework.openai import OpenAIChatClient
from sqlalchemy.orm import Session, sessionmaker

from chanakya.agent.prompt import inject_tools_into_prompt
from chanakya.config import get_mcp_request_timeout_seconds, get_openai_compatible_config
from chanakya.debug import debug_log
from chanakya.history_provider import SQLAlchemyHistoryProvider
from chanakya.mcp_runtime import ToolExecutionTrace, extract_tool_execution_traces
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.services.tool_loader import get_cached_tools, get_tools_availability


@dataclass(slots=True)
class ProfileAgentConfig:
    system_prompt: str
    cached_tools: list[Any]
    availability: list[dict[str, str]]


def build_profile_agent_config(profile: AgentProfileModel) -> ProfileAgentConfig:
    availability = get_tools_availability()
    all_cached = get_cached_tools()
    allowed_ids = list(profile.tool_ids_json or [])
    cached_tools = [t for t in all_cached if getattr(t, "name", None) in allowed_ids]
    system_prompt = inject_tools_into_prompt(profile, cached_tools)
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
) -> tuple[Agent, ProfileAgentConfig]:
    config = build_profile_agent_config(profile)
    context_providers = None
    if include_history:
        context_providers = [
            SQLAlchemyHistoryProvider(
                session_factory=session_factory,
                load_messages=True,
                store_inputs=True,
                store_outputs=True,
            )
        ]
    agent = Agent(
        client=client or OpenAIChatClient(env_file_path=env_file_path),
        name=profile.name,
        instructions=config.system_prompt,
        tools=config.cached_tools or None,
        context_providers=context_providers,
    )
    return agent, config


class RunResult:
    """Container for the output of a single agent run."""

    __slots__ = ("text", "tool_traces", "availability", "response_mode")

    def __init__(
        self,
        *,
        text: str,
        tool_traces: list[ToolExecutionTrace],
        availability: list[dict[str, str]],
        response_mode: str,
    ) -> None:
        self.text = text
        self.tool_traces = tool_traces
        self.availability = availability
        self.response_mode = response_mode


class MAFRuntime:
    """Unified MAF runtime executing on persistent shared background MCP connections."""

    def __init__(
        self,
        profile: AgentProfileModel,
        session_factory: sessionmaker[Session],
        env_file_path: str = ".env",
    ) -> None:
        self.profile = profile
        self.client = OpenAIChatClient(env_file_path=env_file_path)
        self.session_factory = session_factory
        self.agent, config = build_profile_agent(
            profile,
            session_factory,
            client=self.client,
            env_file_path=env_file_path,
            include_history=True,
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
                "tool_specs": list(profile.tool_ids_json or []),
            },
        )

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
    ) -> RunResult:
        """Run the agent, bridging Sync Flask to Background Async Event Loop."""
        return run_in_maf_loop(self._run_async_in_loop(session_id, text, request_id=request_id))

    async def _run_async_in_loop(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
    ) -> RunResult:
        tool_traces: list[ToolExecutionTrace] = []

        session = self.agent.create_session(session_id=session_id)
        session.state["request_id"] = request_id

        debug_log(
            "maf_runtime_before_run",
            {
                "session_id": session_id,
                "request_id": request_id,
                "input": text,
                "tool_count": len(self.cached_tools),
            },
        )

        response: AgentResponse[Any] = await asyncio.wait_for(
            self.agent.run(
                Message(
                    role="user",
                    text=text,
                    additional_properties={"request_id": request_id},
                ),
                session=session,
                options={"store": True},
            ),
            timeout=get_mcp_request_timeout_seconds(),
        )

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
        )

    @staticmethod
    def runtime_metadata() -> dict[str, str | None]:
        cfg = get_openai_compatible_config()
        return {
            "model": cfg.get("model"),
            "endpoint": cfg.get("base_url"),
            "runtime": "maf_agent",
        }
