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
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test
python -m pip install -e .[dev]
cp .env.example .env
cp mcp_config_file.example.json mcp_config_file.json
./scripts/start_chanakya_air.sh core
```

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

```bash
./scripts/stop_chanakya_air.sh
```

Logs and PID files are written to `build/runtime/`.

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
- `scripts/db_viewer.py` - inspect database contents
- `scripts/clear_database.py` - reset local database state
- `scripts/update_database.py` - apply local database updates

## Additional Docs

- `chanakya/README.md` - detailed Chanakya architecture and API documentation
- `AI-Router-AIR/README.md` - AIR server and dashboard documentation
- `chanakya_conversation_layer/README.md` - conversation-layer implementation details
