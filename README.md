# MAF Demo Workspace

This repository is a multi-project workspace centered on the Chanakya agent system and related Microsoft Agent Framework experiments.

The primary app is `chanakya/`, with supporting services in `AI-Router-AIR/` and `chanakya_conversation_layer/`.

## Repository Layout

- `chanakya/` - main Flask app, orchestration runtime, persistence, templates, and MCP integration
- `AI-Router-AIR/` - OpenAI-compatible routing and admin dashboard used by the local stack
- `chanakya_conversation_layer/` - separate conversation-layer prototype and SDK-style implementation
- `scripts/` - start/stop helpers and developer utilities
- `chanakya_data/` - runtime data such as the SQLite database and shared sandbox workspace
- `task.md` - implementation tracker
- `tasks/` - design notes, PRDs, and supporting docs

## Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python -m pip install -e ./AI-Router-AIR
python -m pip install -e ./chanakya_conversation_layer
cp .env.example .env
cp mcp_config_file.example.json mcp_config_file.json
./scripts/start_chanakya_air.sh core
```

If you prefer `conda` for local development, that still works. The `systemd` service install described below strictly requires a repo-root `.venv`.

The startup script can launch either the core stack or the core stack plus A2A:

- `./scripts/start_chanakya_air.sh core`
- `./scripts/start_chanakya_air.sh core+a2a`

Core mode launches three services:

- AIR dashboard: `http://localhost:5512`
- Chanakya app: `http://localhost:5513`
- Chanakya conversation layer: `http://127.0.0.1:5514`

`core+a2a` also launches:

- OpenCode server: `http://127.0.0.1:18496`
- A2A bridge: `http://127.0.0.1:18770`

Stop the stack with:

- `mcp_websearch` (free DuckDuckGo web search)
- `mcp_fetch` (webpage fetching)
- `mcp_calculator` (calculator)
- `mcp_weather` (free weather via `wttr.in`)
- `mcp_map` (free OpenStreetMap geocoding and routing)
- `mcp_timer` (scheduler-backed reminders and scheduled tasks)
- `mcp_code_execution` (sandboxed code execution for developer/tester only)

Logs and PID files are written to `build/runtime/`.

## Systemd Service

The repo includes Ubuntu/Linux `systemd` support for the Chanakya `core` stack.

This installs services for:

- AIR server
- Chanakya conversation layer
- Chanakya app

The installer strictly requires a repo-root virtual environment at `.venv`.

### Prepare The Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python -m pip install -e ./AI-Router-AIR
python -m pip install -e ./chanakya_conversation_layer
```

### Install The Service

```bash
sudo ./scripts/install-autostart-ubuntu.sh
```

Optional user override:

```bash
sudo ./scripts/install-autostart-ubuntu.sh --user <username>
```

Created units:

- `chanakya-air.service`
- `chanakya-conversation-layer.service`
- `chanakya-app.service`
- `chanakya.target`

Useful commands:

```bash
sudo systemctl status chanakya.target
sudo journalctl -u chanakya-air.service -f
sudo journalctl -u chanakya-conversation-layer.service -f
sudo journalctl -u chanakya-app.service -f
```

Restart after code changes:

```bash
sudo systemctl restart chanakya.target
```

If you change unit definitions:

```bash
sudo systemctl daemon-reload
sudo systemctl restart chanakya.target
```

Uninstall:

```bash
sudo ./scripts/uninstall-autostart-ubuntu.sh
```

## Configuration

Root configuration lives in `.env` and `mcp_config_file.json`.

Important `.env` values:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_CHAT_MODEL_ID`
- `OPENAI_RESPONSES_MODEL_ID`
- `DATABASE_URL`
- `CHANAKYA_DEBUG`

MCP servers are configured in `mcp_config_file.json` under `mcpServers`.

## Sandbox Notes

Sandboxed code execution uses the shared workspace under `chanakya_data/shared_workspace/`.

- Host project files may be mounted read-only into the sandbox
- Only the sandbox workspace is writable during execution
- Docker or Podman is required for containerized code execution

## Development

Run the main validation commands from the repo root:

```bash
python -m ruff check chanakya/
python -m mypy chanakya/
pytest chanakya/test
```

Helpful scripts:

- `scripts/start_chanakya_air.sh` - start the core stack, or `core+a2a` to include OpenCode and the A2A bridge
- `scripts/stop_chanakya_air.sh` - stop the local stack
- `scripts/install-autostart-ubuntu.sh` - install the core stack as `systemd` services
- `scripts/uninstall-autostart-ubuntu.sh` - remove installed `systemd` services
- `scripts/db_viewer.py` - inspect database contents
- `scripts/clear_database.py` - reset local database state
- `scripts/update_database.py` - apply local database updates

## Additional Docs

- `chanakya/README.md` - detailed Chanakya architecture and API documentation
- `AI-Router-AIR/README.md` - AIR server and dashboard documentation
- `chanakya_conversation_layer/README.md` - conversation-layer implementation details
