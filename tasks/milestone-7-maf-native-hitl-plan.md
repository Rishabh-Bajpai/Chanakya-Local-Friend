# Milestone 7 Plan: MAF-Native Human-in-the-Loop for Manager-Delegated Workflows

## Scope

- Use Microsoft Agent Framework's native human-in-the-loop pause/resume pattern for Milestone 7.
- Limit the first implementation to manager-delegated workflows only.
- Do not extend native HITL to the direct Chanakya chat/tool path in this milestone.
- Keep Chanakya's domain layer as the product system of record for requests, tasks, and GUI state.

## Goal

Implement a delegated workflow that can:

1. Start normally from Chanakya.
2. Pause inside MAF when an agent needs clarification.
3. Surface the pending question in Chanakya's GUI.
4. Accept the user's response through the GUI.
5. Resume the exact MAF workflow run using MAF's native response path.
6. Sync the resulting lifecycle back into Chanakya task/request persistence.

## Why This Approach

The intended behavior is not just a Chanakya-managed "waiting for input" simulation. The workflow should actually pause in MAF, wait on a native input request, and resume through MAF after the user responds. Chanakya should mirror and expose that state, not invent an alternative pause mechanism.

## MAF Pattern To Use

For the workflow step that needs clarification:

```python
await ctx.request_info(
    request_data=ClarificationRequest(...),
    response_type=ClarificationResponse,
)
```

For resume handling:

```python
@response_handler
async def handle_clarification_response(
    self,
    original_request: ClarificationRequest,
    response: ClarificationResponse,
    ctx: WorkflowContext,
) -> None:
    ...
```

Chanakya's app layer should bridge GUI input to the pending MAF request by using the persisted workflow instance ID and request ID.

## Product Boundaries

### MAF owns

- Workflow execution.
- Native waiting-for-input pause points.
- Resume handlers.
- Exact workflow continuation after user input.

### Chanakya owns

- Request and task persistence.
- Task state visibility in the GUI.
- Mapping between Chanakya task IDs and MAF workflow identifiers.
- User input collection through HTTP + GUI.
- Operator controls such as cancel, retry, and manual unblock.

## First Supported Flow

Implement native HITL for one narrow delegated path first:

- Request is routed to the manager.
- Software-delivery workflow starts.
- Developer stage can request clarification.
- Workflow pauses in MAF.
- Root task and relevant child task move to `waiting_input` in Chanakya.
- User answers in GUI.
- Chanakya submits response back to MAF.
- Workflow resumes and completes or fails normally.

This should be the first vertical slice before expanding HITL to tester, researcher, writer, or specialist review stages.

## Required Architecture Changes

## 1. Add a dedicated MAF workflow runtime layer

Create a new module, likely one of:

- `chanakya/workflow_runtime.py`
- `chanakya/maf_workflows.py`

Responsibilities:

- Define manager-delegated workflows using MAF workflow primitives.
- Start workflow runs.
- Capture workflow instance IDs and pending request IDs.
- Resume pending requests with user responses.
- Provide a small adapter surface that `AgentManager` can call.

Recommended surface:

```python
class WorkflowRuntime:
    def start_manager_workflow(...): ...
    def submit_human_input(...): ...
    def cancel_workflow(...): ...
    def get_pending_input(...): ...
```

## 2. Refactor AgentManager from synchronous execution to workflow facade

Current state:

- `chanakya/agent_manager.py` runs the delegated workflow synchronously in Python methods.
- It directly transitions child tasks and returns a final `ManagerRunResult`.

Needed change:

- `AgentManager` becomes a facade over the MAF workflow runtime.
- Workflow stages move into MAF executors.
- State transitions still get mirrored into Chanakya's store.

Suggested split:

- `AgentManager.start_workflow(...)`
- `AgentManager.resume_waiting_input(...)`
- `AgentManager.cancel_workflow(...)`
- internal sync helpers for translating MAF waiting/completion/failure into task/request updates

## 3. Persist workflow identity mapping

Chanakya must store enough information to resume the exact MAF run later.

Persist per waiting task or root task:

- `maf_workflow_instance_id`
- `maf_pending_request_id`
- `maf_request_kind`
- `maf_waiting_payload`
- optional `maf_resume_handler` or stage metadata if useful for observability

Recommended first version:

- Store these fields in `TaskModel.input_json` or `TaskModel.result_json`.
- Avoid a new table unless querying becomes awkward.

Possible later extraction:

- `WorkflowRunModel`
- `PendingHumanInputModel`

Not required for the first Milestone 7 slice.

## 4. Mirror MAF waiting state into Chanakya task state

Use existing `TASK_STATUS_WAITING_INPUT` in `chanakya/domain.py`.

When MAF pauses for input:

- set the specific worker task to `waiting_input`
- set the root task to `waiting_input` if the overall request is paused
- keep the request non-terminal
- persist an assistant clarification message in chat history

Required events to add:

- `user_input_requested`
- `user_input_submitted`
- `task_resumed`
- `workflow_cancel_requested`
- `workflow_cancelled`
- `task_retry_requested`
- `task_manual_unblocked`

Downstream dependent tasks should remain `blocked` until the upstream workflow actually resumes and advances.

## 5. Add a resume API that forwards to MAF

Add endpoints in `chanakya/app.py`:

- `POST /api/tasks/<task_id>/input`
- `POST /api/tasks/<task_id>/cancel`
- `POST /api/tasks/<task_id>/retry`
- `POST /api/tasks/<task_id>/unblock`

Priority order:

1. `input`
2. `cancel`
3. `retry`
4. `unblock`

The `input` endpoint should:

1. Load the task.
2. Read persisted MAF workflow instance ID and pending request ID.
3. Forward the user's response into the MAF workflow runtime.
4. Persist `user_input_submitted` event.
5. Refresh task/request state from the resumed workflow result.
6. Return updated task/request metadata to the GUI.

Example payload:

```json
{
  "message": "Use PostgreSQL for production and SQLite only for local dev."
}
```

## 6. Update ChatService to support non-terminal delegated workflow starts

Current `chanakya/chat_service.py` behavior is too terminal-focused. It assumes a request resolves to either done or failed in one pass.

Needed behavior:

- Start a delegated workflow run.
- If the workflow pauses for MAF HITL, do not mark the request complete.
- Persist the assistant clarification message.
- Return a `ChatReply` that tells the GUI the request is waiting on user input.

Suggested `ChatReply` additions:

- `requires_input: bool`
- `waiting_task_id: str | None`
- `input_prompt: str | None`

Request/task behavior while paused:

- request remains open, preferably `in_progress`
- root task becomes `waiting_input`
- response is persisted as a clarification prompt, not a final completion

## 7. Add GUI support for pending input and resume

Update `chanakya/templates/index.html`:

- In `renderTasks(...)`, show waiting-input tasks clearly.
- Render a small reply form for tasks in `waiting_input`.
- Submit replies to `POST /api/tasks/<task_id>/input`.
- Refresh request trace, tasks, timeline, and chat panels after submit.

Also update:

- `formatEventTitle(...)`
- `summarizeEvent(...)`

So the timeline exposes native HITL lifecycle clearly.

Suggested visible states:

- waiting for input
- input submitted
- resumed
- cancelled
- retry requested

## 8. Add tests around the native HITL path

Primary test locations:

- `chanakya/test/test_agent_manager.py`
- `chanakya/test/test_domain_foundation.py`
- new Flask endpoint tests if missing

Required test cases:

1. Delegated workflow requests clarification through native MAF HITL.
2. Waiting task persists MAF workflow instance/request IDs.
3. Root task and child task move to `waiting_input`.
4. GUI/API reply path submits user input and resumes the same MAF run.
5. Workflow completes after resume.
6. Cancel works for a waiting workflow.
7. Retry behavior is clearly defined and tested.
8. Downstream blocked tasks remain blocked until upstream resume succeeds.

## Detailed Build Sequence

## Phase 1: Introduce workflow runtime scaffolding

Files:

- new workflow runtime module
- `chanakya/agent_manager.py`

Tasks:

- Add a workflow runtime wrapper around MAF workflow start/resume operations.
- Define request/response dataclasses for clarification requests.
- Create a manager-delegated workflow graph for the software path.

Exit criteria:

- A delegated workflow can be started through the new runtime.

## Phase 2: Implement one native clarification point

Files:

- workflow runtime module
- `chanakya/agent_manager.py`

Tasks:

- Add a clarification gate in the developer stage.
- Use `ctx.request_info(...)` to pause when required context is missing or ambiguous.
- Add a response handler that resumes processing after user input.

Exit criteria:

- A workflow can actually pause inside MAF and expose a pending request ID.

## Phase 3: Sync MAF waiting state into Chanakya persistence

Files:

- `chanakya/agent_manager.py`
- `chanakya/chat_service.py`
- `chanakya/store.py`

Tasks:

- Persist MAF workflow IDs on the relevant task/root task.
- Move task state to `waiting_input` when MAF pauses.
- Persist the clarification prompt into chat history and task events.

Exit criteria:

- GUI/API can show a real waiting task tied to a real MAF pending request.

## Phase 4: Add resume endpoint and GUI response flow

Files:

- `chanakya/app.py`
- `chanakya/templates/index.html`
- `chanakya/chat_service.py`

Tasks:

- Add `POST /api/tasks/<task_id>/input`.
- Forward the user response into MAF.
- Refresh the resumed task/request state.
- Render the pending-input form in the GUI.

Exit criteria:

- User can answer a clarification request from the GUI and the workflow resumes.

## Phase 5: Add cancel, retry, and manual unblock

Files:

- `chanakya/app.py`
- `chanakya/agent_manager.py`
- `chanakya/templates/index.html`

Tasks:

- Add cancel first.
- Define retry semantics carefully.
- Add manual unblock last because it is operator/domain behavior, not core MAF HITL.

Recommended semantics:

- Cancel: attempt to stop the waiting workflow run, then sync Chanakya state.
- Retry: create a new workflow run or rerun a failed stage explicitly; do not treat retry as resume.
- Manual unblock: override a domain `blocked` task only when the operator decides to bypass a dependency issue.

Exit criteria:

- Operator controls exist and are visible in the GUI.

## Data Model Guidance

### Keep for Milestone 7

- `RequestModel`
- `TaskModel`
- `TaskEventModel`

### Add only if necessary

- `REQUEST_STATUS_CANCELLED`
- `TASK_STATUS_CANCELLED`

Recommendation:

- Add cancelled statuses now if cancel is in scope for the milestone.
- Otherwise cancel will be forced into either failed or an event-only state, which is harder to reason about.

### Do not add yet unless forced

- dedicated workflow run table
- dedicated pending human input table

Use JSON persistence first.

## Risks

## 1. Current manager path is synchronous

The current `AgentManager.execute()` model is not yet structured around durable workflow instances. Native HITL requires a more explicit workflow runtime model.

## 2. Resume must target the exact MAF request

If workflow instance ID or request ID handling is sloppy, Chanakya could resume the wrong run or fail to resume at all.

## 3. Retry is easy to mis-specify

Retry is not resume. Resume answers a pending request. Retry should restart a failed/waiting execution path deliberately.

## 4. GUI and domain state can drift from MAF state

Chanakya should not infer waiting state. It should only mirror what MAF reports and persist that faithfully.

## 5. Expanding too broadly too early will slow delivery

Keep the first implementation to manager-delegated software workflow with developer clarification only.

## Validation Plan

## Core validation

1. Submit a delegated software task that lacks a required clarification.
2. Confirm the workflow pauses natively in MAF.
3. Confirm the root task and developer task show `waiting_input` in Chanakya.
4. Confirm the GUI shows the clarification prompt.
5. Reply through the GUI.
6. Confirm Chanakya forwards the input to the pending MAF request.
7. Confirm the same workflow run resumes and completes.
8. Confirm the timeline shows request, waiting, input submission, resume, and completion.

## Secondary validation

1. Cancel a waiting workflow.
2. Confirm task/request state is synced correctly.
3. Retry a failed workflow and confirm the semantics are clear and test-backed.

## Out Of Scope For This Milestone Slice

- Native HITL for direct non-manager chat.
- Native HITL for every worker role on day one.
- Full durable workflow visualization beyond current task/timeline GUI.
- Replacing Chanakya's task system with MAF runtime state.

## Summary

Milestone 7 should be implemented by moving the manager-delegated workflow path onto MAF's native human-in-the-loop pattern, starting with one narrow clarification flow. MAF should own the actual pause/resume execution, while Chanakya should persist, visualize, and bridge user responses back into the waiting workflow. This preserves the PRD's task-centric architecture while still delivering true MAF-native HITL behavior.
