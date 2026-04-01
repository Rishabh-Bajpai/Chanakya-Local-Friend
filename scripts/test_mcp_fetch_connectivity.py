import asyncio
import argparse

import pytest
from agent_framework import MCPStdioTool
from chanakya.services.tool_loader import _wrap_command


@pytest.mark.anyio
async def test_with_wrapper() -> None:
    print("Testing fetch tool with wrapper...")
    cmd, args = _wrap_command("uvx", ["mcp-server-fetch"])
    print(f"Executing: {cmd} {args}")
    tool = MCPStdioTool(name="test_fetch", command=cmd, args=args, tool_name_prefix="check_")
    print("Connecting...")
    await tool.connect()
    print("Connected! Available functions:", tool.functions)
    await tool.close()


@pytest.mark.anyio
async def test_without_wrapper() -> None:
    print("Testing fetch tool without wrapper...")
    tool = MCPStdioTool(
        name="test_fetch", command="uvx", args=["mcp-server-fetch"], tool_name_prefix="check_"
    )
    print("Connecting...")
    await tool.connect()
    print("Connected! Available functions:", tool.functions)
    await tool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MCP connectivity smoke tests.")
    parser.add_argument(
        "--mode",
        choices=("with-wrapper", "without-wrapper", "both"),
        default="both",
        help="Which connectivity path to verify.",
    )
    args = parser.parse_args()

    if args.mode == "with-wrapper":
        asyncio.run(test_with_wrapper())
        return
    if args.mode == "without-wrapper":
        asyncio.run(test_without_wrapper())
        return

    asyncio.run(test_with_wrapper())
    asyncio.run(test_without_wrapper())


if __name__ == "__main__":
    main()
