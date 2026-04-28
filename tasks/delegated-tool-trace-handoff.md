# Delegated Tool Trace Handoff

## Scope

This handoff covers a misleading delegated-work bug where a participant agent successfully uses a tool and writes a real file, but top-level accounting and `/work` history display incorrectly show zero tool usage.

The user noted that other related fixes may already be in progress. Treat this note as current guidance after re-reading the latest workspace state, but verify the exact current behavior before editing because some adjacent issues may already be partially fixed.

This note was prepared after re-reading the current workspace state, especially:

- `chanakya/chat_service.py`
- `chanakya/app.py`
- `chanakya/agent_manager.py`
- `chanakya/templates/work.html`

## Confirmed Reproduction

Observed request pair in the current DB:

- original request: `req_89394c6053`
- follow-up request: `req_5992b26c1d`
- work: `work_9cca3d62bf`
- saved file: `chanakya_data/shared_workspace/work_9cca3d62bf/climate_change_2025_report.md`

Confirmed facts:

- The file exists and contains the report.
- Runtime log shows a real tool call:
  - `build/runtime/chanakya.log`
  - `mcp_filesystem_write_text_file`
  - success at the time the follow-up request ran.
- DB/task metadata still claims no tool usage:
  - `tool_invocations` has no rows for the request
  - `response_persisted` event shows `tool_calls_used: 0`

## User-Visible Bug

There are two linked symptoms.

### 1. Top-level accounting is wrong

Even when a delegated participant uses a tool successfully:

- `tool_invocations` is empty
- response/task metadata says `tool_calls_used = 0`

### 2. `/work` Agent Histories can also be wrong

The group-chat inspector in `/work` renders tool usage from `execution_trace.tool_calls` and per-step `tool_traces`.

If the delegated runtime trace is missing or reconstructed without tool traces, the UI shows zero tool calls even though a delegated participant actually used one.

## Current Code State

### Direct path persists tool traces correctly

In `chanakya/chat_service.py:1632-1668`, direct runtime results persist `run_result.tool_traces` into `tool_invocations` and create `tool_trace_recorded` task events.

In `chanakya/chat_service.py:1684-1696`, direct responses derive `tool_calls_used` from `len(direct_run_result.tool_traces)`.

### Delegated path drops top-level accounting

In `chanakya/chat_service.py:1670-1683`, delegated manager results set:

- `route = "delegated_manager"`
- `direct_tool_calls_used = 0`

That zero is later reused in response metadata and persisted events.

One example in the waiting-input path is `chanakya/chat_service.py:1733-1737`, where visible message metadata hard-codes `tool_calls_used: 0`.

### Delegated runtime trace already captures participant tool usage

`chanakya/agent_manager.py` already records traced participant tool calls.

- `chanakya/agent_manager.py:730-765`
  - builds `execution_trace`
  - stores it in manager `result_json`
- `chanakya/agent_manager.py:1717-1896`
  - `build_group_chat_execution_trace(...)`
  - aggregates `tool_calls`
  - includes per-step `tool_traces`

So the main issue is not tool execution. The issue is persistence and fallback accounting in the top-level chat layer.

### `/work` history display depends on `execution_trace`

The UI in `chanakya/templates/work.html:3635-3873` renders:

- overall tool count from `trace.tool_calls`
- per-turn tool activity from `step.tool_traces`

The API in `chanakya/app.py:1147-1204`:

- prefers `manager_result["execution_trace"]`
- reconstructs a fallback trace if it is missing

That fallback reconstruction does not inject runtime tool traces, so older or incomplete runs can still display zero tool calls.

For the reproduced climate-report case, the persisted manager task result currently does not appear to expose a usable `execution_trace` through the history path, which is why `/work` falls back to reconstructed trace data and loses the tool-call detail.

## Recommended Fix Strategy

Go with the recommended approach discussed with the user:

1. prefer persisted delegated `execution_trace`
2. only enrich from `tool_invocations` when trace is missing or incomplete

This keeps runtime trace as the source of truth and uses DB rows only as repair data.

## Implementation Plan

### 1. Fix delegated tool accounting in `chat_service.py`

Add a helper that persists tool traces for both code paths:

- direct path: `run_result.tool_traces`
- delegated path: normalized traces extracted from `manager_result.result_json["execution_trace"]["tool_calls"]`

Suggested shape for the delegated normalized records:

- `agent_id`
- `agent_name`
- `agent_role`
- `tool_id`
- `tool_name`
- `server_name`
- `status`
- `input_payload`
- `output_text`
- `error_text`

Persist those with:

- `store.create_tool_invocation(...)`
- `store.finish_tool_invocation(...)`
- `store.create_task_event(... event_type="tool_trace_recorded")`

Important: delegated records should use the participant agent identity, not `self.runtime.profile`.

### 2. Replace delegated `tool_calls_used = 0`

In `chat_service.py`, stop hard-coding delegated zero counts.

Compute delegated tool-call count from normalized delegated traces and use that everywhere top-level metadata is assembled, including:

- persisted response metadata
- visible group-chat message metadata
- result payloads
- `response_persisted` task event payloads

### 3. Preserve `/work` history by keeping delegated `execution_trace` authoritative

The preferred source for `/work` is still manager `result_json["execution_trace"]`.

Before adding any fallback logic, verify that delegated manager task results consistently retain:

- `execution_trace.tool_calls`
- per-step `call_sequence[].tool_traces`

If anything in the current flow is dropping that field before persistence, fix that boundary first.

Do not switch `/work` to primarily trust `tool_invocations`. Keep `execution_trace` as the first-class source of truth and use `tool_invocations` only as repair data for missing or incomplete historical runs.

### 4. Add fallback enrichment in `app.py` for older/incomplete runs

In `/api/works/<work_id>/history` (`chanakya/app.py:1147-1204`):

- when `manager_result["execution_trace"]` is missing, malformed, or reconstructed without tool traces
- enrich the reconstructed run with tool-call info derived from `tool_invocations`

Recommended fallback behavior:

- prefer persisted `execution_trace`
- only fall back to `tool_invocations` if `execution_trace.tool_calls` is absent or empty

This fallback is for repair and backward compatibility, not the primary source of truth.

## Acceptance Criteria

After the fix, the following should all be true for delegated runs like the climate-report example:

1. The delegated participant still saves the file successfully.
2. `tool_invocations` contains the filesystem tool call for the request.
3. The persisted agent on the invocation is the real participant, such as Writer, not Chanakya.
4. Top-level metadata no longer says `tool_calls_used: 0` when delegated tools were used.
5. `/work` group-chat inspector shows non-zero tool calls.
6. `/work` per-step timeline shows the participant turn with its `tool_traces`.
7. Direct non-delegated tool persistence remains unchanged.

## Tests To Add

### Delegated accounting tests

1. Delegated initial request with one participant tool call
   - `tool_invocations` row created
   - `tool_calls_used > 0`
   - participant identity persisted correctly

2. Delegated follow-up request with one participant tool call
   - same assertions as above
   - specifically cover the follow-up style seen in `req_5992b26c1d`

3. Multiple delegated tool calls in one turn
   - aggregated count is correct

4. Multiple participants each using tools
   - all traces are persisted

5. Failed delegated tool call
   - persisted with `status="failed"`
   - `error_text` preserved

### `/work` history tests

6. History API returns delegated `execution_trace.tool_calls`
   - non-zero count in `group_chat_inspector.runs`

7. History API returns per-step `call_sequence[].tool_traces`
   - participant step contains the tool trace

8. Reconstructed-history fallback test
   - missing persisted `execution_trace`
   - API enriches from `tool_invocations`

9. Direct path regression test
   - existing direct tool-trace persistence remains unchanged

## Suggested File Touchpoints

Primary:

- `chanakya/chat_service.py`
- `chanakya/app.py`

Only if necessary:

- `chanakya/agent_manager.py`

Read-only consumer to verify after backend changes:

- `chanakya/templates/work.html`

## Non-Goals

- Do not change how the delegated agents decide whether to use a tool.
- Do not change the report-writing behavior itself.
- Do not make `tool_invocations` the primary source for `/work` when a valid runtime `execution_trace` already exists.

## Practical Debugging Tip

When validating the fix locally, re-check the same family of artifacts together:

- `build/runtime/chanakya.log`
- `tool_invocations` rows for the request
- `task_events.response_persisted`
- manager task `result_json.execution_trace`
- `/api/works/<work_id>/history`

All five should agree after the fix.

## Developer Note

Start by re-reading the current versions of:

- `chanakya/chat_service.py`
- `chanakya/app.py`
- `chanakya/agent_manager.py`
- `chanakya/templates/work.html`

Do that before coding. The user indicated some related fixes may already exist in the current worktree, so the safest implementation is the smallest one that restores consistency across runtime logs, `tool_invocations`, top-level metadata, and `/work` history output.
