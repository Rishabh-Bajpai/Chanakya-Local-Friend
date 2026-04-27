# /work Group Chat Orchestration Plan

## Goal

Replace the current strict sequential `/work` orchestration with a manager-led group chat workflow where:

- `Chanakya` remains the user-facing agent in the work session.
- `Agent Manager` acts as the orchestrator and decides which participant speaks next.
- `CTO`, `Informer`, `Developer`, `Researcher`, `Writer`, and `Tester` become first-class direct group-chat participants.
- All agent turns are visible in the `/work` transcript.
- Clarification questions are always surfaced to the user through `Chanakya`, never directly from worker agents.
- Existing migration compatibility is not required because the database will be reset manually.

## Decisions Locked In

- Participants: `Chanakya + Manager + CTO + Informer + Developer + Researcher + Writer + Tester`
- Turn-taking: `Agent Manager decides every turn`
- Clarifications: `Only Chanakya asks the user`
- Transcript visibility: `All agent turns visible`
- Backward compatibility: `Ignore previous works / no migration path required`
- Optimization priority: `Correctness + clarity over raw latency`

## Desired End State

- Each `work_id` still owns a stable dedicated session per agent.
- Each new user message in `/work` still lands in the Chanakya work session for that work.
- Before each agent turn, the selected agent receives synchronized prior group-chat context for that same work.
- The manager no longer runs hidden internal sequential pipelines like `CTO -> Developer -> Tester` or `Informer -> Researcher -> Writer`.
- The manager instead orchestrates a visible collaborative group chat and decides the next speaker until termination.
- Waiting-for-input, request/task tracking, notifications, artifacts, and per-work histories remain intact.

## Implementation Strategy

### Phase 1: Stabilize Current Work State Model

- [ ] Add an explicit per-work orchestration mode field or equivalent runtime constant for `/work` so group chat becomes the only active mode after the DB reset.
- [ ] Audit all current `/work` assumptions in `chat_service.py`, `agent_manager.py`, `maf_workflows.py`, and `app.py` that depend on sequential child-task trees.
- [ ] Add a per-work execution lock to prevent concurrent `/work` messages from racing in the same work session/workspace.
- [ ] Replace the current `find_waiting_input_task()` single-task assumption with explicit pending-interaction state for the active work conversation.
- [ ] Define the minimal persisted state required for resumable group chat turns:
  - [ ] active speaker
  - [ ] pending clarification owner
  - [ ] manager termination state
  - [ ] latest synchronized conversation cursor

### Phase 2: Introduce Group Chat Orchestration Runtime

- [ ] Add a new manager-owned group chat orchestration path using `agent_framework.orchestrations.GroupChatBuilder`.
- [ ] Reuse the local group-chat pattern already present in `chanakya/subagents.py` as the starting implementation reference.
- [ ] Implement a manager-controlled speaker selector instead of round robin.
- [ ] Define a termination condition for `/work` group chat that is manager-safe and bounded.
- [ ] Ensure the manager can end the conversation when:
  - [ ] the user request has been satisfied
  - [ ] clarification is required
  - [ ] a failure/blocker must be surfaced
  - [ ] max rounds are reached
- [ ] Add cleanup/normalization so group-chat orchestration scaffolding is not leaked into visible user-facing messages.

### Phase 3: Flatten Agent Topology

- [ ] Remove the current hidden hierarchy assumption where the manager routes to a specialist and that specialist internally drives workers.
- [ ] Refactor participant construction so `CTO`, `Informer`, `Developer`, `Researcher`, `Writer`, and `Tester` are all direct participants in the same work conversation.
- [ ] Keep `Agent Manager` as orchestrator only, not a hidden worker chain runner.
- [ ] Decide and encode participant role boundaries explicitly:
  - [ ] `CTO` gives software direction/review, but does not own a hidden subworkflow
  - [ ] `Informer` gives research/writing direction/review, but does not own a hidden subworkflow
  - [ ] `Developer` implements
  - [ ] `Researcher` gathers facts
  - [ ] `Writer` drafts/polishes
  - [ ] `Tester` validates
- [ ] Preserve the ability for the manager to pick only the participants needed for a turn instead of forcing all agents to speak.

### Phase 4: Rework Prompting Model

- [ ] Rewrite manager prompt for orchestrator behavior instead of route-then-delegate behavior.
- [ ] Remove prompt language that assumes strict two-stage pipelines like researcher->writer or developer->tester.
- [ ] Rewrite participant prompts so each agent understands:
  - [ ] it is in a shared group chat
  - [ ] all participants may see prior messages
  - [ ] it should only speak when selected
  - [ ] it must stay in role and avoid re-explaining orchestration
  - [ ] it must not ask the user directly
- [ ] Add explicit manager rules for clarification:
  - [ ] if a selected agent needs user input, it tells the manager
  - [ ] the manager causes Chanakya to ask the user
  - [ ] the follow-up user answer is reinjected into the same work conversation
- [ ] Reduce repeated sandbox/file policy boilerplate by centralizing reusable prompt addenda.
- [ ] Introduce shorter structured response contracts for specialist/worker turns to improve quality and latency.

### Phase 5: Session and History Synchronization

- [ ] Keep stable per-agent work sessions via `ensure_work_agent_session(work_id, agent_id, ...)`.
- [ ] Build explicit group-chat history synchronization before each selected turn.
- [ ] Ensure each participant receives the correct work-scoped transcript, not just its isolated agent-local memory.
- [ ] Decide how much history is injected each turn:
  - [ ] full visible transcript window
  - [ ] compact manager summary + recent turns
  - [ ] agent-local session history + synchronized group conversation excerpt
- [ ] Refactor `/api/works/<work_id>/history` expectations so the visible work conversation and internal agent sessions remain understandable together.
- [ ] Validate that all visible `/work` transcript turns are reproducible from persisted state after reload.

### Phase 6: Clarification and HITL Flow

- [ ] Replace the current developer-only clarification resume path with group-chat-native pending-input handling.
- [ ] Allow any participant to trigger a clarification request through the manager.
- [ ] Persist which agent requested clarification and why.
- [ ] Ensure Chanakya is the only visible agent that asks the user the question.
- [ ] On user reply, resume the same work conversation without spawning an unrelated new workflow branch.
- [ ] Keep task/request status transitions accurate for `waiting_input`, `in_progress`, `done`, and `failed`.

### Phase 7: Task/Event Model Refactor

- [ ] Review whether the current task tree still matches the new group-chat execution semantics.
- [ ] Replace misleading sequential workflow task naming such as `developer_execution`, `tester_execution`, `researcher_execution`, `writer_execution` when they are no longer children of a strict pipeline.
- [ ] Decide whether group-chat turns should be represented as:
  - [ ] one root task with turn events only
  - [ ] one root task plus participant turn tasks
  - [ ] one root task plus higher-level contribution tasks
- [ ] Update task events to capture:
  - [ ] selected speaker
  - [ ] manager selection reason
  - [ ] clarification requested
  - [ ] visible message emitted
  - [ ] conversation terminated
- [ ] Preserve notifications and work completion semantics at the root task level.

### Phase 8: Artifact and Workspace Safety

- [ ] Preserve the current per-work shared sandbox/workspace model.
- [ ] Fix artifact attribution so files created during one request are not ambiguously claimed by later requests in the same work.
- [ ] Decide whether artifacts should be attached to:
  - [ ] current request only
  - [ ] current request plus originating agent turn
  - [ ] work-level lineage chain
- [ ] Ensure all participant prompts consistently require exact workspace path reporting for generated deliverables.

### Phase 9: `/work` UI and API Alignment

- [ ] Update `/work` frontend assumptions from “single assistant reply with internal workflow” to “multi-agent visible transcript”.
- [ ] Ensure speaker identity is displayed cleanly for each visible agent turn.
- [ ] Add UI-safe handling for manager-selected pauses, waiting-for-input, and terminated group-chat rounds.
- [ ] Ensure the existing work history popup still renders the new group-chat model intelligibly.
- [ ] Keep all current `/work` affordances that should remain intact:
  - [ ] work creation
  - [ ] stable work id
  - [ ] saved work reopening
  - [ ] pending notifications
  - [ ] artifacts per work

### Phase 10: Verification and Regression Coverage

- [ ] Add tests for manager-selected speaker orchestration in `/work`.
- [ ] Add tests for persisted per-agent work sessions under the new group-chat model.
- [ ] Add tests for visible transcript ordering across multiple agent turns.
- [ ] Add tests for clarification routing through Chanakya only.
- [ ] Add tests for resume-after-user-input on non-developer participants.
- [ ] Add tests for concurrent work-message rejection/serialization.
- [ ] Add tests for artifact attribution across multiple requests in one work.
- [ ] Add tests for work history API correctness under group chat.

## Known Current Bugs / Risks To Address During Refactor

- [ ] Remove or redesign the current “exactly one waiting task” assumption.
- [ ] Prevent overlapping `/work` messages from corrupting shared session/workspace state.
- [ ] Eliminate developer-only clarification resume behavior.
- [ ] Reduce history-loss risk from over-compressed work session context.
- [ ] Remove prompt bloat and repetitive workflow boilerplate that hurts inference speed.

## Suggested Delivery Order

- [ ] First land the state model + execution lock + pending-input redesign.
- [ ] Then land the new manager-led group chat orchestration behind the `/work` path.
- [ ] Then flatten participant topology and replace sequential prompts.
- [ ] Then update task/event persistence and `/work` history rendering.
- [ ] Finally add regression coverage and clean up obsolete sequential code paths.

## Open Questions To Resolve During Build

- [ ] Should `Chanakya` be a true speaking participant inside the group chat runtime, or remain an external user-facing bridge that mirrors manager decisions into the visible work transcript?
- [ ] Should the manager ever emit visible content directly, or only select other agents unless summarization/termination is needed?
- [ ] What exact termination policy should end a work conversation without cutting off useful peer review too early?
- [ ] How much prior transcript should be synchronized into each turn to balance correctness against token cost?
