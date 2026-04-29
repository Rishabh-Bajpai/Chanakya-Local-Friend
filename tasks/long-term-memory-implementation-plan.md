# Implementation Plan: Long-Term Memory for Classic Chat

**Status:** In Progress
**Created:** 2026-04-28
**Last Updated:** 2026-04-28

---

## Goal

Add durable long-term memory to the classic single-chat experience so the agent can:

1. preserve relevant user, agent, and project facts beyond the rolling short-term context,
2. update memory automatically in the background as the conversation evolves,
3. retrieve relevant memory when answering future questions,
4. support add, update, supersede, and delete behavior safely,
5. keep full raw chat history as the source-of-truth audit trail.

This plan is tailored to the current Chanakya architecture and avoids introducing a large new subsystem unless it is needed.

---

## Current Repo Facts

These implementation constraints were verified in the current codebase:

1. Full chat history is already persisted in `chat_messages` through `chanakya/store.py`.
2. The main local runtime already supports MAF `context_providers` in `chanakya/agent/runtime.py`.
3. MCP tools are already first-class and loaded from stdio servers through `chanakya/services/tool_loader.py`.
4. The current history path is not strictly "last 12 messages only" in code.
5. `SQLAlchemyHistoryProvider` already performs compressed retrieval from full history in `chanakya/history_provider.py`.
6. Default history settings today are:
   - `CHANAKYA_HISTORY_RECENT_WINDOW_MESSAGES=16`
   - `CHANAKYA_HISTORY_MAX_MESSAGES=48`
   - `CHANAKYA_HISTORY_MAX_CHARS=24000`
7. `chat_service.py` still records recent history slices for logging/debug, but that is not the full runtime memory strategy.
8. Database schema is currently created via `Base.metadata.create_all(...)` in `chanakya/db.py`, so adding SQLAlchemy models is enough for local schema creation in this repo.

---

## Product Decision

Implement long-term memory as:

- a structured SQL-backed memory store in the existing app database,
- a dedicated internal memory manager MAF agent for memory reasoning,
- a single high-level MCP memory-agent tool for Chanakya,
- a background post-turn memory update pipeline that uses the memory manager,
- debug APIs and UI for inspection,
- optional vector search later, not in the initial version.

Do not use markdown files as the primary memory source.

Markdown may still be used later for:

- export,
- debugging,
- manual inspection,
- backups.

---

## Memory Model

Memory is curated durable state, not compressed transcript history.

### What should be stored

- User profile facts
- User preferences
- Durable instructions
- Active project facts
- Stable environment details
- Agent-side durable notes that help future responses
- Facts explicitly marked as important by the user
- Facts repeatedly referenced across the session

### What should not be stored

- Every single turn
- Temporary debugging details with no future value
- Tool noise or transient logs
- Low-confidence guesses
- Full raw conversations
- Sensitive information unless intentionally needed and explicitly acceptable

### Memory scopes

- `user`: facts about the user
- `agent`: durable facts about how the assistant should behave for this user/app
- `shared`: durable facts about the project, environment, workflows, or ongoing work

### Memory types

- `profile`
- `preference`
- `project`
- `instruction`
- `fact`
- `relationship`
- `agent_note`

---

## Initial Data Model

Add two new tables in `chanakya/model.py`.

### `MemoryRecordModel`

Recommended fields:

- `id: str` primary key
- `owner_id: str`
- `session_id: str | None`
- `scope: str`
- `type: str`
- `subject: str`
- `content: str`
- `importance: int`
- `confidence: float` or bounded numeric string if staying simple with SQLite typing
- `status: str`
- `source_message_ids_json: list[int | str]`
- `source_request_ids_json: list[str]`
- `derived_from_memory_id: str | None`
- `supersedes_memory_id: str | None`
- `expires_at: str | None`
- `created_at: str`
- `updated_at: str`

Status values:

- `active`
- `superseded`
- `deleted`

### `MemoryEventModel`

Recommended fields:

- `id: int` primary key
- `memory_id: str | None`
- `owner_id: str`
- `session_id: str | None`
- `request_id: str | None`
- `event_type: str`
- `payload_json: dict[str, Any]`
- `created_at: str`

Event types:

- `memory_added`
- `memory_updated`
- `memory_superseded`
- `memory_deleted`
- `memory_extraction_skipped`
- `memory_extraction_failed`
- `memory_retrieved`

### Owner strategy for v1

Because the current product is a single personal assistant page/session, use a stable `owner_id` even if there is only one user today.

Recommended v1 value:

- `owner_id = "default_user"`

This keeps the schema future-safe without forcing full multi-user work now.

---

## Repository Layer Changes

Add memory repository methods to `chanakya/store.py` and expose them through `ChanakyaStore`.

### New repository surface

- `add_memory(...)`
- `update_memory(...)`
- `supersede_memory(...)`
- `delete_memory(...)`
- `get_memory(memory_id)`
- `list_memories(owner_id, status="active", limit=...)`
- `search_memories(owner_id, query, types=None, limit=...)`
- `list_recent_memories(owner_id, limit=...)`
- `create_memory_event(...)`
- `list_memory_events(owner_id, limit=...)`

### Search behavior for v1

Do not block v1 on embeddings.

Initial search can combine:

1. active-memory filtering,
2. token overlap against `subject + content`,
3. importance weighting,
4. recency weighting,
5. exact-type filtering.

This is enough to ship a useful first version.

---

## Runtime Integration Strategy

Chanakya should not receive low-level memory CRUD tools.

### Path A: background memory manager updates

After each completed chat turn, the application triggers the memory manager MAF agent in the background.

The memory manager:

1. receives the recent turn slice,
2. receives the current memory state,
3. decides add/update/delete/noop,
4. returns structured JSON operations,
5. the application validates and applies those operations.

### Path B: explicit memory-agent tool usage by Chanakya

Chanakya sees only one high-level MCP tool such as:

- `mcp_memory_agent_memory_agent_request`

Chanakya does not manage memory directly.

Use cases:

- "what do you remember about..."
- "forget that"
- "update my preference"
- "summarize what you know about my app"
- "remember this"

---

## Preferred V1 Wiring

Use minimal changes to the current runtime while moving write decisions to the memory manager agent.

### Option selected now

1. keep app-side structured memory storage,
2. replace heuristic memory writes with a dedicated memory manager MAF agent,
3. expose a single MCP memory-agent tool to Chanakya,
4. add a prompt instruction telling Chanakya to use that tool for memory-related actions,
5. keep debug visibility through API and UI.

### Important constraint

Chanakya should not receive direct memory CRUD tools such as list/add/delete/update.

The memory manager agent owns memory reasoning.

---

## Background Memory Update Pipeline

Memory extraction should not slow down the user-facing reply path.

### Recommendation

Do not run a periodic standalone memory agent as the primary mechanism.

Use event-driven background updates after each completed turn.

The background worker should call the dedicated memory manager agent immediately after reply persistence.

### Why not periodic-only in v1

Periodic-only memory updates have avoidable drawbacks:

1. memory can lag behind the latest turn,
2. multiple turns may pile up and become harder to attribute cleanly,
3. explicit user requests like "remember this" or "forget that" should apply quickly,
4. the app already has a natural per-turn completion hook in `chat_service.py`.

### Dedicated memory manager now

The current implementation direction is to add the dedicated memory manager now, but keep it narrow:

1. one focused MAF agent,
2. one structured JSON output contract,
3. one high-level MCP tool surface,
4. application-side validation before writes.

### Required behavior

After a user message and assistant reply are stored:

1. gather the recent message window relevant to the completed turn,
2. run a memory extractor,
3. emit proposed operations as strict JSON,
4. validate those operations in application code,
5. apply add/update/supersede/delete changes,
6. write memory events for observability.

### Exact per-turn flow

Recommended flow:

1. user sends a message,
2. main assistant replies normally,
3. `chat_service.py` persists the user and assistant messages,
4. `chat_service.py` schedules a background memory update job for that completed turn,
5. the memory manager reads only a bounded recent slice, for example:
   - current user message,
   - assistant reply messages for the same request,
   - optionally a few recent preceding messages for contradiction detection,
6. the memory manager returns JSON operations,
7. `memory_service.py` validates, dedupes, and applies them,
8. the next user turn can retrieve from the updated memory store.

This gives near-immediate memory updates without blocking the response.

### Do not do this inline inside the main answer generation path

The user should get the answer first.
Memory maintenance should happen after.

### Initial execution model

Use an in-process background worker path first.

Examples:

- a thread-based fire-and-forget worker,
- an async background job on the existing loop if a safe integration point already exists,
- a lightweight service object called after response persistence.

Do not introduce Celery, Redis, or an external queue in v1.

### Recommended implementation shape

Add an internal component such as:

- `MemoryManagerService`

Responsibilities:

1. accept `session_id`, `request_id`, and source message ids,
2. load the bounded source messages,
3. call the dedicated memory manager MAF agent,
4. validate operations,
5. apply memory writes,
6. log success/failure events.

This component can be invoked from `chat_service.py` after response persistence.

### Should it ever run periodically?

Yes, but periodic execution should be additive, not the main mechanism.

Recommended periodic jobs for v2:

1. retry failed extraction jobs,
2. compact or merge duplicate memories,
3. expire stale memories,
4. recompute embeddings if embeddings are added later,
5. run low-priority maintenance passes over older transcript windows.

So the design should support both:

- event-driven per-turn updates for freshness,
- periodic maintenance jobs for cleanup and repair.

### Write authority rule

The memory manager agent does not write to the database directly.

It proposes operations.
Application code validates and applies them.

---

## Memory Extraction Contract

The extractor must return strict JSON only.

### Proposed output format

```json
{
  "operations": [
    {
      "op": "add",
      "scope": "shared",
      "type": "project",
      "subject": "personal assistant app",
      "content": "User is building a personal assistant app using Microsoft Agent Framework, an OpenAI-compatible endpoint, and MCP tools.",
      "importance": 4,
      "confidence": 0.96,
      "source_message_ids": [123, 124],
      "source_request_ids": ["req_123"]
    }
  ]
}
```

### Allowed ops

- `add`
- `update`
- `supersede`
- `delete`
- `noop`

### Validation rules

- reject unknown keys or malformed payloads,
- bound `importance` to 1-5,
- bound `confidence` to 0-1,
- require `subject` and `content` for add/update,
- never delete raw transcript history,
- apply delete only to memory records,
- ignore empty or low-value content,
- never store massive text blobs as memory,
- dedupe near-identical active memories before insert.

---

## Memory Update Rules

### Add

Add when the turn contains durable information likely to matter later.

Examples:

- app architecture facts,
- ongoing project details,
- preferred style of help,
- explicit instructions like "remember that...",
- stable environment choices.

### Update

Update when the same fact is refined but still refers to the same durable record.

Example:

- old: user app uses only SQLite
- new: user now uses SQLite and a memory MCP server

### Supersede

Supersede when a newer fact replaces a prior active one.

Example:

- old: memory uses SQLite only
- new: memory migrated to Postgres

### Delete

Delete when:

- user explicitly says to forget something,
- the fact is no longer true and should not appear again,
- a prior low-confidence memory is clearly wrong.

Use soft-delete semantics in storage by setting `status="deleted"`.

---

## Retrieval Rules

Memory retrieval should stay compact and selective.

### Before each main run

- use the newest user message as the primary query,
- optionally combine with the current work/request summary,
- fetch top relevant active memories,
- include a small number of high-value profile/instruction memories,
- cap total injected memory text.

### Suggested V1 limits

- `top_k = 6`
- max injected memory items: `8`
- max injected memory chars: `2000-3000`

### Retrieval ordering heuristic

Sort by a combined score of:

- lexical relevance,
- importance,
- recency,
- confidence.

### Never inject

- deleted memories,
- superseded memories,
- very low-confidence memories,
- verbose memory bodies that are really transcript chunks.

---

## MCP Memory-Agent Server Plan

Add a new stdio MCP server under `chanakya/services/` following the existing server patterns.

### New file

- `chanakya/services/mcp_memory_agent_server.py`

### Initial tool set

Expose only one tool to Chanakya:

- `memory_agent_request(memory_request: str)`

### Tool policy

Chanakya should only see the single memory-agent tool.

Low-level list/add/update/delete operations stay internal to the memory manager implementation.

### Config integration

Add the memory server to MCP configuration and tool loading flows.

Likely touchpoints:

- `mcp_config_file.json`
- `chanakya/services/tool_loader.py`
- agent profiles / default tool assignment logic if the core assistant should always have memory tools.

---

## File-by-File Implementation Tasks

## Phase 1: Data Model and Store

- [ ] Add `MemoryRecordModel` to `chanakya/model.py`
- [ ] Add `MemoryEventModel` to `chanakya/model.py`
- [ ] Export and wire the new models where needed
- [ ] Add a `MemoryRepository` to `chanakya/store.py`
- [ ] Add memory repository access through `ChanakyaStore`
- [ ] Add search, list, add, update, supersede, and soft-delete operations
- [ ] Add memory event logging helpers
- [ ] Add model/store unit tests for CRUD, supersede, and delete semantics

## Phase 2: Memory Manager Service

- [ ] Add a new service module such as `chanakya/services/memory_manager_service.py`
- [ ] Define typed payload shapes for memory-agent operations
- [ ] Implement normalization and validation helpers for memory-agent output
- [ ] Implement dedupe logic for near-identical active memories
- [ ] Implement lexical retrieval scoring for v1 search
- [ ] Implement a compact memory-summary formatter for prompt injection
- [ ] Add service tests for validation, dedupe, and scoring

## Phase 3: Background Extraction

- [ ] Replace heuristic updater with the dedicated memory manager MAF agent
- [ ] Create a strict memory-manager prompt that produces JSON only
- [ ] Add a parser for memory-manager JSON output with failure handling
- [ ] Add a post-response trigger point in `chanakya/chat_service.py`
- [ ] Capture the source message ids and request id for each extraction pass
- [ ] Run the memory manager outside the main user-response critical path
- [ ] Apply validated operations through `memory_service.py`
- [ ] Log `memory_extraction_failed` events without breaking chat replies
- [ ] Add tests proving background extraction failures do not break chat

## Phase 4: Retrieval and Prompt Injection

- [ ] Add a retrieval method that accepts `owner_id`, `session_id`, and current user query
- [ ] Build compact injected memory text from top relevant memories
- [ ] Wire memory retrieval into `chanakya/chat_service.py` before runtime invocation
- [ ] Pass memory text through existing `prompt_addendum` support in `chanakya/agent/runtime.py`
- [ ] Preserve existing transcript history behavior in `SQLAlchemyHistoryProvider`
- [ ] Add observability for retrieved memory ids and counts
- [ ] Add tests verifying relevant memory is injected and irrelevant memory is excluded

## Phase 5: MCP Memory-Agent Tool

- [ ] Add `chanakya/services/mcp_memory_agent_server.py`
- [ ] Implement the single `memory_agent_request(memory_request: str)` tool
- [ ] Make the tool invoke the dedicated memory manager service
- [ ] Add memory-agent server startup entrypoint consistent with other MCP servers
- [ ] Register the memory-agent server in local MCP configuration
- [ ] Ensure the core assistant profile gets only the single memory-agent tool if configured
- [ ] Add MCP server tests similar to existing MCP tool server coverage

## Phase 6: API and Debug Visibility

- [ ] Add debug/admin APIs for listing memories and memory events if useful
- [ ] Optionally add `/api/memory` and `/api/memory/events` routes in `chanakya/app.py`
- [ ] Add safe response payloads for memory inspection in the UI or debugging tools
- [ ] Keep these endpoints minimal and internal-facing unless a product surface is required

## Phase 7: Prompt and Policy Updates

- [ ] Update core assistant prompt guidance so it knows the memory-agent tool exists
- [ ] Instruct the assistant not to manage memory directly
- [ ] Instruct the assistant to use the memory-agent tool for explicit recall or forgetting
- [ ] Add rules for handling "forget this" and "remember this" through the memory-agent tool

## Phase 8: Verification

- [ ] Add unit tests for models, repositories, extraction parsing, dedupe, and retrieval
- [ ] Add integration tests covering end-to-end memory add and later recall
- [ ] Add integration tests covering contradiction and supersede flows
- [ ] Add integration tests covering explicit forget/delete flows
- [ ] Add integration tests covering MCP memory tool behavior
- [ ] Run `python -m ruff check chanakya/`
- [ ] Run `python -m mypy chanakya/`
- [ ] Run focused `pytest` for changed areas first
- [ ] Run broader `pytest chanakya/test`

---

## Suggested New Files

- [ ] `chanakya/services/memory_manager_service.py`
- [ ] `chanakya/services/mcp_memory_agent_server.py`
- [ ] `chanakya/test/test_memory_service.py`
- [ ] `chanakya/test/test_memory_extractor.py`
- [ ] `chanakya/test/test_mcp_memory_server.py`
- [ ] `chanakya/test/test_long_term_memory_integration.py`

---

## Suggested Existing Files to Modify

- [ ] `chanakya/model.py`
- [ ] `chanakya/store.py`
- [ ] `chanakya/chat_service.py`
- [ ] `chanakya/agent/runtime.py`
- [ ] `chanakya/services/tool_loader.py` if registration logic needs updates
- [ ] `chanakya/app.py` if adding debug APIs
- [ ] agent profile seed/default tool wiring if memory tools should be baseline

---

## Implementation Order

Use this order to reduce risk and keep the system shippable at each step.

### Milestone 1: durable store only

- add models
- add repository methods
- add tests

### Milestone 2: retrieval-only memory

- seed some manual memories
- inject relevant memories before runs
- verify answer quality improvements

### Milestone 3: automatic background extraction

- enable extractor writes
- add event logging and failure isolation

### Milestone 4: MCP memory tools

- enable explicit search, remember, and forget operations

### Milestone 5: optional semantic search

- add embeddings only if lexical retrieval is no longer enough

---

## Prompting Guidance for Extractor

The extractor prompt should explicitly say:

1. store only durable facts likely to matter later,
2. do not summarize the entire conversation,
3. prefer updating or superseding over duplicating,
4. ignore temporary or low-value details,
5. output JSON only,
6. never invent facts not present in the source turns,
7. mark low-confidence candidates conservatively.

---

## Observability Requirements

Add debug logging and events for:

- extraction job start/end,
- extraction duration,
- operations proposed,
- operations applied,
- dedupe suppressions,
- retrieval query text,
- retrieved memory ids,
- injected memory char count,
- failures and fallbacks.

This is important because memory bugs often look like prompt bugs unless the pipeline is visible.

---

## Rollout Controls

Add feature flags in `chanakya/config.py`.

Recommended flags:

- [ ] `CHANAKYA_LONG_TERM_MEMORY_ENABLED`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_EXTRACTION_ENABLED`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_MCP_ENABLED`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_MAX_INJECTED_ITEMS`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_MAX_INJECTED_CHARS`

Initial rollout sequence:

1. ship store + manual retrieval with feature flag off by default,
2. enable retrieval in local development,
3. enable background extraction,
4. enable MCP memory tools,
5. tune prompt and scoring based on observed quality.

---

## Explicit Non-Goals for V1

- [ ] No external vector database
- [ ] No markdown-file primary memory system
- [ ] No full transcript summarization as memory replacement
- [ ] No multi-user auth redesign
- [ ] No distributed job queue
- [ ] No UI redesign for memory management unless needed for debugging

---

## Phase 2: Embeddings and Episodic Recall

Only do this after v1 is stable.

- [ ] Add optional embedding field/table for memory records
- [ ] Evaluate `sqlite-vec` for local-first deployment
- [ ] Alternatively evaluate Postgres + `pgvector` if moving toward production multi-user deployment
- [ ] Add semantic search fallback for vague recall queries
- [ ] Optionally add semantic retrieval over conversation chunks for episodic recall

Use embeddings for recall quality, not as the sole source of truth.

---

## Acceptance Criteria

- [ ] The system preserves durable user/project facts across long sessions
- [ ] The agent can recall relevant saved facts even when they are far outside recent history
- [ ] Memory updates happen automatically after conversation turns
- [ ] Contradicted memories are superseded and not retrieved as current truth
- [ ] Explicit forget requests remove memories from future retrieval
- [ ] Transcript history remains intact and separate from curated memory
- [ ] User-facing latency does not regress materially because extraction runs outside the main reply path
- [ ] The feature can be disabled cleanly with configuration flags

---

## Verification Commands

Run from repo root:

```bash
python -m ruff check chanakya/
python -m mypy chanakya/
pytest chanakya/test/test_memory_service.py -q
pytest chanakya/test/test_memory_extractor.py -q
pytest chanakya/test/test_mcp_memory_server.py -q
pytest chanakya/test/test_long_term_memory_integration.py -q
pytest chanakya/test
```

---

## Recommended First Build Slice

If you want the smallest valuable slice first, implement this order:

- [ ] Add memory tables and repository methods
- [ ] Add lexical retrieval and prompt injection
- [ ] Manually seed one or two memory records in tests
- [ ] Verify the agent answers from injected long-term memory
- [ ] Add background extractor after retrieval quality is confirmed
- [ ] Add MCP memory tools last

This gets memory usefulness on-screen before taking on autonomous mutation logic.
