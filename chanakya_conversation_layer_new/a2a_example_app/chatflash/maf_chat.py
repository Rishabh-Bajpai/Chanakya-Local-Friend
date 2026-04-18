from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from agent_framework import Agent, AgentSession, Message
from agent_framework.openai import OpenAIChatClient
from agent_framework_a2a import A2AAgent

from .config import Settings
from .models import AgentDescriptor, MessageRecord


AGENT_PREFIX = "[[opencode-agent:"


def encode_agent_prompt(agent_name: str, user_text: str) -> str:
    return f"{AGENT_PREFIX}{agent_name}]]\n{user_text}"


@dataclass(slots=True)
class ActiveAgent:
    backend: str
    instance: Agent | A2AAgent


class MAFChatService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.active = self._build_active_agent()
        self._sessions: dict[tuple[str, str], AgentSession] = {}

    def _build_active_agent(self) -> ActiveAgent:
        return ActiveAgent(
            backend="a2a",
            instance=A2AAgent(
                name="OpenCode A2A",
                description="Microsoft Agent Framework A2A agent for OpenCode.",
                url=self.settings.opencode_a2a_url,
            ),
        )

        # To switch to a local OpenAI-compatible model, comment the block above
        # and uncomment the block below. The rest of the app can stay unchanged.
        # return ActiveAgent(
        #     backend="openai",
        #     instance=Agent(
        #         name="Local OpenAI-Compatible Agent",
        #         description="Microsoft Agent Framework local agent.",
        #         instructions="You are a helpful assistant for multi-turn chat.",
        #         client=OpenAIChatClient(
        #             base_url=self.settings.model_base_url,
        #             api_key=self.settings.model_api_key,
        #             model_id=self.settings.model_id,
        #         ),
        #     ),
        # )

    async def list_agents(self) -> list[AgentDescriptor]:
        if self.active.backend == "a2a":
            names: list[str] = []
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        f"{self.settings.opencode_http_url.rstrip('/')}/agent"
                    )
                    response.raise_for_status()
                    data = response.json()
                names = [
                    item["name"]
                    for item in data
                    if item.get("mode") in {"primary", "all", "subagent"}
                ]
            except Exception:
                names = list(self.settings.default_remote_agents)

            return [
                AgentDescriptor(
                    id=f"opencode:{name}",
                    label=f"OpenCode {name}",
                    backend="maf-a2a",
                    description=f"OpenCode agent '{name}' via MAF A2AAgent.",
                    badges=["maf", "a2a", "opencode"],
                    detail=self.settings.opencode_a2a_url,
                )
                for name in names
            ]

        return [
            AgentDescriptor(
                id="local:assistant",
                label="Local Assistant",
                backend="maf-openai",
                description="Local OpenAI-compatible agent via MAF Agent.",
                badges=["maf", "openai-compatible"],
                detail=self.settings.model_id,
            )
        ]

    def _get_session(self, agent_id: str, session_id: str) -> AgentSession:
        key = (agent_id, session_id)
        if key not in self._sessions:
            self._sessions[key] = self.active.instance.create_session(
                session_id=session_id
            )
        return self._sessions[key]

    @staticmethod
    def _seed_prompt(history: list[MessageRecord], user_text: str, limit: int) -> str:
        chunks = [
            "Continue this conversation using the transcript excerpt below.",
        ]
        for item in history[-limit:]:
            role = "User" if item.role == "user" else "Assistant"
            chunks.append(f"{role}: {item.content}")
        chunks.append(f"User: {user_text}")
        return "\n".join(chunks)

    def _build_messages(
        self,
        *,
        agent_id: str,
        history: list[MessageRecord],
        user_text: str,
        seeded: bool,
        remote_context_id: str | None,
    ) -> list[Message]:
        if self.active.backend == "a2a":
            agent_name = agent_id.split(":", 1)[1]
            content = user_text
            if seeded:
                content = self._seed_prompt(
                    history, user_text, limit=self.settings.session_history_limit
                )
            additional_properties = {}
            if remote_context_id:
                additional_properties["context_id"] = remote_context_id
            return [
                Message(
                    "user",
                    text=encode_agent_prompt(agent_name, content),
                    additional_properties=additional_properties,
                )
            ]

        if seeded:
            return [
                Message(
                    "user",
                    text=self._seed_prompt(
                        history, user_text, limit=self.settings.session_history_limit
                    ),
                )
            ]

        return [Message("user", text=user_text)]

    @staticmethod
    def _extract_context_id(response: Any) -> str | None:
        raw = getattr(response, "raw_representation", None)
        if isinstance(raw, list):
            for item in reversed(raw):
                context_id = getattr(item, "context_id", None)
                if context_id:
                    return context_id
        return getattr(raw, "context_id", None)

    async def send_message(
        self,
        *,
        agent_id: str,
        session_id: str,
        history: list[MessageRecord],
        user_text: str,
        remote_context_id: str | None,
    ) -> tuple[str, str | None]:
        maf_session = self._get_session(agent_id, session_id)
        messages = self._build_messages(
            agent_id=agent_id,
            history=history,
            user_text=user_text,
            seeded=False,
            remote_context_id=remote_context_id,
        )
        try:
            response = await self.active.instance.run(messages, session=maf_session)
        except Exception:
            fallback_messages = self._build_messages(
                agent_id=agent_id,
                history=history,
                user_text=user_text,
                seeded=True,
                remote_context_id=None,
            )
            response = await self.active.instance.run(
                fallback_messages, session=maf_session
            )

        return (
            response.text
            or response.value
            or "No response returned from the active MAF agent.",
            self._extract_context_id(response) or remote_context_id,
        )
