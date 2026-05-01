# Implementation Plan: MAF HistoryProvider Integration

**Status:** Corrected and Partially Implemented
**Created:** 2026-03-27
**Last Updated:** 2026-03-27

---

## Purpose

Integrate a MAF history provider so conversation memory is loaded from and stored to the existing SQLAlchemy-backed `chat_messages` table instead of relying on manual prompt stitching.

This plan has been corrected to match the installed MAF API in this repo.

---

## Verified API Notes

These points were verified against the installed `agent_framework` package:

1. Use `BaseHistoryProvider` from `agent_framework`
2. Use `Message` from `agent_framework`
3. `BaseHistoryProvider.__init__` supports:
   - `load_messages`
   - `store_inputs`
   - `store_outputs`
4. `BaseHistoryProvider` requires implementing:
   - `get_messages(...)`
   - `save_messages(...)`
5. `Message` uses `text=` / `contents=` and `additional_properties=`
6. `replace_messages()` is not part of the installed `BaseHistoryProvider` API
7. `CompactingHistoryMixin` is not available in the installed package, so compaction work is out of scope for now

---

## Problems in the Old Approach

1. Manual `_build_prompt()` duplicated what a history provider should do
2. In-memory session reuse in `MAFRuntime` was not restart-safe
3. Chat history lived in SQLAlchemy already, but MAF was not using it as the source of memory
4. The plan incorrectly proposed API names not present in the installed package

---

## Approved Implementation Scope

Only the following changes are correct and approved:

### 1. Add a SQLAlchemy-backed history provider

- New file: `chanakya/history_provider.py`
- Class name: `SQLAlchemyHistoryProvider`
- Backing table: `ChatMessageModel`
- Responsibilities:
  - `get_messages()` loads session history from the database
  - `save_messages()` appends new MAF messages to the database

### 2. Update `MAFRuntime`

- Accept `session_factory` in the constructor
- Register `SQLAlchemyHistoryProvider` through `context_providers`
- Remove the in-memory session dictionary
- Create a session per call using `agent.create_session(session_id=...)`
- Keep debug logging for runtime visibility

### 3. Simplify `ChatService`

- Remove `_build_prompt()`
- Stop manually writing user/assistant messages through `store.add_message()`
- Keep event logging (`route_decision`, `chat_response`)
- Keep reading history through `store.list_messages()` for GUI/debug visibility

### 4. Keep `ChatMessageModel`

- Do not drop the table
- Do not remove `list_messages()` because the GUI depends on it
- The history provider should make this table the single persisted chat-history source of truth

---

## Explicitly Rejected Items

The following items from the earlier draft should not be implemented now:

1. `replace_messages()` on the provider
2. `store_responses` flag name
3. `Message(content=...)`
4. SQLite-specific naming like `SQLiteHistoryProvider`
5. Removing `ChatMessageModel` usage after migration
6. Dropping the `chat_messages` table
7. Compaction based on `CompactingHistoryMixin`

---

## Implementation Sketch

### `chanakya/history_provider.py`

- Implement `SQLAlchemyHistoryProvider(BaseHistoryProvider)`
- Convert DB rows into `Message(role=..., text=..., additional_properties=...)`
- Persist new messages into `ChatMessageModel`
- Use SQLAlchemy sessions via the app's shared session factory

### `chanakya/maf_runtime.py`

- Construct the provider with:
  - `load_messages=True`
  - `store_inputs=True`
  - `store_outputs=True`
- Pass the provider into `Agent(..., context_providers=[...])`
- Create a new `AgentSession` per call using the same `session_id`

### `chanakya/chat_service.py`

- Keep request/event logging
- Pass the raw user message to MAF
- Let MAF history loading handle conversation continuity

---

## Notes on Metadata

The history provider may attach lightweight metadata such as `request_id` and `route` using `session.state` for the current invocation. This is acceptable because it preserves useful observability in `chat_messages` without manual prompt stitching.

---

## Testing Checklist

- [ ] First message persists to `chat_messages`
- [ ] Follow-up message uses prior conversation context
- [ ] Chat history remains visible through `/api/sessions/<session_id>`
- [ ] Context still works after a server restart when the same `session_id` is reused
- [ ] `ruff` passes
- [ ] `mypy` passes

---

## Decision

Use a SQLAlchemy-backed MAF history provider now. Defer compaction and full serialized session persistence until later.
