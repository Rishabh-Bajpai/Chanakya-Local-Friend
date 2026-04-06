# Chanakya

Chanakya is a task orchestration system powered by Microsoft Agent Framework. The active application lives in `chanakya/`.

## Quick Start

```bash
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test
cp .env.example .env
cp mcp_config_file.example.json mcp_config_file.json
./scripts/start_chanakya_air.sh
```

Open `http://localhost:5513` for Chanakya and `http://localhost:5512` for AIR.

## Core Paths

- `chanakya/` - Flask app, MAF runtime, orchestration, persistence, templates
- `chanakya/seeds/agents.json` - default persistent agent definitions
- `task.md` - milestone tracker and execution plan
- `tasks/prd-chanakya-full-system.md` - product requirements for the full system

## Configuration

Set values in `.env`:

- `OPENAI_BASE_URL` or `OPENAI_API_BASE`
- `OPENAI_API_KEY`
- `OPENAI_CHAT_MODEL_ID` (or `OPENAI_MODEL` / `MODEL`)
- `DATABASE_URL` (optional; defaults to local SQLite)

MCP servers are configured in `mcp_config_file.json`.

Default MCP servers now include:

- `mcp_websearch` (free DuckDuckGo web search)
- `mcp_fetch` (webpage fetching)
- `mcp_calculator` (calculator)
- `mcp_code_execution` (sandboxed code execution for developer/tester only)

Sandboxed code execution uses a shared persistent workspace under:

- `chanakya_data/shared_workspace/<work_id>`
- `chanakya_data/shared_workspace/temp` (fallback when no work id is available)

Code execution is container-only (Docker/Podman) and must not execute host-system commands.

## Sandbox Capabilities

Available:

- Execute Python and shell commands inside an isolated container
- Persist files across runs in `chanakya_data/shared_workspace/<work_id>` or `temp`
- Read host project files through read-only mounts inside the sandbox
- Use the shared workspace as the only writable location during sandbox execution
- Run with bounded CPU, memory, and pid count
- Use full network access from sandboxed code when external fetches are required
- Retry safely after permission errors by copying files into `/workspace`

Unavailable:

- Writing to host-mounted files or directories outside the shared workspace
- Running commands directly on the host system
- Privilege escalation or container capability expansion
- Arbitrary path traversal outside the sandbox workspace policy

Common permission behavior:

- Host files are readable but read-only inside the sandbox
- Only `/workspace` is writable in the container
- If an agent hits `Permission denied` or `Read-only file system`, it should copy the target file into the shared workspace and retry there

## Validation Commands

```bash
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test
python -m ruff check chanakya/
python -m mypy chanakya/
pytest chanakya/test
```

For detailed architecture and API documentation, see `chanakya/README.md`.
