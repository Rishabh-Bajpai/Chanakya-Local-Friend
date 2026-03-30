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
- [X] Remove duplicate `AgentProfile` domain/ORM definitions and use SQLAlchemy models directly for persisted agent records
- [X] Add provider-agnostic `DATABASE_URL` SQLAlchemy engine/session setup and remove path-bound DB initialization
- [X] Rename `chanakya/models.py` to `chanakya/domain.py` to separate app-domain types from ORM models
- [X] Replace manual chat prompt stitching with a MAF `BaseHistoryProvider` backed by SQLAlchemy chat history

- Validation:
  - Open the GUI
  - Send a normal message
  - Confirm a direct response is returned
  - Confirm request metadata and stored messages appear

### Milestone 2 - Tool Routing

- [X] Create separate MCP handling module (`chanakya/mcp_runtime.py`)
- [X] Add MCP tool loader and availability cache (`chanakya/services/tool_loader.py`)
- [X] Add MCP config loading + env merge helpers (`chanakya/services/config_loader.py`)
- [X] Add MCP stdout wrapper for noisy tool servers (`chanakya/services/mcp_wrapper.py`)
- [X] Add ToolInvocationModel for persistent tool traces (`chanakya/model.py`)
- [X] Add tool invocation repository methods to `chanakya/store.py`
- [X] Refactor runtime to unified agent-driven tool path in `chanakya/agent/runtime.py` (remove run_direct/run_chat split)
- [X] Update `chanakya/chat_service.py` to remove hardcoded route decisions and wire tool traces
- [X] Integrate MCP calculator tool (`githejie/mcp-server-calculator`)
- [X] Integrate MCP fetch tool (`zcaceres/fetch-mcp`)
- [X] Assign tool_ids to Chanakya agent profile in seeds
- [X] Add `/api/tool-traces` endpoint to `app.py`
- [X] Add Tool Traces panel to GUI showing selection, input, output, errors
- [ ] End-to-end validation with running MCP servers

- Validation:
  - Ask Chanakya to calculate an expression
  - Ask Chanakya to fetch a page summary
  - Confirm tool traces are visible in the GUI Tool Traces panel

### Milestone 3 - Domain Foundation

- [X] Add `RequestModel` and `TaskModel` SQLAlchemy schema with parent/child task support
- [X] Add task/request lifecycle status constants aligned to the PRD state model
- [X] Add append-only request/task event persistence with request/task/session linkage
- [X] Refactor `chanakya/store.py` into first-class repositories for chat, requests, tasks, events, tools, and agents
- [X] Update `ChatService` to create a persisted request and root task for each user message
- [X] Persist lifecycle transitions for request start, task start, completion, and failure
- [X] Add task-oriented read APIs for requests, tasks, and task timelines
- [X] Add GUI task list view showing root tasks and current lifecycle state
- [X] Add GUI task timeline view showing ordered domain events
- [X] Link request trace and tool traces to persisted request/task identifiers
- [X] Add focused tests for request/task creation, transitions, and read APIs

- Validation:
  - Create a request that becomes a persisted request record plus root task record
  - Confirm lifecycle transitions from `created` to `in_progress` to terminal state are visible in GUI
  - Confirm task timeline entries match the request execution path

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
- Completed: Milestone 3 domain foundation with persisted requests, tasks, lifecycle events, and GUI visibility
- In Progress: Milestone 4 agent manager delegation on top of persisted tasks
- Next: Milestone 5 persistent agent configuration after manager flow is visible

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

## Adding New MCP Servers

Use this flow to add a new MCP server (for example, search, filesystem, or custom internal tools):

1. Add a server entry in `mcp_config_file.json` under `mcpServers`.
2. Set `command`, `args`, `transport: "stdio"`, and optional `env` values.
3. Add the server id to an agent in `chanakya/seeds/agents.json` using `tool_ids`.
4. Restart the Flask app so `initialize_all_tools()` reconnects and caches the new MCP tools.
5. Verify from GUI:
   - Ask a prompt that should trigger the new tool.
   - Open Tool Traces panel (or `/api/tool-traces`) and confirm invocation records.

Example config entry:

```json
{
  "mcpServers": {
    "mcp_fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"],
      "transport": "stdio",
      "env": {}
    }
  }
}
```

Notes:
- Loader path: `chanakya/services/tool_loader.py`
- Config loader: `chanakya/services/config_loader.py`
- Noisy stdout is sanitized via `chanakya/services/mcp_wrapper.py` before MAF reads MCP JSON-RPC.

## Done Log

- 2026-03-26: Created phased task tracker from `tasks/prd-chanakya-full-system.md`.
- 2026-03-26: Started the new `chanakya/` full app instead of extending the MVP package.
- 2026-03-26: Added the Milestone 1 Flask GUI, direct MAF chat runtime, SQLite persistence, seed agent loading, and heartbeat file placeholders.
- 2026-03-26: Fixed GUI session persistence by restoring the last chat from SQLite on reload and added explicit per-panel scrollbars.
- 2026-03-26: Fixed panel layout sizing so chat, trace, agent, and event areas scroll independently within the viewport.
- 2026-03-26: Fixed multi-turn chat continuity by feeding recent stored session history back into the MAF runtime for follow-up requests.
- 2026-03-26: Added `CHANAKYA_DEBUG=true` support to print request flow, history, prompts, session state, and model responses to the terminal.
- 2026-03-26: Replaced direct sqlite3 access in the new app with SQLAlchemy ORM models and a session factory in `chanakya/model.py`.
- 2026-03-26: Removed duplicate agent profile DTO/ORM mapping and now use `AgentProfileModel` directly across the persistence layer.
- 2026-03-26: Added SQLAlchemy engine/session management via `DATABASE_URL` so the new app is no longer initialized around a SQLite file path.
- 2026-03-26: Renamed `chanakya/models.py` to `chanakya/domain.py` to reduce confusion between domain types and ORM models.
- 2026-03-27: Added a SQLAlchemy-backed MAF history provider and removed manual chat prompt reconstruction for multi-turn memory.
- 2026-03-28: Implemented Milestone 2 tool routing: separate MCP trace extraction module, MCP tool loader/config wrapper services, unified runtime (removed run_direct/run_chat split), agent-driven tool selection, tool trace persistence, GUI tool traces panel, and /api/tool-traces endpoint.
- 2026-03-29: Expanded Milestone 3 into request/task/task-event persistence, task-oriented repositories, GUI task visibility, and focused validation steps before Agent Manager work.
- 2026-03-30: Completed Milestone 3 by wiring persisted requests, root tasks, lifecycle events, request/task read APIs, GUI task panels, and focused domain-foundation tests.
- 2026-03-30: Fixed Milestone 3 panel restore/render issues by refreshing side panels after session history load and updating the right-side layout to allocate space for all five panels.
- 2026-03-30: Updated `scripts/update_database.py` to include Milestone 3 request/task/task-event models for schema updates on existing databases.
