# How To Use MCP Efficiently From `mcp_config_file.json`

This guide explains the practical MCP pattern used in this repo so another agent can reuse it in a different project.

## Goal

Use `mcp_config_file.json` as the single source of truth for:

- which MCP servers to start
- how to start them
- which environment variables they need
- how to expose their tools to your application

## Recommended Config Shape

Use a top-level `mcpServers` object.

```json
{
  "mcpServers": {
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    },
    "calculate": {
      "command": "python",
      "args": ["-m", "my_mcp_server"],
      "transport": "stdio",
      "env": {
        "API_KEY": "${OPTIONAL_FALLBACK_OR_EMPTY}"
      }
    }
  }
}
```

Each server should define:

- `command`: executable to run
- `args`: command arguments
- `transport`: usually `stdio`
- `env`: optional environment variables

## Efficient Loading Pattern

1. Read `mcp_config_file.json` once at startup.
2. Extract `mcpServers` only.
3. For each server, build a runtime config object.
4. Merge `env` values with real OS environment variables.
5. Wrap the server process if needed to keep stdout MCP-safe.
6. Load tools once and cache them.
7. Pass both tool objects and tool descriptions into the agent layer.

This avoids repeated process startup, repeated config parsing, and prompt/tool mismatch.

## Best Practices

### 1. Keep config declarative

Do not hardcode MCP server commands in application logic when they can live in `mcp_config_file.json`.

Good:

- app reads server definitions from config
- adding a new tool usually means editing one JSON file

Avoid:

- scattered per-server startup code
- custom branching for every tool unless truly necessary

### 2. Merge env from the real environment first

Recommended precedence:

1. actual environment variable from the OS or `.env`
2. fallback value from `mcp_config_file.json`

This makes local dev, CI, Docker, and production easier without changing code.

Pseudo-code:

```python
effective_env = {}
for key, fallback in config_env.items():
    effective_env[key] = os.environ.get(key) or fallback
```

### 3. Cache loaded tools

Tool discovery can be expensive. Load once, then cache:

- tool objects for execution
- rendered descriptions for the LLM prompt
- a list/string of tool names for allowed tool calls

This repo uses that pattern so the request path stays fast.

### 4. Separate execution from prompting

Store two things after MCP load:

- executable tool objects
- prompt-safe metadata about those tools

Why this matters:

- the LLM needs descriptions and allowed names
- the runtime needs actual callable tool objects
- keeping both in sync reduces agent errors

### 5. Protect the MCP protocol on stdout

Many MCP servers print logs to stdout. That can break JSON-based MCP communication.

Efficient solution:

- wrap the subprocess
- pass valid JSON lines to stdout
- redirect non-JSON logs to stderr

If another project uses noisy MCP servers, this wrapper pattern is worth copying.

### 6. Fail soft when MCP is unavailable

If config is missing or a server fails to load:

- log the problem
- continue with zero tools if possible
- do not crash the whole app unless MCP is mandatory

This makes startup more robust.

### 7. Keep secrets out of the file when possible

Do not rely on committed secrets inside `mcp_config_file.json`.

Prefer:

- `.env`
- runtime environment variables
- secrets managers in production

Use the JSON file only for non-sensitive defaults or placeholders.

## Reusable Implementation Blueprint

Another project can follow this structure:

```text
project/
  mcp_config_file.json
  src/
    services/
      config_loader.py
      tool_loader.py
      mcp_wrapper.py
    agent/
      prompt.py
      runtime.py
```

Recommended responsibilities:

- `config_loader.py`: read and validate `mcp_config_file.json`
- `tool_loader.py`: transform config into MCP client runtime config and cache tools
- `mcp_wrapper.py`: protect stdout protocol if servers emit logs
- `prompt.py`: inject tool descriptions and allowed names
- `runtime.py`: pass executable MCP tools into the agent executor

## Minimal Runtime Flow

```text
mcp_config_file.json
  -> load config
  -> normalize per-server settings
  -> merge env
  -> optionally wrap command
  -> create MCP client
  -> get tools
  -> cache tools + descriptions + names
  -> inject into prompt and agent runtime
```

## What Another Agent Should Do In A New Project

If you hand this file to another agent, ask it to:

1. create a repo-level `mcp_config_file.json`
2. add a config loader that returns `mcpServers`
3. add a tool loader that initializes all servers once at startup
4. cache loaded tools and prompt metadata
5. merge `env` values with the real environment
6. add a stdout/stderr wrapper if MCP servers are noisy
7. inject tool descriptions into the prompt and tool objects into the agent runtime
8. handle config/load failures without crashing the whole app

## Short Copy-Paste Instruction For Another Agent

```text
Implement MCP using a repo-level mcp_config_file.json with a top-level mcpServers object. Load it once at startup, merge each server's env block with OS environment variables, wrap noisy subprocesses so only valid JSON reaches stdout, initialize all MCP servers through a shared tool loader, cache the resulting tool objects plus prompt metadata (tool descriptions and allowed tool names), and inject descriptions into the LLM prompt while passing executable tool objects into the agent runtime. Fail soft if config is missing or a tool server fails.
```

## Notes From This Repo's Pattern

This repo's implementation is a solid baseline because it already follows the most useful MCP efficiency rules:

- startup loading instead of per-request loading
- cached tools instead of repeated discovery
- env override support
- prompt/runtime separation
- stdout filtering for protocol safety
- graceful fallback when MCP is unavailable

If a new project copies those six ideas, it will avoid most common MCP integration problems.
