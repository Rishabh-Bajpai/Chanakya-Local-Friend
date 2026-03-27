# Chanakya MAF MVP

The repo now contains two tracks:

- `chanakya/`: the new full app under active development
- `chanakya_mvp/`: the earlier feasibility MVP kept temporarily as reference

## New Full App

Run the current full-app milestone:

```bash
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test
export CHANAKYA_DEBUG=true
python -m flask --app chanakya.app run --host 0.0.0.0 --port 5000
```

Open `http://localhost:5000` and validate the direct chat flow.

When `CHANAKYA_DEBUG=true`, the app prints important runtime details to the terminal, including request payloads, stored chat history, prompt construction, MAF session state, and model responses.

Execution plan is tracked in `task.md`.

This repository contains a minimal feasibility MVP for the PRD in `tasks/rpd-chanakya-maf-mvp-feasibility.md`.

It validates:

- single user-facing assistant routing,
- direct/tool/delegated execution paths,
- parent/child task decomposition,
- dependency enforcement,
- persistent task/state tracking,
- waiting-input pause/resume,
- final aggregation and user-facing result reporting.

## Project Structure

- `chanakya_mvp/chanakya.py`: user-facing PA entrypoint and routing
- `chanakya_mvp/manager.py`: Agent Manager orchestration
- `chanakya_mvp/agents.py`: Developer and Tester agents
- `chanakya_mvp/tools.py`: weather tool
- `chanakya_mvp/store.py`: SQLite task persistence and transition history
- `chanakya_mvp/scenarios.py`: TS-001 to TS-007 scenario implementations
- `chanakya_mvp/testing/run_scenarios.py`: scenario runner and transition report generation
- `webapp/app.py`: Flask backend + API endpoints for chat and traces
- `webapp/templates/index.html`: chat UI with execution trace timeline
- `chanakya_mvp/testing/docs/task_model.md`: task model definition
- `chanakya_mvp/testing/docs/maf_class_mapping.md`: MAF class-to-requirement mapping
- `chanakya_mvp/testing/docs/maf_capability_matrix.md`: US/FR fit matrix (Native vs glue vs custom)
- `chanakya_mvp/testing/docs/transition_records.md`: generated state transition records
- `tasks/prd-chanakya-full-system.md`: full-system PRD

## Environment

Use your conda environment:

```bash
conda activate test
```

Optional `.env` (OpenAI-compatible endpoint metadata, if configured):

- `OPENAI_BASE_URL` or `OPENAI_API_BASE`
- `OPENAI_API_KEY`
- `OPENAI_CHAT_MODEL_ID` (preferred) or `OPENAI_MODEL` / `MODEL`
- `DATABASE_URL` (optional; defaults to local SQLite via SQLAlchemy)

The current MVP detects this config and reports it in direct-response evidence.

Model env key support includes `OPENAI_CHAT_MODEL_ID`.

The new full app reads `DATABASE_URL` through SQLAlchemy, so switching to another SQL provider should not require store-layer code changes.

## Run Scenarios

```bash
conda activate test
python -m chanakya_mvp.testing.run_scenarios
```

Expected outcomes:

- TS-001 direct response: pass
- TS-002 weather tool: pass
- TS-003 delegated workflow: pass
- TS-004 dependency enforcement: pass
- TS-005 failure path: pass
- TS-006 waiting input and resume: pass
- TS-007 final aggregation: pass

Artifacts written to:

- `chanakya_mvp/testing/artifacts/tasks.db`
- `chanakya_mvp/testing/artifacts/events.jsonl`
- `chanakya_mvp/testing/docs/transition_records.md`

## Run Flask UI

```bash
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test
python -m flask --app webapp.app run --host 0.0.0.0 --port 5000
```

Open `http://localhost:5000`.

UI behavior:

- left panel: user/assistant chat
- right top: execution trace events (route/tool/delegation/runtime logs)
- right bottom: parent + child task state transition timeline from SQLite store

## Lint and Typecheck

```bash
conda activate test
python -m ruff check .
python -m mypy .
```
