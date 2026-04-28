import asyncio
import sys
from pathlib import Path
from typing import Any, cast

from agent_framework import MCPStdioTool

from chanakya.config import get_mcp_request_timeout_seconds
from chanakya.debug import debug_log
from chanakya.services.async_loop import get_maf_loop
from chanakya.services.config_loader import get_mcp_config_path, load_mcp_config, merge_env_with_os

_loaded_tools: list[MCPStdioTool] = []
_tools_availability: list[dict[str, Any]] = []
_tools_catalog: list[dict[str, Any]] = []
_tools_initialized = False


def _wrap_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    """Wrap a command so its stdout is strictly valid JSON."""
    wrapper_path = Path(__file__).resolve().parent / "mcp_wrapper.py"
    return sys.executable, ["-u", str(wrapper_path), command] + args


def _describe_server(command: str, args: list[str]) -> str:
    parts = [command, *args]
    return " ".join(part for part in parts if part).strip() or command


def _tool_functions_payload(tool: MCPStdioTool) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for function in list(getattr(tool, "functions", []) or []):
        name = str(getattr(function, "name", "")).strip()
        if not name:
            continue
        payload.append(
            {
                "name": name,
                "description": str(getattr(function, "description", "") or "").strip(),
            }
        )
    return payload


def _summarize_tool_descriptions(functions: list[dict[str, str]]) -> str:
    descriptions = [item["description"] for item in functions if item.get("description")]
    if not descriptions:
        return ""
    return " ".join(descriptions[:3]).strip()


def _build_catalog_entry(
    *,
    server_id: str,
    command: str,
    args: list[str],
    env: dict[str, str],
    transport: str,
    server_name: str,
    status: str,
    error: str | None = None,
    functions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    function_payload = list(functions or [])
    return {
        "tool_id": server_id,
        "tool_name": server_id,
        "server_name": server_name,
        "status": status,
        "error": error,
        "transport": transport,
        "command": command,
        "args": list(args),
        "env_keys": sorted(str(key) for key in env.keys()),
        "functions": function_payload,
        "function_count": len(function_payload),
        "description": _summarize_tool_descriptions(function_payload),
        "config_path": str(get_mcp_config_path()),
    }


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
    global _loaded_tools, _tools_availability, _tools_catalog, _tools_initialized
    if _tools_initialized:
        return

    await _reload_tools_async()


async def _close_loaded_tools_async() -> None:
    global _loaded_tools
    for tool in list(_loaded_tools):
        try:
            await tool.close()
        except Exception as exc:
            debug_log(
                "mcp_tool_close_failed",
                {"server": getattr(tool, "name", "unknown_tool"), "error": str(exc)},
            )
    _loaded_tools = []


async def _reload_tools_async() -> None:
    global _loaded_tools, _tools_availability, _tools_catalog, _tools_initialized
    await _close_loaded_tools_async()

    # Ensure availability reflects only the latest initialization attempt
    _tools_availability.clear()
    _tools_catalog.clear()
    config = load_mcp_config()
    for server_id, details in config.items():
        try:
            command, args, env = _normalize_server_config(server_id, details)
        except ValueError as exc:
            entry = _build_catalog_entry(
                server_id=server_id,
                command="",
                args=[],
                env={},
                transport="stdio",
                server_name=server_id,
                status="unavailable",
                error=str(exc),
            )
            _tools_availability.append(dict(entry))
            _tools_catalog.append(entry)
            debug_log("mcp_tool_config_invalid", {"server": server_id, "error": str(exc)})
            continue

        transport = str(cast(dict[str, Any], details).get("transport") or "stdio").strip() or "stdio"
        if transport != "stdio":
            message = f"Unsupported MCP transport: {transport}. Only stdio is currently supported."
            entry = _build_catalog_entry(
                server_id=server_id,
                command=command,
                args=args,
                env=env,
                transport=transport,
                server_name=_describe_server(command, args),
                status="unavailable",
                error=message,
            )
            _tools_availability.append(dict(entry))
            _tools_catalog.append(entry)
            debug_log("mcp_tool_transport_unsupported", {"server": server_id, "transport": transport})
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
            functions = _tool_functions_payload(tool)
            entry = _build_catalog_entry(
                server_id=server_id,
                command=command,
                args=args,
                env=env,
                transport=transport,
                server_name=server_name,
                status="available",
                functions=functions,
            )
            _tools_availability.append(dict(entry))
            _tools_catalog.append(entry)
            debug_log("mcp_tool_loaded", {"server": server_id, "status": "connected"})
        except Exception as e:
            try:
                await tool.close()
            except Exception:
                pass
            entry = _build_catalog_entry(
                server_id=server_id,
                command=command,
                args=args,
                env=env,
                transport=transport,
                server_name=server_name,
                status="unavailable",
                error=str(e),
            )
            _tools_availability.append(dict(entry))
            _tools_catalog.append(entry)
            debug_log("mcp_tool_connection_failed", {"server": server_id, "error": str(e)})
    _tools_initialized = True


def initialize_all_tools() -> None:
    loop = get_maf_loop()
    future = asyncio.run_coroutine_threadsafe(_init_tools_async(), loop)
    future.result()


def reload_all_tools() -> list[dict[str, Any]]:
    loop = get_maf_loop()
    future = asyncio.run_coroutine_threadsafe(_reload_tools_async(), loop)
    future.result()
    return get_tools_availability()


def get_cached_tools() -> list[MCPStdioTool]:
    return list(_loaded_tools)


def get_tools_availability() -> list[dict[str, Any]]:
    return list(_tools_availability)


def get_tools_catalog() -> list[dict[str, Any]]:
    return list(_tools_catalog)


def get_configured_tool_ids() -> set[str]:
    if _tools_catalog:
        return {
            str(item.get("tool_id") or "").strip()
            for item in _tools_catalog
            if str(item.get("tool_id") or "").strip()
        }
    return {str(tool_id).strip() for tool_id in load_mcp_config().keys() if str(tool_id).strip()}
