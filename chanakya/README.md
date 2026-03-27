# Chanakya — Personal Multi-Agent Operating System

Chanakya is a **task orchestration system powered by Microsoft Agent Framework**. It provides a single user-facing assistant while a network of intelligent agents collaborates in the background to complete tasks, provide insights, and maintain ongoing workflows.

This is the full implementation tracked in `task_new.md`.

---

## Quick Start

```bash
# Activate your conda environment
source /home/rishabh/miniconda3/etc/profile.d/conda.sh
conda activate test

# Run the Flask app
python -m flask --app chanakya.app run --host 0.0.0.0 --port 5000
```

Open `http://localhost:5000` to access the GUI.

---

## What Is Chanakya?

Chanakya is **not a chatbot**. It is a task-driven operating system where:

- **Tasks are the source of truth** — not conversations
- **Agents are workers** — not decision-makers
- **The system controls orchestration** — not agents
- **Execution is delegated** — but control is centralized

### Core Principles

- Single interface, many agents
- Tasks persist; chat messages are supporting artifacts
- Domain layer owns state; MAF handles execution
- Every routing decision is logged for observability

---

## Architecture

```
┌─────────────────────────────────────┐
│           Flask GUI/API             │
│  (app.py — routes, templates)        │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│        ChatService                  │
│  (chat_service.py — routing)        │
└──────────────┬──────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
┌───────────┐  ┌───────────────┐
│ Direct    │  │ Tool /        │
│ Response  │  │ Delegation    │
└───────────┘  │ (future)      │
               └───────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│         MAFRuntime                  │
│  (maf_runtime.py — agent exec)      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│    Microsoft Agent Framework       │
│    (agent_framework package)       │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│        ChanakyaStore                │
│  (store.py — SQLite persistence)    │
└─────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Responsibility |
|-------|----------------|
| Flask GUI/API | User interaction, request intake, response rendering |
| ChatService | Request classification, routing, event logging |
| MAFRuntime | Agent execution via Microsoft Agent Framework |
| ChanakyaStore | SQLite persistence for sessions, messages, events, agent profiles |

---

## Project Structure

```
chanakya/
├── __init__.py           # Package marker
├── app.py                # Flask app factory, routes, startup logic
├── chat_service.py       # Request handling and routing logic
├── config.py             # Environment and configuration utilities
├── heartbeat.py          # Heartbeat file reading
├── maf_runtime.py       # MAF agent execution wrapper
├── models.py             # Data classes (AgentProfile, ChatReply)
├── seed.py               # Agent seed loading from JSON
├── store.py              # SQLite persistence layer
├── templates/
│   └── index.html        # GUI template
├── seeds/
│   └── agents.json       # Seed agent definitions
└── (runtime-created)
    ├── chanakya_data/    # Data directory (created at runtime)
    │   ├── chanakya.db   # SQLite database
    │   └── heartbeats/  # Heartbeat control files
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Render the GUI |
| `/api/chat` | POST | Send a chat message `{session_id?, message}` |
| `/api/sessions/<session_id>` | GET | Get all messages in a session |
| `/api/events` | GET | Get recent app events |
| `/api/agents` | GET | Get all agent profiles with heartbeat previews |

---

## Database Schema

### `chat_sessions`
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Session ID |
| title | TEXT | Session title |
| created_at | TEXT | ISO timestamp |
| updated_at | TEXT | ISO timestamp |

### `chat_messages`
| Column | Type | Description |
|--------|------|-------------|
| session_id | TEXT | FK to session |
| role | TEXT | `user` or `assistant` |
| content | TEXT | Message text |
| request_id | TEXT | Request ID |
| route | TEXT | Routing decision |
| metadata | TEXT | JSON blob |
| created_at | TEXT | ISO timestamp |

### `app_events`
| Column | Type | Description |
|--------|------|-------------|
| event_type | TEXT | Event type (e.g., `route_decision`, `chat_response`) |
| payload | TEXT | JSON blob |
| created_at | TEXT | ISO timestamp |

### `agent_profiles`
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Agent ID |
| name | TEXT | Display name |
| role | TEXT | Role (`personal_assistant`, `developer`, `tester`) |
| system_prompt | TEXT | Agent instructions |
| personality | TEXT | Personality description |
| tool_ids | TEXT | JSON array |
| workspace | TEXT | Workspace identifier |
| heartbeat_enabled | INTEGER | Boolean |
| heartbeat_interval_seconds | INTEGER | Interval |
| heartbeat_file_path | TEXT | Path to control file |
| is_active | INTEGER | Boolean |

---

## Configuration

### Environment Variables

Create a `.env` file in the repo root:

```bash
# OpenAI-compatible endpoint
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...

# Model (any of these work — first found is used)
OPENAI_CHAT_MODEL_ID=gpt-4o
OPENAI_MODEL=gpt-4
MODEL=gpt-4
```

The app reads `.env` automatically via `config.py`.

### Data Directory

The app creates `chanakya_data/` in the repo root on first run:

```
chanakya_data/
├── chanakya.db       # SQLite database
└── heartbeats/       # Heartbeat control files
    ├── chanakya.md
    ├── developer.md
    └── tester.md
```

---

## Seed Agents

The app loads three seed agents on startup from `chanakya/seeds/agents.json`:

| Agent ID | Name | Role |
|----------|------|------|
| `agent_chanakya` | Chanakya | Personal assistant (main user interface) |
| `agent_developer` | Developer | Worker for implementation tasks |
| `agent_tester` | Tester | Worker for validation tasks |

---

## Development

### Run the App

```bash
conda activate test
python -m flask --app chanakya.app run --host 0.0.0.0 --port 5000
```

### Run Lint and Typecheck

```bash
conda activate test
python -m ruff check chanakya/
python -m mypy chanakya/
```

---

## Milestones

| Milestone | Description | Status |
|-----------|-------------|--------|
| 1 | Simple Chanakya chat | Complete |
| 2 | Domain foundation (tasks, events, lifecycle) | Next |
| 3 | Tool routing (calculator, fetch) | Pending |
| 4 | Agent Manager v1 (delegation, decomposition) | Pending |
| 5 | Persistent agent configuration | Pending |
| 6 | Temporary subagents | Pending |
| 7 | User input loop (pause/resume) | Pending |
| 8 | Scheduling and heartbeat | Pending |
| 9 | Direct agent interaction | Pending |
| 10 | Hardening and demo flow | Pending |

Full milestone details in `task_new.md`.

---

## Related Files

- `task_new.md` — Execution tracker with milestones, risks, and delivery rules
- `tasks/prd-chanakya-full-system.md` — Product Requirements Document
- `chanakya_mvp/` — Reference MVP (to be removed after full app is complete)
