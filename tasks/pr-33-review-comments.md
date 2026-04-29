# PR #33 Review Comments

Source PR: `https://github.com/Rishabh-Bajpai/MAF-demo/pull/33`

## Findings

### 1. Work deletion leaks work-scoped artifact files

Status: valid

Relevant files:

- `chanakya/services/mcp_artifact_tools_server.py`
- `chanakya/app.py`
- `chanakya/store.py`

Why this is a real issue:

- Artifacts are stored under the global artifact root via `get_artifact_storage_root()`.
- The PR model and APIs support work-scoped artifacts through `ArtifactModel.work_id`, `list_artifacts_for_work()`, and `/api/works/<work_id>/artifacts`.
- `DELETE /api/works/<work_id>` deletes artifact database rows and the work sandbox, but it does not remove the corresponding artifact files from the global artifact storage area.

Impact:

- orphaned files on disk
- storage leak over time
- retained work output after logical deletion

Note:

Even if classic chat is currently the main producer of artifacts in practice, the PR code clearly supports work-scoped artifacts, so this is still a correctness bug.

### 2. `locate_artifact` skips artifact-root containment validation

Status: valid

Relevant file:

- `chanakya/services/mcp_artifact_tools_server.py`

Why this is a real issue:

- `read_artifact_text()` validates that the resolved file path stays under the artifact root.
- The download API path also validates containment through `_resolve_artifact_file()`.
- `locate_artifact()` resolves and returns `absolute_path` without performing the same containment check.

Impact:

- inconsistent security behavior across artifact access paths
- possible disclosure of host filesystem paths from malformed or unsafe stored artifact paths

Severity note:

This looks more like path disclosure / missing validation than direct arbitrary file read, but it is still worth fixing.

### 3. AIR proxy trace summaries leak on exception paths

Status: valid

Relevant file:

- `AI-Router-AIR/server/core/proxy_engine.py`

Why this is a real issue:

- `_record_trace_request()` inserts request state into `_trace_summaries`.
- Normal cleanup only happens through `_log_response_snapshot()` and `_finalize_stream_trace()`.
- The broad exception handler logs and re-raises as `ProxyError`, but does not remove the trace entry when failures happen after trace registration.

Impact:

- in-memory trace state can accumulate under repeated failures
- completed summaries for affected traces may never be emitted cleanly

Severity note:

This is primarily a memory/lifecycle cleanup bug on failure paths.

## Clarifications From Follow-up Review

### Artifact scope

The code in this PR does not treat artifacts as classic-only.

Supporting evidence:

- `chanakya/model.py` includes nullable `ArtifactModel.work_id`
- `chanakya/app.py` exposes `GET /api/works/<work_id>/artifacts`
- `chanakya/store.py` includes `list_artifacts_for_work()` and work-related artifact deletion logic
- PR planning docs explicitly distinguish classic direct artifacts from work-scoped artifacts

Conclusion:

The artifact-leak comment should remain unless work-scoped artifact support is removed from the PR.

## Suggested Wording For Review Submission

1. `chanakya/app.py`, `chanakya/store.py`, `chanakya/services/mcp_artifact_tools_server.py`
Work deletion removes work-scoped artifact rows but not the artifact files themselves. Because artifact content is stored under the global artifact root rather than the work sandbox, deleting `/api/works/<work_id>` currently leaves orphaned artifact files on disk for any work-scoped artifacts. This causes storage leakage and retains deleted work output unexpectedly.

2. `chanakya/services/mcp_artifact_tools_server.py`
`locate_artifact()` resolves and returns an absolute host path without the artifact-root containment check already used by `read_artifact_text()` and the download API. That makes this tool less strict than the other artifact access paths and can disclose arbitrary host paths if a stored artifact path is malformed or unsafe.

3. `AI-Router-AIR/server/core/proxy_engine.py`
Trace state is registered in `_trace_summaries` before forwarding, but cleanup only occurs on the normal response/finalization paths. If forwarding fails after trace registration and execution reaches the broad exception handler, the trace entry is never removed. Repeated upstream failures will accumulate stale trace state in memory.
