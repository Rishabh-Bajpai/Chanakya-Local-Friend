from datetime import datetime, timezone

from agent_framework import MCPStdioTool

from chanakya.domain import now_iso
from chanakya.model import AgentProfileModel


def _build_runtime_prompt_prelude() -> str:
    current_utc_time = now_iso()
    local_now = datetime.now().astimezone()
    local_label = local_now.tzname() or str(local_now.tzinfo or "local")
    return (
        "# Runtime Context\n"
        f"Current local time: {local_now.isoformat()} ({local_label})\n"
        f"Current UTC time: {current_utc_time}\n"
        "For user-facing scheduling and time references, prefer the local time shown above unless the user explicitly asks for UTC or another timezone."
    )


def inject_tools_into_prompt(
    profile: AgentProfileModel,
    tools_cache: list[MCPStdioTool],
    *,
    base_prompt: str | None = None,
) -> str:
    """Takes the system prompt and explicitly tells the LLM the tools it has."""
    base_prompt = str(base_prompt if base_prompt is not None else profile.system_prompt)
    base_prompt = f"{_build_runtime_prompt_prelude()}\n\n{base_prompt}"
    if not tools_cache:
        return base_prompt

    # We load descriptions directly from the cached tools
    extensions = ["\n\n# Available External Capabilities\n"]

    for tool in tools_cache:
        # tool.allowed_tools or just tool._functions properties?
        # Actually `mcp_calculator` defines tools within it.
        # MCPTool has `.functions` property or `get_allowed_functions()`
        funcs = tool.functions
        for func in funcs:
            extensions.append(f"- Tool Name: `{func.name}`")
            if func.description:
                extensions.append(f"  Description: {func.description}")

    return base_prompt + "\n".join(extensions)


def get_allowed_tool_ids_for_agent(profile: AgentProfileModel) -> list[str]:
    """Helper to determine which tools this agent is supposed to use."""
    return list(profile.tool_ids_json or [])
