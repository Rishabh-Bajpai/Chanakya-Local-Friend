# AGENTS.md

## Workspace Shape

- Primary app is `chanakya/`. Most active product work, tests, and Flask routes live there.
- `AI-Router-AIR/` is a separate FastAPI service used by the local stack on `:5512`.
- `chanakya_conversation_layer/` is a separate prototype package with its own `pyproject.toml` and tests; do not assume its commands or Python version match the root package.

## Startup / Runtime

- Preferred local stack entrypoint is `./scripts/start_chanakya_air.sh core` from repo root.
- `core+a2a` also starts OpenCode and the A2A bridge: `./scripts/start_chanakya_air.sh core+a2a`.
- Stop everything with `./scripts/stop_chanakya_air.sh`.
- Runtime logs and PID files go to `build/runtime/`.
- The startup script sources the repo-root `.env` automatically and exports `ENV_FILE_PATH`; do not manually duplicate env wiring unless you are bypassing the script.

## Environment / Setup

- Root package expects Python `>=3.10`; `chanakya_conversation_layer/` expects `>=3.11`.
- Standard setup in docs uses the `test` conda env:
  - `source /home/rishabh/miniconda3/etc/profile.d/conda.sh`
  - `conda activate test`
  - `python -m pip install -e .[dev]`
- Local runtime config is expected in root `.env` and `mcp_config_file.json`; both are gitignored.

## Verification Commands

- Root app checks (run from repo root):
  - `python -m ruff check chanakya/`
  - `python -m mypy chanakya/`
  - `pytest chanakya/test`
- Root pytest is already scoped to `chanakya/test` by `pyproject.toml`; use focused runs like `pytest chanakya/test/test_agent_manager.py -q`.
- `AI-Router-AIR/` has its own pytest config under `AI-Router-AIR/tests`.
- `chanakya_conversation_layer/` has its own pytest config under `chanakya_conversation_layer/tests`.

## Repo-Specific Gotchas

- `chanakya/seed.py` always refreshes seeded agent profiles on load. Editing `chanakya/seeds/agents.json` updates existing DB rows for matching seeded profiles; current behavior is overwrite, not merge, while preserving each profile's original `created_at`.
- `chanakya_data/` is runtime state, not source of truth. It contains the SQLite DB and shared sandbox workspace and is ignored by git.
- Sandboxed code execution writes only under `chanakya_data/shared_workspace/`; host project files may be mounted read-only.

## Architecture Shortcuts

- Main Flask app factory and startup wiring: `chanakya/app.py`.
- Chat orchestration and classic/delegated routing: `chanakya/chat_service.py`.
- Persistence layer and task/session/work records: `chanakya/store.py`.
- MAF runtime integration: `chanakya/agent/runtime.py`.
- Main classic/work UI is in `chanakya/templates/`; shared voice logic is in `chanakya/static/js/air_voice.js`.

## When Editing Behavior

- Check whether behavior is coming from:
  - backend routing/state in `chat_service.py`
  - persisted state in `store.py` / `chanakya_data/chanakya.db`
  - frontend delivery logic in `templates/index.html`, `templates/work.html`, and `static/js/air_voice.js`
- For delegated-work bugs, inspect both the main classic session and the linked `classic_active_works` / work session state before changing prompts; many failures here are state-flow bugs, not pure prompt bugs.
