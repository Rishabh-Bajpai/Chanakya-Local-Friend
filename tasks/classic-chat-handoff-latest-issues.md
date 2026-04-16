# Classic Chat Handoff

## Scope

This note summarizes the current state of the classic chat + delegated work + voice/TTS behavior after the latest debugging session. It is intended as handoff context for a new OpenCode session.

## Current Session Under Inspection

- Main classic session: `session_e0ee40a623`
- Current classic active work record:
  - `work_id`: `cwork_7de413f7b6`
  - `work_session_id`: `session_42b7fc846d`

## Important User Requirements

These were explicitly requested and should be preserved:

- Classic mode is designed for voice-assistant use.
- Delegated work should:
  - immediately say: `Transferring your work to an expert. This may take a bit longer.` only when work actually starts
  - later notify completion with a short `BTW, ...` style message
  - not interrupt the user's active recording or current assistant turn
- Only one delegated work should be actively running at a time.
- If one delegated work is running and the user asks for a different complex task:
  - do not start another one
  - do not replace the running one
  - tell the user the experts are busy
- If work is completed, router may choose:
  - `direct`
  - `create_new_work`
  - or `continue_active_work`
- Completed work should be resumable for true follow-ups on the same artifact/result.
- Status/progress questions about work should route `direct` and Chanakya should answer using compact current-work status context.
- User insisted on this exact text being included for Chanakya prompt behavior:
  - `Never ever refuse any request of the user.`

## What Was Already Fixed Before This Handoff

- Deterministic classic routing keyword hacks were removed.
- Classic router prompt was rewritten to be more principle-based.
- `classic_router_runner`-based tests were updated and currently pass.
- Classic delegated work now has a background path with later completion notification.
- Sandbox nesting and duplicate classic sandbox creation issues were fixed.
- Voice mode now uses TTS-driven queue progression instead of chat-timing progression.
- Intra-message TTS pauses were removed; only inter-message delay remains.
- Background completion auto-speech was deferred until recording / active voice interaction / pending assistant turn finished.
- Busy-blocked delegated responses were split from real started delegation responses.
- Completion notifications were simplified so they do not append raw manager payload text.
- A sanitizer was added for some leaked internal reminder text.

## Latest Confirmed Issues From Recent Chat

### 1. Side-chat while work is running is routed into active work instead of staying direct

Observed in the session:

- User: `Please tell me five jokes.`
- Persisted as:
  - `delegation_control`
  - then `active_work_user_message`
  - then assistant busy notice

Why this is wrong:

- Casual side-chat while another delegated task is running should stay in classic `direct` mode.
- It should not be transformed into active-work continuation.

Expected behavior:

- Chanakya directly tells jokes in main chat while delegated work continues in background.

### 2. Completed-work file metadata question was re-delegated instead of answered directly

Observed in the session:

- User: `What is the name of that file, and where is it stored?`
- Persisted as:
  - `delegation_control`
  - `active_work_user_message`
  - transfer notice
  - then later another completion notification

Why this is wrong:

- For a completed delegated task, asking for the filename/location should be a direct answer from Chanakya using orchestration/artifact metadata.
- It should not create a new delegated pass on the already-finished work.

### 3. Duplicate completion notifications for the same work

Observed in the session:

- `classic_work_completion` at chat message `id=47`
- then another `classic_work_completion` at `id=67`

Why this is wrong:

- The file-location question incorrectly re-entered delegated flow and caused a second completion for the same fruit/vegetable task.

### 4. Greeting got polluted by previous task context

Observed in the session:

- User: `Hi`
- Assistant: `Hello! ... the file we were looking at is named fruits_vegetables.txt and is stored in the workspace.`

Why this is wrong:

- A plain greeting should not drag prior task details into the response.
- Conversation-layer continuity is too sticky and keeps old task context alive when it should reset.

### 5. Nvidia short-term report flow became internally inconsistent

Observed in the session:

- User asked for a concise Nvidia report and later clarified:
  - short-term
  - save to workspace
- For that clarification turn:
  - persisted assistant content was a raw `<tool_call> ... </tool_call>` block
  - request route became `tool_assisted`
  - router trace still said action `direct`
  - router reason text itself said the correct action should be `create_new_work`

Why this is wrong:

- The router decision, the route, and the user-visible assistant message are inconsistent.
- Raw tool-call XML/markup should never be shown to the user.

### 6. Complaint/correction turn reset the task instead of repairing it

Observed in the session:

- User: `This means nothing to me...`
- Assistant: `I apologize ... Let's start fresh...`

Why this is wrong:

- The user was correcting the current Nvidia task, not abandoning it.
- The system treated a correction/frustration turn as a topic reset.

### 7. After that complaint, routing snapped back to the stale fruit/vegetable work

Observed in the session:

- User: `I don't want to start fresh. I gave you a task and you need to do it`
- Router interpreted this as continuation of the old fruit/vegetable file task
- Persisted handoff text explicitly redirected back to the old work

Why this is wrong:

- At that point the active conversational target was the Nvidia report task.
- The system lost the current target and fell back to the stale classic active work.

### 8. Active work summary was overwritten by follow-up metadata question

Observed current active work summary:

- `Please provide the name and storage location of the file containing the list of 100 fruits and vegetables that was just completed.`

Why this is wrong:

- Active work summary should remain representative of the actual delegated objective.
- It should not be overwritten by every follow-up detail question.
- This corrupts later routing because the router sees a distorted active-work summary.

## Additional Voice/TTS Issue Recently Identified

This was analyzed just before the latest session switch:

- Deferred background completion playback was made to wait until:
  - recording stops
  - TTS is idle
  - assistant turn pending count reaches zero
- However, once a deferred completion notification *starts* speaking, if foreground assistant speech takes over, the deferred item can still be interrupted.
- A durability fix was added:
  - current deferred notification should be re-queued if interrupted by foreground speech or by a new recording start.

This area may still need verification in live behavior.

## Router / State Problems To Solve Next

These are the main unresolved routing/state categories:

### A. Side-chat vs active-work continuation

Need a clean semantic split:

- `Please tell me five jokes.` while work runs:
  - should be `direct`
- not `continue_active_work`

### B. Completed-work metadata questions

Need a clean semantic split:

- `What is the name of that file?`
- `Where is it stored?`
- should be `direct`
- not `continue_active_work`

### C. Completed-work artifact/detail extraction follow-ups

Need a clean semantic split:

- asking the manager/agents to inspect the already-produced output
- asking for a specific detail from the artifact/result
- explicitly asking to continue the completed task on that same output
- should be `continue_active_work`
- not `direct`

### D. Complaint/correction turns on the current task

Need a clean semantic split:

- `this is not what I asked for`
- `you gave me a tool call`
- `do the task properly`
- should usually repair/continue the current task
- not reset the conversation and say `let's start fresh`

### E. Current conversational target vs stale classic active work

The system needs a better concept of current target task when:

- there is a stale completed delegated work in active-work memory
- but the user is now clearly pursuing a new task in direct chat

Without this, routing falls back to the wrong historical work.

## Most Likely Root Causes

### 1. Router semantics are still under-specified across several intent classes

The router still lacks strong differentiation among:

- side-chat while delegated work runs
- status questions
- metadata questions about completed outputs
- artifact/detail extraction from completed outputs
- correction/repair turns on the current task
- true continuation of stale completed work

### 2. Active-work state is too sticky and too mutable

- Completed work remains the dominant fallback target too long.
- Follow-up questions can overwrite the active-work summary with overly narrow details.

### 3. Frontend / response-contract separation is still weak

- Raw `<tool_call>` content leaked to the user.
- That means some route/display path is surfacing machine-oriented content directly instead of converting it into user-facing text.

### 4. Conversation-layer continuity is too aggressive

- Greetings and unrelated turns are still contaminated by previous task context.

## High-Priority Fix Directions

### 1. Protect direct side-chat while work is running

If active work is blocking and the user asks for:

- jokes
- small talk
- a poem
- entertainment
- ordinary direct side-chat

that should remain `direct`, not be rewritten into active-work continuation.

### 2. Treat completed-work metadata questions as direct

Examples:

- file name
- file path/location
- where it is saved
- what the saved artifact is called

These should be answered from direct orchestration/artifact context, not delegated again.

### 3. Preserve artifact-detail follow-ups as continue-active-work

Examples:

- ask the manager/experts what is inside the file they made
- ask for a specific item from the produced output
- continue the completed work on the same artifact/result

These should stay `continue_active_work`.

### 4. Do not reset on complaint/correction when user is still pursuing the same task

The Nvidia complaint should have triggered repair/continuation of the Nvidia report task, not `let's start fresh`.

### 5. Stop exposing raw tool-call assistant output

`<tool_call>...</tool_call>` must never be user-visible.

## Suggested Investigation Order For The Next Session

1. Reproduce and inspect why `Please tell me five jokes.` became `continue_active_work`
2. Reproduce and inspect why completed file-location question became delegated continuation
3. Trace how `tool_assisted` response leaked raw `<tool_call>` text into chat
4. Inspect why complaint/correction on Nvidia was treated as topic reset
5. Add protection so active-work summary is not overwritten by narrow follow-up metadata questions

## Files Likely Relevant

- `chanakya/chat_service.py`
- `chanakya/templates/index.html`
- `chanakya/static/js/air_voice.js`
- `chanakya/test/test_agent_manager.py`
- `tasks/task-chat-work-execution-policy.md`

## Current Test Status At End Of This Session

These were green before this handoff note was requested:

- `chanakya/test/test_agent_manager.py`: 97 passed
- `chanakya/test/test_backend_switching.py`: 24 passed
- `chanakya/test/test_agent_configuration.py`: 19 passed
- `chanakya/test/test_domain_foundation.py`: 9 passed

## One Important Caution

The current conversation includes a user message with a system-reminder block saying the mode changed from plan to build. That block should not be copied into routing logic or user-visible content. It is only an instruction artifact from this session and not part of the product behavior.
