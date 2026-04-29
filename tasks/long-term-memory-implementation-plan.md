# Implementation Plan: Long-Term Memory for Classic Chat

**Status:** Partially Implemented
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

Current decision:

- do not add a deterministic pre-gate for skipping the memory manager yet,
- keep the background memory-manager call asynchronous and post-response,
- rely on prompt-driven `noop` behavior for transient turns unless cost or noise becomes a real issue later.

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

## Implementation Status

The core architecture described above is now implemented.

### Completed foundation work

- [x] Add `MemoryRecordModel` to `chanakya/model.py`
- [x] Add `MemoryEventModel` to `chanakya/model.py`
- [x] Add `MemoryRepository` to `chanakya/store.py`
- [x] Expose memory repository operations through `ChanakyaStore`
- [x] Add memory event logging helpers
- [x] Add basic active-memory retrieval and soft-delete behavior

### Completed memory-manager architecture work

- [x] Replace heuristic write decisions with a dedicated memory manager MAF agent
- [x] Add `chanakya/services/memory_manager_service.py`
- [x] Use a strict JSON operation contract for memory-manager output
- [x] Validate and apply proposed operations in app code rather than letting the agent write directly
- [x] Support multiple operations in a single memory request
- [x] Add failure fields such as `retryable`, `error_code`, and `error_detail`

### Completed background update integration

- [x] Trigger background memory updates after response persistence in `chat_service.py`
- [x] Keep memory updates outside the main reply critical path
- [x] Capture request/session context for background memory updates
- [x] Log `memory_extraction_failed` and `memory_extraction_skipped` events without breaking replies

### Completed retrieval and prompt work

- [x] Add retrieval by `owner_id`, `session_id`, and current query
- [x] Inject compact long-term memory into runtime prompt addendum
- [x] Preserve existing transcript history behavior in `SQLAlchemyHistoryProvider`
- [x] Add observability for retrieved memory ids and counts
- [x] Update classic-chat prompt guidance so Chanakya knows it is an agent with long-term memory
- [x] Instruct Chanakya not to manage memory directly and to rely on the memory-agent tool

### Completed MCP memory-agent tool work

- [x] Add `chanakya/services/mcp_memory_agent_server.py`
- [x] Implement the single `memory_agent_request(memory_request: str)` tool
- [x] Make the tool invoke the dedicated memory manager service
- [x] Add memory-agent server startup entrypoint consistent with other MCP servers
- [x] Register the memory-agent server in local MCP configuration
- [x] Ensure the core assistant profile can receive only the single memory-agent tool when configured

### Completed debug visibility work

- [x] Add `/api/memory`
- [x] Add `/api/memory/events`
- [x] Add `/api/sessions/<session_id>/memory`
- [x] Add `+ -> Memory` debug UI in classic chat
- [x] Show stored memories and memory events for the active session

### Completed verification work

- [x] Add focused tests for long-term memory behavior
- [x] Add focused tests for memory debug APIs
- [x] Add prompt-regression checks for classic/work runtime addenda
- [x] Run focused `pytest` for changed areas during implementation

---

## Remaining Work

The remaining items are mostly quality, reliability, and future-scale improvements rather than core architecture work.

## Priority 1: Reliability and Product Confidence

- [ ] Add stronger end-to-end tests for explicit memory recall through Chanakya replies, not just service-layer behavior
- [ ] Add integration tests covering contradiction and supersede flows in realistic multi-turn sessions
- [ ] Add integration tests covering explicit forget/delete flows with ambiguous references like `remove it`
- [ ] Add integration tests covering memory-agent MCP behavior from the assistant side
- [ ] Add tests covering failure propagation so Chanakya accurately reflects memory-agent failures and retryability

## Priority 2: Retrieval Quality

- [ ] Improve ranking of memories for prompt injection beyond simple lexical overlap
- [ ] Add stronger weighting for identity, preference, and instruction memories
- [ ] Add better suppression for low-value or overly verbose memory bodies
- [ ] Add tests verifying relevant memory is injected and irrelevant memory is excluded
- [ ] Optionally surface the latest retrieved memories more explicitly in the debug UI

## Priority 3: Memory Hygiene and Maintenance

- [ ] Implement dedupe logic for near-identical active memories
- [ ] Add explicit supersede flows where newer memories should replace older active ones cleanly
- [ ] Add optional memory expiry handling for facts that should age out
- [ ] Add a periodic maintenance job later for retrying failed memory updates, dedupe, and cleanup

## Priority 4: Tooling and Debug Depth

- [ ] Add dedicated MCP server tests similar to other MCP server coverage
- [ ] Add richer event payloads for operations proposed vs operations applied
- [ ] Add clearer debug visibility for source messages used during a memory update decision
- [ ] Add richer filtering/search in the debug UI if manual inspection becomes cumbersome

## Priority 5: Future-Scale Enhancements

- [ ] Add optional embedding field/table for memory records
- [ ] Evaluate `sqlite-vec` for local-first deployment
- [ ] Evaluate Postgres + `pgvector` if moving toward multi-user or production deployment
- [ ] Add semantic search fallback for vague recall queries
- [ ] Optionally add episodic recall over old transcript chunks

---

## Current File Map

Implemented or added during this feature:

- [x] `chanakya/services/memory_manager_service.py`
- [x] `chanakya/services/mcp_memory_agent_server.py`
- [x] `chanakya/services/long_term_memory.py`
- [x] `chanakya/test/test_long_term_memory.py`
- [x] `chanakya/test/test_memory_api.py`

Still useful to add later:

- [ ] `chanakya/test/test_mcp_memory_agent_server.py`
- [ ] `chanakya/test/test_long_term_memory_integration.py`

---

## Primary Files Touched

- [x] `chanakya/model.py`
- [x] `chanakya/store.py`
- [x] `chanakya/chat_service.py`
- [x] `chanakya/app.py`
- [x] agent profile/default tool wiring via `sync_default_agent_tools()`

Potential future touchpoints:

- [ ] `chanakya/agent/runtime.py` if memory-specific runtime hooks become necessary
- [ ] `chanakya/services/tool_loader.py` if tool lifecycle for memory-agent MCP needs refinement

---

## Suggested Next Milestones

### Milestone A: confidence and regression coverage

- expand end-to-end tests
- lock down forget/update/recall behavior
- verify failure and retry reporting stays honest

### Milestone B: retrieval quality

- improve ranking and filtering
- validate memory injection quality over longer sessions

### Milestone C: maintenance and cleanup

- dedupe
- supersede hygiene
- periodic retry/cleanup jobs

### Milestone D: optional semantic search

- embeddings only if lexical retrieval is no longer enough

---

## Prompting Guidance for Memory Manager

The memory-manager prompt should explicitly say:

1. store only durable facts likely to matter later,
2. do not summarize the entire conversation,
3. prefer updating or superseding over duplicating,
4. ignore temporary or low-value details,
5. output JSON only,
6. never invent facts not present in the source turns,
7. mark low-confidence candidates conservatively.
8. multiple operations in one request are allowed when necessary.
9. return precise failure and retryability information when processing fails.

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

Current useful flags in `chanakya/config.py`:

- [x] `CHANAKYA_LONG_TERM_MEMORY_ENABLED`
- [x] `CHANAKYA_LONG_TERM_MEMORY_MAX_INJECTED_ITEMS`
- [x] `CHANAKYA_LONG_TERM_MEMORY_MAX_INJECTED_CHARS`
- [x] `CHANAKYA_LONG_TERM_MEMORY_OWNER_ID`

Potential future flags if needed:

- [ ] `CHANAKYA_LONG_TERM_MEMORY_EXTRACTION_ENABLED`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_MCP_ENABLED`
- [ ] `CHANAKYA_LONG_TERM_MEMORY_DEBUG_UI_ENABLED`

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

- [x] The system preserves durable user/project facts across long sessions
- [x] The agent can recall relevant saved facts even when they are far outside recent history
- [x] Memory updates happen automatically after conversation turns
- [x] Explicit forget requests remove memories from future retrieval when the memory-agent tool succeeds
- [x] Transcript history remains intact and separate from curated memory
- [x] User-facing latency does not regress materially because extraction runs outside the main reply path
- [x] The feature can be disabled cleanly with configuration flags

Still to strengthen further:

- [ ] Contradicted memories are always superseded cleanly in realistic multi-turn cases
- [ ] Retrieval quality remains strong as memory volume grows
- [ ] Failure and retry behavior remain consistently honest in all memory-agent edge cases

---

## Verification Commands

Current focused checks:

```bash
pytest chanakya/test/test_long_term_memory.py -q
pytest chanakya/test/test_memory_api.py -q
pytest chanakya/test/test_domain_foundation.py -q
pytest chanakya/test/test_agent_manager.py::test_normal_chat_uses_classic_runtime_prompt_addendum_for_direct_runs -q
pytest chanakya/test/test_agent_manager.py::test_work_mode_uses_work_runtime_prompt_addendum_for_direct_runs -q
```

Broader checks when doing follow-up work:

```bash
python -m ruff check chanakya/
python -m mypy chanakya/
pytest chanakya/test
```

---

## Recommended Next Slice

If work resumes on this feature, the best next slice is:

- [ ] add realistic end-to-end tests for recall, delete, ambiguity, and retry paths
- [ ] improve retrieval ranking and filtering quality
- [ ] add richer memory event payloads for debugging proposed vs applied operations

That will improve product confidence more than adding embeddings or new infrastructure right now.
