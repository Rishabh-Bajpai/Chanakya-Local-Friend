# Chanakya Full App Task Tracker

## Source

- PRD: `tasks/prd-chanakya-full-system.md`
- Build approach: new full app in `chanakya/`
- MVP status: keep `chanakya_mvp/` as reference only until removed

## Product Direction

- Chanakya is a task orchestration system powered by MAF agents.
- Tasks remain the source of truth.
- The GUI is the primary validation loop for each milestone.
- Persistent agents will be supported through both GUI configuration and a seed file.
- Heartbeat behavior will use per-agent heartbeat files that are read at the configured interval.

## Architecture Decisions

- App package: `chanakya/`
- Web stack: Flask app factory + server-rendered HTML + JSON APIs
- Agent runtime: Microsoft Agent Framework (`agent_framework`) for all agents
- Persistence: SQLite for sessions, messages, events, and agent profiles
- Seed source: `chanakya/seeds/agents.json`
- Heartbeat storage: file path per agent, read on interval by a heartbeat service

## Milestones

Milestone 1 - Simple Chanakya Chat

- [X] Create new `chanakya/` package
- [X] Add Flask app and GUI for direct chat validation
- [X] Use MAF `Agent` + `OpenAIChatClient` for direct responses
- [X] Persist chat sessions, messages, request events, and seed agents
- [X] Restore persisted chat history in the GUI across page reloads
- [X] Reuse stored session history for multi-turn follow-up responses
- [X] Show route/runtime metadata in GUI
- [X] Add scrollable panels for chat, agents, and events
- [X] Add `CHANAKYA_DEBUG` terminal tracing for important runtime state and values
- [X] Move the new app DB layer to SQLAlchemy models in `chanakya/model.py`

- Validation:
  - Open the GUI
  - Send a normal message
  - Confirm a direct response is returned
  - Confirm request metadata and stored messages appear

### Milestone 2 - Domain Foundation

- [ ] Expand the task schema for parent/child tasks and history
- [ ] Add first-class request, task, and event repositories
- [ ] Add task list and event timeline views in GUI

- Validation:
  - Create a request that becomes a persisted task record
  - Confirm lifecycle transitions are visible in GUI

### Milestone 3 - Tool Routing

- [ ] Add internal tool registry
- [ ] Integrate MCP calculator tool
- [ ] Integrate MCP fetch tool
- [ ] Show tool selection, input, output, and errors in GUI

- Validation:
  - Ask Chanakya to calculate an expression
  - Ask Chanakya to fetch a page summary
  - Confirm tool traces are visible

### Milestone 4 - Agent Manager v1

- [ ] Add manager delegation for complex requests
- [ ] Create parent task, subtasks, and dependency edges
- [ ] Use MAF worker agents for delegated execution
- [ ] Show task graph and execution progress in GUI

- Validation:
  - Submit an implement-and-test request
  - Confirm decomposition and dependency ordering in GUI

### Milestone 5 - Persistent Agent Configuration

- [ ] Add GUI for creating and editing persistent agents
- [ ] Persist role, personality, tools, workspace, heartbeat settings
- [ ] Use stored agents during manager selection

- Validation:
  - Create a developer and tester in GUI
  - Run a delegated task using those saved agents

### Milestone 6 - Temporary Subagents

- [ ] Add ephemeral subagent creation and cleanup
- [ ] Record parent agent, purpose, and lifetime

- Validation:
  - Trigger a task that spawns a helper subagent
  - Confirm cleanup is visible in GUI

### Milestone 7 - User Input Loop

- [ ] Add waiting-for-input state and resume flow
- [ ] Add retry, cancel, and manual unblock controls

- Validation:
  - Trigger clarification request
  - Reply in GUI and confirm task resumes

### Milestone 8 - Social and Isolated Agents

- [ ] Add direct interaction with persistent social agents
- [ ] Add social circles and isolation policies

- Validation:
  - Start a direct conversation with a social agent
  - Confirm isolation rules are enforced

### Milestone 9 - Scheduling and Heartbeat

- [ ] Add recurring schedules and background execution logs
- [ ] Add heartbeat service that reads each agent heartbeat file on interval
- [ ] Surface heartbeat decisions in GUI

- Validation:
  - Configure a heartbeat-enabled agent
  - Confirm periodic reads and resulting actions are visible

### Milestone 10 - Hardening and Demo Flow

- [ ] Add focused tests for routing, state transitions, tools, and scheduling
- [ ] Improve GUI observability and operator controls
- [ ] Document runbooks and demo steps

## Current Focus

- Completed: Milestone 1 foundation and simple chatbot
- Next: Milestone 2 domain foundation for full task orchestration

## GUI Review Loop

- After each milestone, validate from the GUI before moving on.
- Keep each milestone shippable and demoable.
- Update this file immediately after each completed feature or scope change.

## Heartbeat Notes

- Each persistent agent will have:
  - `heartbeat_enabled`
  - `heartbeat_interval_seconds`
  - `heartbeat_file_path`
- The heartbeat service will read the file contents periodically and decide if work is required.
- Heartbeat files will be the lightweight control surface for pending work, reminders, or operating instructions.

## Done Log

- 2026-03-26: Created phased task tracker from `tasks/prd-chanakya-full-system.md`.
- 2026-03-26: Started the new `chanakya/` full app instead of extending the MVP package.
- 2026-03-26: Added the Milestone 1 Flask GUI, direct MAF chat runtime, SQLite persistence, seed agent loading, and heartbeat file placeholders.
- 2026-03-26: Fixed GUI session persistence by restoring the last chat from SQLite on reload and added explicit per-panel scrollbars.
- 2026-03-26: Fixed panel layout sizing so chat, trace, agent, and event areas scroll independently within the viewport.
- 2026-03-26: Fixed multi-turn chat continuity by feeding recent stored session history back into the MAF runtime for follow-up requests.
- 2026-03-26: Added `CHANAKYA_DEBUG=true` support to print request flow, history, prompts, session state, and model responses to the terminal.
- 2026-03-26: Replaced direct sqlite3 access in the new app with SQLAlchemy ORM models and a session factory in `chanakya/model.py`.
