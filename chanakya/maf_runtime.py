from __future__ import annotations

import asyncio

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient

from chanakya.config import get_openai_compatible_config
from chanakya.models import AgentProfile


class MAFRuntime:
    def __init__(self, profile: AgentProfile, env_file_path: str = ".env") -> None:
        self.profile = profile
        self.client = OpenAIChatClient(env_file_path=env_file_path)
        self.agent = Agent(
            client=self.client,
            name=profile.name,
            instructions=profile.system_prompt,
        )

    def run_direct(self, text: str) -> str:
        async def _run() -> str:
            result = await asyncio.wait_for(self.agent.run(text), timeout=30)
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
