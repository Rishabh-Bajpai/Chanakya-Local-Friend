# Issue 2: Build a Developer Debug Dashboard for Full Conversation and Agent Trace Visibility

## Problem

In the future UI, we want to inspect the full behavior of the system turn by turn, not just the chat transcript.

Today the app already exposes some partial debug visibility:
- chat history via `/sessions/<session_id>/history`
- working memory via `/sessions/<session_id>/working-memory`
- episodic summaries via `/sessions/<session_id>/episodic-summary`
- combined state via `/sessions/<session_id>/debug-state`
- structured event logging through `log_event(...)`

But this is still incomplete for serious debugging and development because it does not provide one coherent developer surface showing:
- the user message
- the working memory before and after the turn
- the policy/resume/interruption decisions
- the exact payload/context that goes to the core agent
- the core agent output
- agent-side memory/history/context
- critique/finalization steps
- the final response returned to the UI

For future development, evaluation, and architecture work, we need a dedicated developer-only dashboard that exposes the full pipeline and state transitions.

## Goal

Create a developer-only debug dashboard that shows the complete end-to-end flow of a turn in a timeline-plus-panels format.

This dashboard should make it easy to answer questions like:
- What exactly did the user send?
- What was in working memory before the turn?
- What did the policy engine decide and why?
- What exactly was passed to the core agent?
- What history/context/tools were visible to the agent?
- What did the core agent return?
- What changes were written back into working memory and episodic memory?
- What is the difference between conversation-layer memory and agent-side memory?

## Desired UX

### Primary surface

A dedicated **developer debug dashboard** rather than exposing internals in the normal chat UI.

### Access model

Developer-only. It should be available only in debug/dev mode.

### Presentation model

Use a **timeline plus panels** layout.

#### Timeline

Each turn should show ordered steps such as:
1. user message received
2. working memory loaded
3. preference inference / interruption restore
4. resume or policy selection
5. disclosure planning if applicable
6. exact core-agent request assembled
7. core-agent response received
8. critique/rewrite step
9. working memory update written
10. episodic summary update written
11. final response returned

#### Panels

Separate inspectable panels for:
- chat transcript
- working memory current state
- working memory diff for the selected turn
- episodic summaries
- agent history/context
- agent tool traces or tool availability
- raw event/trace payloads
- final metadata returned by the wrapper

## Scope of Visibility

The dashboard should expose as much detail as reasonably possible for both sides of the system.

### Conversation-layer visibility

Show:
- raw user message
- request metadata
- loaded working memory snapshot
- working memory update preview
- policy decision
- policy reasoning
- disclosure plan / selected undisclosed item
- interruption suspend/restore metadata
- response processor / realizer outputs
- critique status and critique action
- final response metadata
- saved working memory state
- saved episodic summary state

### Core-agent visibility

Show as much of the core-agent side as possible, including:
- exact message payload sent to the core agent
- context/history made available to the agent
- tool list or tool metadata available to the agent
- any tool calls made by the agent
- core-agent raw output before wrapper post-processing
- agent-side stored memory/history if available

Important: the UI should clearly distinguish between:
- **conversation-layer memory**
- **agent memory/history/context**

These should not be mixed together in one panel.

## Architectural Notes

This issue should align with the long-term separation described in `issue_1.md`.

That means the dashboard should make the system boundary visible:
- what belongs to the conversation layer
- what belongs to the core agent app
- what crosses the interface between them

This is especially important once the conversation layer becomes reusable across many MAF agents.

## Required Changes

### 1. Add a trace model for one conversation turn

Define a structured trace object for each handled turn, for example:
- turn id
- session id
- timestamp
- ordered list of pipeline events
- conversation-layer snapshots/diffs
- core-agent request/response payloads
- final returned response

This should be richer than plain logs and suitable for UI rendering.

### 2. Capture wrapper pipeline events as structured data

The current `log_event(...)` stream is useful but insufficient as the main data model.

Add a structured trace capture mechanism around `ConversationWrapper.handle(...)` so that the dashboard can render:
- per-step event payloads
- before/after snapshots
- selected decisions and metadata

### 3. Capture the exact core-agent boundary

Instrument the boundary between wrapper and agent so the dashboard can show:
- what the wrapper sent into the agent
- what context/history was provided
- what the agent returned before any wrapper-side rewriting

If the agent framework does not expose all internals today, capture the maximum available subset and document the remaining gaps.

### 4. Add agent-side debug adapters/hooks

Define a debug/inspection interface for agents so the host app can expose agent details when available, such as:
- current history
- current memory snapshot
- tools available
- tool-call trace

This should be optional so the conversation layer still works with agents that provide limited introspection.

### 5. Add developer-only debug endpoints

In addition to existing endpoints, add richer debug endpoints for:
- session trace list
- specific turn trace
- conversation-layer snapshots
- agent-side debug state
- wrapper-to-agent boundary payloads

These endpoints should only be enabled in debug/dev mode.

### 6. Build a dedicated debug dashboard UI

Add a dedicated page or mode for the debug dashboard that includes:
- a session selector
- a turn timeline
- synchronized detail panels
- pretty-printed JSON/raw payload inspectors
- diffs for memory changes across turns

### 7. Distinguish raw-agent and wrapped-agent views

Because the host app will eventually expose both a raw agent and a wrapped agent, the dashboard should support comparing:
- raw-agent execution trace
- wrapped-agent execution trace

This will make the added value of the conversation layer visible.

### 8. Handle sensitive/internal data carefully

The dashboard is developer-only, but we should still define what is safe to show by default and what may need redaction or truncation in the future.

## Suggested Implementation Shape

### Trace layers

1. **UI trace model**
   The normalized shape rendered by the dashboard.

2. **Wrapper trace capture**
   Instrumentation inside `ConversationWrapper.handle(...)`.

3. **Agent debug interface**
   Optional introspection hooks implemented by the host app's agents.

### Example sections in the dashboard

1. **Turn Timeline**
   Chronological event stream for the selected turn.

2. **Conversation Layer**
   Working memory before/after, policy decisions, summary updates, critique state.

3. **Agent Boundary**
   Exact input passed to the agent and the exact raw output returned.

4. **Agent Internals**
   History, tools, tool traces, and memory/debug state when available.

5. **Final Response**
   The final user-visible response and metadata.

## Files Likely Involved

- `app/templates/index.html` - likely split or extended with a dedicated debug view
- `app/routes.py` - add richer developer-only debug endpoints
- `app/services/conversation_wrapper.py` - capture structured per-turn traces
- `app/logging_utils.py` - may evolve into or feed a trace store
- `app/services/core_agent.py` - expose agent boundary payloads and debug hooks
- `app/services/history_provider.py` - support agent history visibility where needed
- future conversation-layer package and host-app boundaries from `issue_1.md`

## Open Questions

1. Should traces be persisted in the database, kept in memory, or both?
2. Do we want side-by-side comparison of raw-agent vs wrapped-agent traces in the first version, or later?
3. How much raw payload detail should be shown by default versus behind expandable sections?
4. What agent debug interface should be required versus optional?

## Acceptance Criteria

1. **Developer-only dashboard exists**: A dedicated debug dashboard is available only in debug/dev mode
2. **Timeline plus panels UI exists**: The UI presents turn execution as a timeline linked to detailed state panels
3. **Full wrapper visibility exists**: The dashboard shows user input, working memory before/after, policy decisions, critique step, summary updates, and final response
4. **Core-agent boundary is visible**: The dashboard shows the exact payload/context sent to the core agent and the raw response received back
5. **Agent internals are exposed when available**: Agent history, tools, memory/debug state, and tool traces are visible through an agent debug interface when supported
6. **Memory domains are separated**: Conversation-layer memory and agent-side memory are shown as distinct concepts in the UI
7. **Per-turn trace data exists**: The backend stores or serves structured trace data suitable for rendering one full turn end to end
8. **Debug endpoints exist**: There are dedicated backend endpoints for retrieving trace and debug data for sessions/turns
9. **Raw vs wrapped support is possible**: The dashboard structure supports inspecting both raw-agent and wrapped-agent execution paths
10. **Current debug features are integrated**: Existing history/working-memory/episodic-summary/debug-state capabilities are incorporated rather than duplicated

## Related Issues

- `issues/issue_1.md` - Split conversation layer into an independent stack
- `issues/issue_4.md` - Replace hard-coded conversation orchestration with LLM-powered planning
- `issues/issue_5.md` - Add a decorator-based integration API on top of the conversation wrapper
