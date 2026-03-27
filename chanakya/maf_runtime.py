from __future__ import annotations

import asyncio

from agent_framework import Agent, Message
from agent_framework.openai import OpenAIChatClient
from sqlalchemy.orm import Session, sessionmaker

from chanakya.config import get_openai_compatible_config
from chanakya.debug import debug_log
from chanakya.history_provider import SQLAlchemyHistoryProvider
from chanakya.model import AgentProfileModel


class MAFRuntime:
    def __init__(
        self,
        profile: AgentProfileModel,
        session_factory: sessionmaker[Session],
        env_file_path: str = ".env",
    ) -> None:
        self.profile = profile
        self.client = OpenAIChatClient(env_file_path=env_file_path)
        self.history_provider = SQLAlchemyHistoryProvider(
            session_factory=session_factory,
            load_messages=True,
            store_inputs=True,
            store_outputs=True,
        )
        self.agent = Agent(
            client=self.client,
            name=profile.name,
            instructions=profile.system_prompt,
            context_providers=[self.history_provider],
        )
        debug_log(
            "maf_runtime_initialized",
            {
                "agent_name": profile.name,
                "history_provider": "sqlalchemy",
                "role": profile.role,
                "model": self.runtime_metadata().get("model"),
                "endpoint": self.runtime_metadata().get("endpoint"),
            },
        )

    def run_direct(self, text: str) -> str:
        async def _run() -> str:
            result = await asyncio.wait_for(self.agent.run(text), timeout=30)
            return str(result).strip()

        return asyncio.run(_run())

    def run_chat(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        route: str,
    ) -> str:
        async def _run() -> str:
            session = self.agent.create_session(session_id=session_id)
            session.state["request_id"] = request_id
            session.state["route"] = route
            debug_log(
                "maf_runtime_before_run",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "route": route,
                    "session_state": session.state,
                    "input": text,
                },
            )
            result = await asyncio.wait_for(
                self.agent.run(
                    Message(
                        role="user",
                        text=text,
                        additional_properties={
                            "request_id": request_id,
                            "route": route,
                        },
                    ),
                    session=session,
                    options={"store": True},
                ),
                timeout=30,
            )
            debug_log(
                "maf_runtime_after_run",
                {
                    "session_id": session_id,
                    "session_state": session.state,
                    "raw_result": str(result),
                },
            )
            return str(result).strip()

        return asyncio.run(_run())

    @staticmethod
    def runtime_metadata() -> dict[str, str | None]:
        cfg = get_openai_compatible_config()
        return {
            "model": cfg.get("model"),
            "endpoint": cfg.get("base_url"),
            "runtime": "maf_agent",
        }
