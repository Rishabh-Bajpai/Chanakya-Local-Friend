# Chanakya MAF MVP

This repository contains a minimal feasibility MVP for the PRD in `PRD.md`.

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
- `run_scenarios.py`: scenario runner and transition report generation
- `webapp/app.py`: Flask backend + API endpoints for chat and traces
- `webapp/templates/index.html`: chat UI with execution trace timeline
- `docs/task_model.md`: task model definition
- `docs/maf_class_mapping.md`: MAF class-to-requirement mapping
- `docs/maf_capability_matrix.md`: US/FR fit matrix (Native vs glue vs custom)
- `docs/transition_records.md`: generated state transition records
- `RPD.md`: feasibility conclusion

## Environment

Use your conda environment:

```bash
conda activate test
```

Optional `.env` (OpenAI-compatible endpoint metadata, if configured):

- `OPENAI_BASE_URL` or `OPENAI_API_BASE`
- `OPENAI_API_KEY`
- `OPENAI_CHAT_MODEL_ID` (preferred) or `OPENAI_MODEL` / `MODEL`

The current MVP detects this config and reports it in direct-response evidence.

Model env key support includes `OPENAI_CHAT_MODEL_ID`.

## Run Scenarios

```bash
conda activate test
python run_scenarios.py
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

- `artifacts/tasks.db`
- `artifacts/events.jsonl`
- `docs/transition_records.md`

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
