# PRD: Classic Chat Explicit Work Tools Refactor

**Date:** 2026-04-16
**Status:** Draft
**Owner:** OpenCode

## Summary

Refactor Chanakya so classic chat no longer performs any automatic task delegation to the Agent Manager. Classic chat should become a voice-first direct assistant that uses only its own runtime tools unless the user explicitly asks it to interact with work. Work orchestration remains available through `/work` mode and through explicit work-management tools that Chanakya can call on the user's instruction.

This refactor is intended to remove unstable routing behavior from classic chat, reduce surprise handoffs, simplify voice interactions, and preserve the multi-agent work system as an explicit user-controlled capability.

## Background

The current classic chat implementation in `chanakya/chat_service.py` mixes multiple responsibilities:

- direct assistant behavior
- automatic delegation routing
- active-work continuation and replacement logic
- waiting-for-input handoff behavior
- background completion mirroring back into classic chat
- voice-safe deferred delivery of delegated results

This has made classic chat behavior hard to reason about and has produced routing/state bugs. The repository already contains strong primitives for work/task management, including:

- `works`
- `tasks`
- `task_events`
- work session mappings
- task input submission APIs
- polling and SSE-style notification support

The proposed refactor does not remove the work system. It changes classic chat from an implicit delegation surface to an explicit control surface.

## Problem Statement

Automatic delegation from classic chat is not consistently reliable and introduces product ambiguity:

- the system may delegate when the user wanted a direct answer
- classic chat may continue or replace the wrong work item
- delegated completions may re-enter classic chat at awkward times
- voice interaction quality degrades when hidden background state drives replies
- the classic experience becomes harder to trust because Chanakya appears to act on its own instead of following the user's intent

The user wants Chanakya to do everything by itself in classic chat and only use work tools when explicitly asked.

## Product Goal

Make classic chat the best possible voice-first direct assistant experience while preserving work/task orchestration as an explicit, user-invoked capability.

## User Requirements

These requirements were explicitly requested by the user and must be preserved.

1. Automatic task delegation must be removed completely from classic chat.
2. Classic chat should become perfect for voice-based interactions using the tools it already has access to.
3. Chanakya should not delegate any task to Agent Manager automatically from classic chat.
4. Chanakya should have task/work management capability exposed as a tool.
5. The work-management capability must allow Chanakya to:
   - list all current tasks or work items
   - get the status of those tasks or work items
   - send a message to a task or work item
   - access pending messages
6. There must be an endpoint for checking pending messages from tasks/work items.
7. Pending messages must support at least these two categories:
   - input needed messages
   - task completion messages or responses
8. The app should periodically check that pending-messages endpoint.
9. If a pending work message is present, the classic app page should show a notification.
10. The classic app should provide a field or affordance for opening that work's chat page directly from the notification area.
11. The expected behavior is that Chanakya does everything by itself and only uses work tools when the user says to do so.

## Scope

### In Scope

- remove automatic classic-chat delegation and classic router-driven delegation paths
- keep `/work` mode and its multi-agent behavior available
- add explicit work-management tool surface for Chanakya
- add backend support for pending work messages
- add classic UI polling and notification display for work-originated updates
- add direct navigation from classic UI notifications to the corresponding work chat
- update tests to reflect the new classic-chat contract

### Out of Scope

- removing the work system itself
- removing Agent Manager from `/work` mode
- redesigning the full `/work` UI beyond what is needed for linking/navigation
- changing the user's required seed text unless separately requested
- building a generalized notification center for unrelated app events

## Target Product Behavior

### Classic Chat

1. Classic chat always handles the user message directly.
2. Classic chat never auto-creates work.
3. Classic chat never auto-continues work.
4. Classic chat never auto-hands off to Agent Manager.
5. Classic chat may use its own normal runtime tools.
6. Classic chat may use work-management tools only when the user explicitly asks it to inspect, message, monitor, or interact with work.

### Work Mode

1. `/work` continues to be the explicit surface for multi-agent delegated execution.
2. Existing work/task orchestration behavior should remain available there unless separately refactored.

### Notifications

1. Work-originated updates should not silently hijack classic chat.
2. Instead, classic chat should surface a notification when there is a pending work update.
3. The user should be able to jump directly to the relevant work chat from that notification.

## Core Product Principles

1. Explicit over implicit.
2. Voice-first interactions in classic chat.
3. Work is a controllable subsystem, not a hidden routing destination.
4. Classic chat state and work state should remain clearly separated.
5. Notifications should inform the user without interrupting the current classic interaction.

## Functional Requirements

### FR1: Remove Automatic Classic Delegation

Classic chat must not automatically:

- invoke the classic router for delegation decisions
- create a new work item
- continue an active work item
- replace an active work item
- background-run delegated work and mirror results into classic chat as if they were part of the same execution path

### FR2: Keep Direct Classic Runtime Behavior

Classic chat should continue to:

- run the direct Chanakya runtime
- use the tools already available to Chanakya
- support voice-friendly response delivery
- preserve direct chat history

### FR3: Add Explicit Work Tools

Chanakya must have access to explicit work-management tools that can be used when the user asks.

Minimum tool capabilities:

1. `list_works`
   - returns current works with identifiers and basic metadata
2. `get_work_status`
   - returns the current state of one work item
3. `send_message_to_work`
   - sends a user-authored message into a work item's chat/task context
4. `get_pending_work_messages`
   - returns pending unread work-originated updates

## Tool Requirements

### Tool: `list_works`

Minimum output fields:

- `work_id`
- `title`
- `status`
- `updated_at`

Recommended additional fields:

- short summary or description
- whether user input is needed
- whether unread pending messages exist

### Tool: `get_work_status`

Minimum inputs:

- `work_id`

Minimum output fields:

- `work_id`
- `title`
- `status`
- latest summary or concise progress text
- whether blocked or waiting for input
- latest update timestamp

### Tool: `send_message_to_work`

Minimum inputs:

- `work_id`
- `message`

Expected behavior:

- append a message into the target work's user-facing task/chat flow
- preserve traceability of the message origin
- return success/failure and enough metadata for the UI or agent to confirm the action

### Tool: `get_pending_work_messages`

Expected behavior:

- return pending unread work updates across work items
- support filtering by type and optionally by `work_id`
- support acknowledgement or separate mark-as-read behavior

Minimum pending message categories:

- `input_required`
- `completed`

Minimum output fields per item:

- `message_id`
- `work_id`
- `type`
- `title`
- `text`
- `created_at`
- `target_url` or enough data to construct a direct work-chat link
- `acknowledged` or `read` status

## API Requirements

### Pending Messages Endpoint

There must be an endpoint dedicated to pending work messages.

Minimum requirements:

1. return pending unread messages for work items
2. distinguish at least:
   - input needed
   - task completion
3. include enough metadata to render a classic notification and open the work chat directly
4. support periodic polling from the classic app

Recommended shape:

- `GET /api/works/pending-messages`
- optional query params:
  - `since`
  - `work_id`
  - `include_acknowledged`

Recommended companion APIs:

- `POST /api/works/pending-messages/<message_id>/ack`
- or a bulk acknowledgement endpoint

### Existing API Reuse

The implementation should reuse existing work/task/event infrastructure where possible instead of inventing a parallel storage model.

Existing primitives already present in the repo include:

- `GET /api/tasks`
- `GET /api/task-events`
- `POST /api/tasks/<task_id>/input`
- `GET /api/works`
- `GET /api/works/<work_id>/sessions`
- `GET /api/stream`

## Frontend Requirements

### Classic App Notification Behavior

The classic app page should periodically check the pending-messages endpoint.

When pending work messages exist, the UI should:

1. show a visible notification indicator
2. render the pending message summary
3. distinguish input-needed vs completion notifications
4. provide an affordance to open the corresponding work chat directly

### UX Requirements

1. Notifications must not interrupt or overwrite the current classic chat turn.
2. Notifications should be informative, lightweight, and voice-compatible.
3. The notification area should make it obvious which work item the message belongs to.
4. Opening the work chat should be one action from the notification.

### Navigation Requirement

The classic UI must provide a field, control, or direct link that opens the target work's chat page from the notification context.

## Data And Domain Requirements

The system needs a stable concept of a pending work-originated message. This may be implemented via:

- a new persistence model
- a derived view over existing `task_events`
- or another reuse-based approach

Regardless of storage strategy, the product contract must support:

- unread vs acknowledged state
- work association
- message type classification
- timestamp ordering
- direct navigation to work context

## Voice And Interaction Requirements

This refactor is explicitly motivated by voice-first classic chat.

Therefore:

1. Classic chat should avoid background behavior that unexpectedly speaks delegated results.
2. Work updates should arrive as notifications, not as surprise classic-chat assistant replies.
3. Voice interaction should remain focused on the current direct conversation.
4. Polling or notification updates must not break recording or active assistant playback behavior.

## Migration Requirements

### Backend Migration

The implementation must remove or bypass classic-chat-only delegation behavior while preserving `/work` mode behavior.

Likely affected areas:

- `chanakya/chat_service.py`
- `chanakya/app.py`
- `chanakya/store.py`
- relevant models and tests

### Test Migration

Tests should be updated to reflect the new contract.

Tests that currently validate classic auto-delegation should be removed or rewritten.

New tests should cover:

- classic chat never auto-delegates
- work tools are available and behave explicitly
- pending work messages endpoint behavior
- notification rendering behavior in classic UI
- direct navigation from notification to work chat

## Acceptance Criteria

1. In classic chat, a complex request no longer starts delegated work automatically.
2. In classic chat, Chanakya answers directly using its own tools unless the user explicitly asks to inspect or interact with work.
3. Chanakya can list works, inspect work status, and send a message to work through explicit tools.
4. A dedicated endpoint exposes pending work messages.
5. Pending work messages distinguish at least input-needed and completion events.
6. The classic UI polls for pending work messages and shows notifications.
7. Each notification can open the corresponding work chat directly.
8. `/work` mode still supports delegated multi-agent behavior.
9. No automatic classic-chat delegation behavior remains in the classic execution path.

## Non-Goals And Guardrails

1. Do not reintroduce deterministic routing shortcuts in classic chat as a replacement for delegation.
2. Do not silently convert classic-chat requests into work actions.
3. Do not overload classic chat notifications with full work transcripts.
4. Do not create a second disconnected task/work state model if existing store primitives can support the feature.

## Risks

1. Some tasks previously solved by auto-delegation may appear less capable in classic chat unless direct-tool coverage is good enough.
2. If work tools are too implicit, the system may drift back toward hidden delegation behavior.
3. If pending messages are derived poorly from raw task events, notifications may be noisy or duplicated.
4. If acknowledgement state is missing, the classic UI may repeatedly surface the same work update.

## Open Questions

1. Should work notifications be stored as their own durable entity or derived from existing `task_events` plus acknowledgement metadata?
2. When Chanakya is asked to send a message to work, should it target the work-level Chanakya session, a root task, or a currently waiting task when one exists?
3. Should classic chat be allowed to ask one clarification question before using a work tool when the user references work ambiguously?
4. Should work notifications also use existing SSE infrastructure in addition to polling, or should the first version be polling-only?
5. Should `classic_active_work` be removed entirely, or retained only as a UI convenience for navigation/history with no routing authority?

## Comment for an AI coding agect for this PRD

### Thoughts

1. This refactor should be treated as a simplification project, not as a prompt-tuning project.
2. The most important product boundary is: classic chat is direct, `/work` is delegated, and work tools are explicit bridges between them.
3. The repo already has enough task/work primitives that the cleanest implementation is probably a reduction of special-case routing logic, not a large new architecture.
4. The current classic complexity is concentrated in `chanakya/chat_service.py`; that file should be simplified carefully and incrementally.
5. The frontend already has polling patterns and SSE infrastructure that can likely support a work-notification inbox with relatively small UI changes.

### Questions

1. Do you want the first implementation to keep legacy classic delegated completion messages in old sessions readable but stop producing any new ones, or should the old path be removed immediately and fully?
2. Should explicit work tools be exposed only to Chanakya, or also as user-facing REST operations beyond the existing `/api/tasks` and `/api/works` APIs?
3. For `send_message_to_work`, what is the authoritative target when a work has multiple relevant sessions or tasks?
4. Should pending work messages be acknowledged automatically when the user opens the work chat, or only when the classic UI explicitly marks them read?

### Recommendations

1. Keep `/work` mode behavior intact during the first slice. Do not mix this refactor with a `/work` orchestration redesign.
2. First remove classic auto-delegation cleanly, then add explicit work tools, then add the pending-message notification UI.
3. Prefer building a small pending work inbox abstraction over directly exposing raw task events to the classic UI.
4. Preserve existing voice safety behavior in classic chat, but redirect work-originated updates into notifications instead of assistant-message injection.
5. Minimize changes to persistence unless acknowledgement/read tracking truly requires a dedicated model.
6. Add regression tests at the contract level rather than reproducing the old router edge cases.
