from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from openai import AsyncOpenAI


class OrchestrationAgentError(RuntimeError):
    pass


@dataclass(slots=True)
class MAFOrchestrationAgent:
    model: str
    base_url: str
    api_key: str
    env_file_path: str
    backend: str = "openai_compatible"
    debug: bool = False
    runner: Callable[[str], Any] | None = None
    remote_agent_url: str = ""
    default_remote_agent: str | None = None
    default_model_provider: str | None = None
    default_model_id: str | None = None
    a2a_agent_factory: Any | None = None
    _agent: Agent | None = field(init=False, default=None, repr=False)
    _agent_by_model: dict[str, Agent] = field(
        init=False, default_factory=dict, repr=False
    )
    _a2a_agent: Any | None = field(init=False, default=None, repr=False)
    _a2a_sessions: dict[str, Any] = field(
        init=False, default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        if self.runner is None:
            if self.backend == "a2a":
                if self.a2a_agent_factory is None:
                    from agent_framework_a2a import A2AAgent

                    self.a2a_agent_factory = A2AAgent
                self._a2a_agent = self.a2a_agent_factory(
                    name="ConversationLayerPlanner",
                    description="Structured orchestration planner for the conversation layer.",
                    url=self.remote_agent_url,
                )
            else:
                client = self._build_openai_chat_client(self.model)
                self._agent = Agent(
                    client=client,
                    name="ConversationLayerPlanner",
                    description="Structured orchestration planner for the conversation layer.",
                    instructions=(
                        "You are a planning agent for a conversation orchestration layer. "
                        "Return only valid JSON that matches the requested schema. "
                        "Do not include markdown fences or prose outside the JSON object."
                    ),
                )

    def plan(
        self, *, task: str, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            task=task, instructions=instructions, payload=payload
        )
        raw_text = self._run(prompt)
        return self._parse_result(raw_text)

    def plan_with_model(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
        model_id: str | None,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            task=task, instructions=instructions, payload=payload
        )
        raw_text = self._run(
            prompt,
            model_override=model_id,
            request_headers=request_headers,
        )
        return self._parse_result(raw_text)

    async def plan_async(
        self, *, task: str, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            task=task, instructions=instructions, payload=payload
        )
        raw_text = await self._run_async(prompt)
        return self._parse_result(raw_text)

    def _parse_result(self, raw_text: str) -> dict[str, Any]:
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise OrchestrationAgentError(
                f"Invalid JSON from orchestration agent: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise OrchestrationAgentError(
                "Orchestration agent must return a JSON object"
            )
        return result

    def _run(
        self,
        prompt: str,
        *,
        model_override: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> str:
        if self.runner is not None:
            result = self.runner(prompt)
        elif self.backend == "a2a":
            result = asyncio.run(self._run_a2a(prompt, model_override=model_override))
        else:
            agent = self._agent_for_model(model_override, request_headers=request_headers)
            result = asyncio.run(agent.run(prompt))
        return self._coerce_result_to_text(result)

    async def _run_async(self, prompt: str) -> str:
        if self.runner is not None:
            result = self.runner(prompt)
        elif self.backend == "a2a":
            result = await self._run_a2a(prompt)
        else:
            result = await self._agent.run(prompt)

        return self._coerce_result_to_text(result)

    async def _run_a2a(self, prompt: str, *, model_override: str | None = None) -> Any:
        if self._a2a_agent is None:
            raise OrchestrationAgentError("A2A orchestration agent is not initialized")
        session = self._a2a_session_for_model(model_override)
        message = self._build_a2a_prompt(prompt, model_override=model_override)
        from agent_framework import Message

        return await self._a2a_agent.run(
            [Message(role="user", text=message)],
            session=session,
        )

    def _coerce_result_to_text(self, result: Any) -> str:
        if isinstance(result, dict):
            return json.dumps(result)
        text = getattr(result, "text", None)
        if text is not None:
            return str(text)
        return str(result)

    def _build_prompt(
        self, *, task: str, instructions: str, payload: dict[str, Any]
    ) -> str:
        return (
            f"Task: {task}\n"
            f"Instructions:\n{instructions}\n\n"
            "Return exactly one JSON object.\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=True)}"
        )

    def _agent_for_model(
        self,
        model_override: str | None,
        request_headers: dict[str, str] | None = None,
    ) -> Agent:
        model_id = str(model_override or "").strip()
        if not model_id or model_id == self.model:
            if request_headers:
                client = self._build_openai_chat_client(
                    self.model, default_headers=request_headers
                )
                return Agent(
                    client=client,
                    name=f"ConversationLayerPlanner[{self.model}]",
                    description="Structured orchestration planner for the conversation layer.",
                    instructions=(
                        "You are a planning agent for a conversation orchestration layer. "
                        "Return only valid JSON that matches the requested schema. "
                        "Do not include markdown fences or prose outside the JSON object."
                    ),
                )
            if self._agent is None:
                raise OrchestrationAgentError("Orchestration agent is not initialized")
            return self._agent
        cached = self._agent_by_model.get(model_id)
        if cached is not None:
            return cached
        client = self._build_openai_chat_client(model_id, default_headers=request_headers)
        created = Agent(
            client=client,
            name=f"ConversationLayerPlanner[{model_id}]",
            description="Structured orchestration planner for the conversation layer.",
            instructions=(
                "You are a planning agent for a conversation orchestration layer. "
                "Return only valid JSON that matches the requested schema. "
                "Do not include markdown fences or prose outside the JSON object."
            ),
        )
        self._agent_by_model[model_id] = created
        return created

    def _a2a_session_for_model(self, model_override: str | None):
        model_id = str(model_override or "").strip() or str(
            self.default_model_id or ""
        ).strip()
        session_key = model_id or "default"
        session = self._a2a_sessions.get(session_key)
        if session is not None:
            return session
        if self._a2a_agent is None:
            raise OrchestrationAgentError("A2A orchestration agent is not initialized")
        created = self._a2a_agent.create_session(
            session_id=f"conversation-layer-planner:{session_key}"
        )
        self._a2a_sessions[session_key] = created
        return created

    def _build_a2a_prompt(self, prompt: str, *, model_override: str | None) -> str:
        header_parts: list[str] = []
        remote_agent = str(self.default_remote_agent or "").strip()
        model_provider = str(self.default_model_provider or "").strip()
        model_id = str(model_override or self.default_model_id or "").strip()
        if remote_agent:
            header_parts.append(f"agent={remote_agent}")
        if model_provider and model_id:
            header_parts.append(f"model_provider={model_provider}")
            header_parts.append(f"model_id={model_id}")
        if not header_parts:
            return prompt
        return f"[[opencode-options:{';'.join(header_parts)}]]\n{prompt}"

    def _build_openai_chat_client(
        self,
        model_id: str,
        default_headers: dict[str, str] | None = None,
    ) -> OpenAIChatClient:
        async_client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,
            default_headers=default_headers,
        )
        return OpenAIChatClient(
            model_id=model_id,
            async_client=async_client,
            env_file_path=self.env_file_path,
        )
