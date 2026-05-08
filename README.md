# Chanakya

Chanakya is an **advanced, open-source, and self-hostable voice assistant** designed for privacy, power, and flexibility. It can leverage local AI/ML models to help keep data under your control and supports connecting to third-party MCP servers through configuration. A network of intelligent agents collaborates to complete tasks, provide insights, and maintain ongoing workflows.

- Chanakya Flask app on `http://127.0.0.1:5513`
- AIR service on `http://127.0.0.1:5512`
- Conversation layer on `http://127.0.0.1:5514`
- Optional A2A bridge on `http://127.0.0.1:18770` (future feature)

The most important setup rule is simple: create the repo-root `.env` and `mcp_config_file.json` before starting the stack. The startup scripts read those files immediately.

## Quick Start

### Prerequisites

- **Python 3.11+** or conda — required by all services
- **Docker** with `docker compose` plugin — required for:
  - Sandboxed code execution (artifact generation, work sandboxing)
  - Local TTS/STT via Speaches/Kokoro (optional)
- `uvx` — only needed if you use the example MCP config as-is

### 1. Run the setup script

```bash
./scripts/setup.sh
```

The script will walk you through:

- **Environment** — asks whether to use `conda` or `.venv`, then creates/activates it
- **Dependencies** — installs all packages (`pip install -e .[dev]`, AIR, conversation layer)
- **Config files** — creates `.env` and `mcp_config_file.json` from examples if they don't exist
- **LLM provider** — prompts for your LLM backend URL (e.g. `http://localhost:11434/v1`) and API key
- **TTS/STT (optional)** — if you choose yes, it pulls the Speaches Docker image, downloads Kokoro-82M (TTS) and Faster-Whisper (STT) models, and auto-configures the AIR `.env`

### 2. Start the stack

```bash
./scripts/start_chanakya_air.sh
```

Open the main UI at `http://127.0.0.1:5513`.

### 3. Stop the stack

```bash
./scripts/stop_chanakya_air.sh
```

This stops all services and (if set up) the TTS/STT Docker containers.

## What The Startup Script Does

`./scripts/start_chanakya_air.sh` starts the current local stack in this order:

1. AIR service
2. Chanakya conversation layer
3. Chanakya Flask app

It also:

- reads `.env` from the repo root unless `ENV_FILE_PATH` is already set
- auto-detects the Python binary (`.venv/bin/python` → conda → PATH)
- writes PID files and logs under `build/runtime/`
- prints the service URLs after startup

The stop script also stops TTS/STT Docker containers if `docker-compose.yml` is present.

Use `./scripts/stop_chanakya_air.sh` to stop everything cleanly.

## Required Configuration

### `.env`

Start from `.env.example` and copy it into place:

```bash
cp .env.example .env
```

Then edit `.env` with your local values. Replace all placeholder values marked with `<...>`:

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_BASE_URL` | Your LM Studio or OpenAI-compatible server endpoint | `http://127.0.0.1:1234/v1` |
| `OPENAI_API_KEY` | API key for your model server | `lm-studio` (or your key) |
| `DATABASE_URL` | Path to your SQLite database | `sqlite:////home/user/chanakya_data/chanakya.db` |

The defaults in `.env.example` use placeholder values and will not work out of the box.

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
pytest apps/chanakya/test
python -m ruff check apps/chanakya/
python -m mypy apps/chanakya/
```

For a focused test run:

```bash
pytest apps/chanakya/test/test_agent_manager.py -q
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

- `apps/chanakya/`: primary Flask app, routes, templates, core state, tests
- `apps/AI-Router-AIR/`: FastAPI service used by the local stack on port 5512
- `apps/chanakya_conversation_layer/`: separate conversation-layer package and tests
- `scripts/`: startup, shutdown, database, and service-management scripts

If you are changing runtime behavior, the most relevant files are usually:

- `apps/chanakya/core/app.py`
- `apps/chanakya/core/chat_service.py`
- `apps/chanakya/core/store.py`
- `apps/chanakya/agent/runtime.py`
- `apps/chanakya/templates/`
- `apps/chanakya/static/js/air_voice.js`

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
