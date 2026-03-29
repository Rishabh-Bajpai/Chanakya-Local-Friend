import asyncio
import sys
from pathlib import Path
from typing import cast

from agent_framework import MCPStdioTool

from chanakya.config import get_mcp_request_timeout_seconds
from chanakya.debug import debug_log
from chanakya.services.async_loop import get_maf_loop
from chanakya.services.config_loader import load_mcp_config, merge_env_with_os

_loaded_tools: list[MCPStdioTool] = []
_tools_availability: list[dict[str, str]] = []


def _wrap_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    """Wrap a command so its stdout is strictly valid JSON."""
    wrapper_path = Path(__file__).resolve().parent / "mcp_wrapper.py"
    return sys.executable, ["-u", str(wrapper_path), command] + args


def _describe_server(command: str, args: list[str]) -> str:
    parts = [command, *args]
    return " ".join(part for part in parts if part).strip() or command


def _normalize_server_config(
    server_id: str, details: object
) -> tuple[str, list[str], dict[str, str]]:
    if not isinstance(details, dict):
        raise ValueError(f"Invalid MCP config for {server_id}: expected an object")

    command = details.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"Invalid MCP config for {server_id}: command must be a non-empty string")
    command_text = cast(str, command).strip()

    raw_args = details.get("args", [])
    if not isinstance(raw_args, list) or any(not isinstance(arg, str) for arg in raw_args):
        raise ValueError(f"Invalid MCP config for {server_id}: args must be a list of strings")
    args = [cast(str, arg) for arg in raw_args]

    raw_env = details.get("env", {})
    if not isinstance(raw_env, dict):
        raise ValueError(f"Invalid MCP config for {server_id}: env must be an object")

    env = {str(key): str(value) for key, value in raw_env.items()}
    return command_text, args, env


async def _init_tools_async() -> None:
    global _loaded_tools, _tools_availability
    if _loaded_tools:
        return

    # Ensure availability reflects only the latest initialization attempt
    _tools_availability.clear()
    config = load_mcp_config()
    for server_id, details in config.items():
        try:
            command, args, env = _normalize_server_config(server_id, details)
        except ValueError as exc:
            _tools_availability.append(
                {
                    "tool_id": server_id,
                    "tool_name": server_id,
                    "server_name": server_id,
                    "status": "unavailable",
                    "error": str(exc),
                }
            )
            debug_log("mcp_tool_config_invalid", {"server": server_id, "error": str(exc)})
            continue

        merged_env = merge_env_with_os(env)
        server_name = _describe_server(command, args)

        # Wrap everything since noisy logs break things
        wrapped_cmd, wrapped_args = _wrap_command(command, args)

        tool = MCPStdioTool(
            name=server_id,
            command=wrapped_cmd,
            args=wrapped_args,
            env=merged_env,
            tool_name_prefix=f"{server_id}_",  # single underscore exactly like the manual fix
            approval_mode="never_require",
        )

        try:
            # We explicitly connect without `async with` inside the background loop
            # to make it persistent.
            debug_log(
                "mcp_tool_connecting",
                {"server": server_id, "command": wrapped_cmd, "args": wrapped_args},
            )
            await asyncio.wait_for(tool.connect(), timeout=get_mcp_request_timeout_seconds())
            debug_log("mcp_tool_connected", {"server": server_id})
            setattr(tool, "server_name", server_name)
            _loaded_tools.append(tool)
            _tools_availability.append(
                {
                    "tool_id": server_id,
                    "tool_name": server_id,
                    "server_name": server_name,
                    "status": "available",
                }
            )
            debug_log("mcp_tool_loaded", {"server": server_id, "status": "connected"})
        except Exception as e:
            try:
                await tool.close()
            except Exception:
                pass
            _tools_availability.append(
                {
                    "tool_id": server_id,
                    "tool_name": server_id,
                    "server_name": server_name,
                    "status": "unavailable",
                    "error": str(e),
                }
            )
            debug_log("mcp_tool_connection_failed", {"server": server_id, "error": str(e)})


def initialize_all_tools() -> None:
    loop = get_maf_loop()
    future = asyncio.run_coroutine_threadsafe(_init_tools_async(), loop)
    future.result()


def get_cached_tools() -> list[MCPStdioTool]:
    return list(_loaded_tools)


def get_tools_availability() -> list[dict[str, str]]:
    return list(_tools_availability)
