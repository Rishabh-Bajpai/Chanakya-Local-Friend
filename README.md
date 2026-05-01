# Chanakya

Chanakya is a **task orchestration system powered by Microsoft Agent Framework**. It provides a single user-facing assistant while a network of intelligent agents collaborates in the background to complete tasks, provide insights, and maintain ongoing workflows.

- Chanakya Flask app on `http://127.0.0.1:5513`
- AIR service on `http://127.0.0.1:5512`
- Conversation layer on `http://127.0.0.1:5514`
- Optional A2A bridge on `http://127.0.0.1:18770`

The most important setup rule is simple: create the repo-root `.env` and `mcp_config_file.json` before starting the stack. The startup scripts read those files immediately.

## Quick Start

### 1. Prerequisites

- Python 3.11 is the safest default for local development.
- `python3.11 -m venv` available on your machine.
- `uvx` available if you use the example MCP config as-is.
- An OpenAI-compatible API key and base URL.

### 2. Create the virtual environment

From the repo root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python -m pip install -e ./AI-Router-AIR
python -m pip install -e ./chanakya_conversation_layer
```

If you prefer conda for day-to-day development, that is still fine. The only hard requirement is that the `systemd` installer expects a repo-root `.venv`.

### 3. Create `.env` before starting anything

Start from the checked-in template:

```bash
cp .env.example .env
```

Then edit `.env` for your machine and credentials. Use `.env.example` as the source of truth for supported variables.

The startup scripts source this file automatically and export `ENV_FILE_PATH` for child processes. If this file is missing, the services start without your intended runtime configuration.

### 4. Create `mcp_config_file.json` before starting anything

The app expects a repo-root `mcp_config_file.json`.

Start from the example:

```bash
cp mcp_config_file.example.json mcp_config_file.json
```

Then edit it for your environment if needed. Use `mcp_config_file.example.json` as the source of truth for the default MCP server layout. The checked-in example includes both local Python-backed MCP servers and `uvx`-launched servers.

### 5. Start the stack

Core stack:

```bash
./scripts/start_chanakya_air.sh core
```

Core stack plus A2A components:

```bash
./scripts/start_chanakya_air.sh core+a2a
```

Open the main UI at `http://127.0.0.1:5513`.

### 6. Stop the stack

```bash
./scripts/stop_chanakya_air.sh
```

## What The Startup Script Does

`./scripts/start_chanakya_air.sh` starts the current local stack in this order:

1. AIR service
2. Chanakya conversation layer
3. Optional A2A services for `core+a2a`
4. Chanakya Flask app

It also:

- reads `.env` from the repo root unless `ENV_FILE_PATH` is already set
- writes PID files and logs under `build/runtime/`
- prints the service URLs after startup

Use `./scripts/stop_chanakya_air.sh` to stop everything cleanly.

## Required Configuration

### `.env`

Start from `.env.example` and copy it into place:

```bash
cp .env.example .env
```

At minimum, make sure your local `.env` has working model endpoint and credential values. The exact defaults live in `.env.example`.

Common variables used in local development include:

```bash
CHANAKYA_CORE_AGENT_BACKEND=local
A2A_AGENT_URL=http://127.0.0.1:18770
AIR_SERVER_PORT=5512
CHANAKYA_PORT=5513
CONVERSATION_LAYER_PORT=5514
```

### `mcp_config_file.json`

Start from the checked-in template:

```bash
cp mcp_config_file.example.json mcp_config_file.json
```

This file defines the MCP servers Chanakya can connect to. The example file already includes entries for:

- `mcp_websearch`
- `mcp_fetch`
- `mcp_calculator`
- `mcp_code_execution`
- `mcp_filesystem`
- `mcp_git`
- `mcp_http`
- `mcp_json`
- `mcp_shell_utils`
- `mcp_weather`
- `mcp_map`
- `mcp_timer`
- `mcp_work_tools`
- `mcp_artifact_tools`

If you add or remove MCP servers, restart the stack afterward so the tool loader reconnects using the updated config.

## Local Development

### Test, lint, and type-check

From the repo root with the environment activated:

```bash
pytest chanakya/test
python -m ruff check chanakya/
python -m mypy chanakya/
```

For a focused test run:

```bash
pytest chanakya/test/test_agent_manager.py -q
```

### Database utilities

```bash
python scripts/db_viewer.py
python scripts/update_database.py
python scripts/clear_database.py
```

Notes:

- `scripts/clear_database.py` is destructive.
- If `DATABASE_URL` is unset, the default SQLite database is `chanakya_data/chanakya.db`.

### Manual smoke checks

These rely on external tooling and are not the default verification path:

```bash
python scripts/run_maf_tools.py
python scripts/test_mcp_fetch_connectivity.py --mode with-wrapper
python scripts/test_mcp_fetch_connectivity.py --mode without-wrapper
```

## Runtime Files

Runtime state is written under `chanakya_data/` and `build/runtime/`.

- `chanakya_data/` holds application state such as the SQLite database and shared workspace data.
- `build/runtime/` holds PID files and service logs from the startup scripts.

If something fails to boot, check the recent logs in `build/runtime/` first.

## Service Installation On Ubuntu

The repo includes a `systemd` installer for the core stack:

```bash
sudo ./scripts/install-autostart-ubuntu.sh
```

Important details:

- it requires a repo-root `.venv`
- it passes `ENV_FILE_PATH` pointing at the repo-root `.env`
- it installs services for the invoking non-root user by default

Useful commands:

```bash
sudo systemctl status chanakya.target
sudo journalctl -u chanakya-air.service -f
sudo journalctl -u chanakya-conversation-layer.service -f
sudo journalctl -u chanakya-app.service -f
sudo systemctl restart chanakya.target
```

Uninstall:

```bash
sudo ./scripts/uninstall-autostart-ubuntu.sh
```

## Repository Layout

This workspace contains a few related codebases. The main ones are:

- `chanakya/`: primary Flask app, routes, templates, core state, tests
- `AI-Router-AIR/`: FastAPI service used by the local stack on port 5512
- `chanakya_conversation_layer/`: separate conversation-layer package and tests
- `scripts/`: startup, shutdown, database, and service-management scripts

If you are changing runtime behavior, the most relevant files are usually:

- `chanakya/app.py`
- `chanakya/chat_service.py`
- `chanakya/store.py`
- `chanakya/agent/runtime.py`
- `chanakya/templates/`
- `chanakya/static/js/air_voice.js`

## Common Problems

### The stack starts but behaves incorrectly

Check these first:

1. `.env` exists at the repo root and has the expected model credentials.
2. `mcp_config_file.json` exists at the repo root.
3. The virtual environment includes all three editable installs.
4. `build/runtime/*.log` shows all services stayed up after startup.

### A service starts with the wrong environment

The startup scripts source the repo-root `.env` automatically. If you want a different env file, set `ENV_FILE_PATH` before invoking the script.

### MCP tools are missing

Confirm that:

1. the tool exists in `mcp_config_file.json`
2. its command is installed on your machine
3. you restarted the stack after editing the MCP config

## Related Files

- `mcp_config_file.example.json`: starting point for MCP server configuration
- `scripts/start_chanakya_air.sh`: standard local stack entrypoint
- `scripts/stop_chanakya_air.sh`: standard shutdown entrypoint
- `scripts/install-autostart-ubuntu.sh`: `systemd` installer for Linux
