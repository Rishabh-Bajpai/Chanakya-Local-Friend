import asyncio
import sys
from pathlib import Path
from agent_framework import MCPStdioTool
from chanakya.services.config_loader import load_mcp_config, merge_env_with_os
from chanakya.services.async_loop import get_maf_loop
from chanakya.debug import debug_log

_loaded_tools: list[MCPStdioTool] = []
_tools_availability: list[dict[str, str]] = []

def _wrap_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    """Wrap a command so its stdout is strictly valid JSON."""
    wrapper_path = Path(__file__).resolve().parent / "mcp_wrapper.py"
    return sys.executable, ["-u", str(wrapper_path), command] + args

async def _init_tools_async() -> None:
    global _loaded_tools, _tools_availability
    if _loaded_tools:
        return

    # Ensure availability reflects only the latest initialization attempt
    _tools_availability.clear()
    config = load_mcp_config()
    for server_id, details in config.items():
        command = details.get("command")
        args = details.get("args", [])
        env = details.get("env", {})

        merged_env = merge_env_with_os(env)

        # Wrap everything since noisy logs break things
        wrapped_cmd, wrapped_args = _wrap_command(command, args)

        tool = MCPStdioTool(
            name=server_id,
            command=wrapped_cmd,
            args=wrapped_args,
            env=merged_env,
            tool_name_prefix=f"{server_id}_", # single underscore exactly like the manual fix
            approval_mode="never_require",
        )

        try:
            # We explicitly connect without `async with` inside the background loop 
            # to make it persistent.
            debug_log(
                "mcp_tool_connecting",
                {"server": server_id, "command": wrapped_cmd, "args": wrapped_args},
            )
            await tool.connect()
            debug_log("mcp_tool_connected", {"server": server_id})
            _loaded_tools.append(tool)
            _tools_availability.append({
                "tool_id": server_id,
                "tool_name": server_id,
                "server_name": " ".join(args),
                "status": "available",
            })
            debug_log("mcp_tool_loaded", {"server": server_id, "status": "connected"})
        except Exception as e:
            try:
                await tool.close()
            except Exception:
                pass
            _tools_availability.append({
                "tool_id": server_id,
                "tool_name": server_id,
                "server_name": " ".join(args),
                "status": "unavailable",
                "error": str(e)
            })
            debug_log("mcp_tool_connection_failed", {"server": server_id, "error": str(e)})

def initialize_all_tools() -> None:
    loop = get_maf_loop()
    future = asyncio.run_coroutine_threadsafe(_init_tools_async(), loop)
    future.result()

def get_cached_tools() -> list[MCPStdioTool]:
    return list(_loaded_tools)

def get_tools_availability() -> list[dict[str, str]]:
    return list(_tools_availability)
