from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatClient
from pydantic import Field


@tool(approval_mode="never_require")
def maf_weather_tool(
    location: Annotated[str, Field(description="Location to fetch weather for")],
) -> str:
    """Return deterministic weather text for MVP validation."""
    return f"Weather for {location}: 27C, partly cloudy, light wind."


@dataclass(slots=True)
class MafAgentRuntime:
    direct_agent: Agent
    weather_agent: Agent

    @classmethod
    def from_env(cls, env_file_path: str = ".env") -> MafAgentRuntime | None:
        try:
            client = OpenAIChatClient(env_file_path=env_file_path)
        except Exception:
            return None

        direct_agent = Agent(
            client=client,
            name="ChanakyaDirectAgent",
            instructions="You are Chanakya. Reply briefly and clearly.",
        )
        weather_agent = Agent(
            client=client,
            name="ChanakyaWeatherAgent",
            instructions=(
                "You are Chanakya weather helper. "
                "Always call maf_weather_tool when location is present. "
                "If no location is provided, ask for it."
            ),
            tools=[maf_weather_tool],
        )
        return cls(direct_agent=direct_agent, weather_agent=weather_agent)

    def run_direct(self, user_prompt: str) -> str | None:
        return self._run_agent(self.direct_agent, user_prompt)

    def run_weather(self, user_prompt: str, location: str | None) -> str | None:
        if not location:
            return None
        prompt = (
            f"User request: {user_prompt}\n"
            f"Location: {location}\n"
            "Use maf_weather_tool and provide a concise weather response."
        )
        return self._run_agent(self.weather_agent, prompt)

    @staticmethod
    def _run_agent(agent: Agent, prompt: str) -> str | None:
        async def _run() -> str:
            result = await asyncio.wait_for(agent.run(prompt), timeout=20)
            return str(result).strip()

        try:
            output = asyncio.run(_run())
        except Exception:
            return None
        if not output:
            return None
        return output
