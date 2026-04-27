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

## Progress Notes

- Implemented a new manager-led `/work` group chat execution path using `GroupChatBuilder`.
- Switched `/work` manager execution away from the strict hidden sequential specialist pipeline.
- Added per-work execution locking in `ChatService` to reduce same-work race conditions.
- Added visible multi-agent transcript persistence into the Chanakya work session.
- Added mirrored transcript persistence into dedicated per-agent work sessions for continuity.
- Added group-chat-native waiting-input handling through the manager task instead of developer-only resume.
- Updated `/work` transcript rendering to show visible speaker labels for agent turns.
- Added focused regression tests for visible agent turns and waiting-input resume.
- Hardened the new group-chat path against transient AIR/provider `502` failures with automatic retry.
- Bounded seeded visible work-history context for follow-up group-chat turns to reduce prompt blow-up risk.
- Replaced the builder-default zero-retry agent-based orchestrator construction with a manual MAF workflow build that gives the manager-orchestrator explicit retry attempts for malformed `next_speaker` outputs.
- Refactored `/api/works/<work_id>/history` to expose a first-class shared conversation payload plus richer per-agent session stats for the Agent Histories UI.
- Added a group-chat-first inspector trace that captures manager decisions, per-call prompt/input packets, and participant tool-call traces for new `/work` runs.
- Replaced `/work` auto-resume's implicit single-waiting-task scan with an explicit root-task pending-interaction marker so the active clarification can be resumed reliably even if stale waiting tasks remain in session history.
- Added persisted group-chat runtime state on root/manager tasks covering active speaker, pending clarification owner, manager termination state, and latest synchronized conversation cursor.
- Added explicit manager speaker-selection and termination task events for `/work` group chat.
- Tightened participant/orchestrator prompts with explicit role boundaries and removed raw `NEEDS_USER_INPUT:` scaffolding from visible `/work` transcript turns.
- Normalized bounded round-limit termination into explicit persisted failure state and richer termination metadata for `/work` group chat.
- Added explicit clarification-request and visible-message task events plus a persisted transcript-context policy marker for group-chat runs.
- Added work-history `active_runtime` state and explicit artifact lineage metadata so reload state and request-vs-latest artifact attribution are first-class in the `/work` APIs.

## Implementation Strategy

### Phase 1: Stabilize Current Work State Model

- [x] Add an explicit per-work orchestration mode field or equivalent runtime constant for `/work` so group chat becomes the only active mode after the DB reset.
- [x] Audit all current `/work` assumptions in `chat_service.py`, `agent_manager.py`, `maf_workflows.py`, and `app.py` that depend on sequential child-task trees.
- [x] Add a per-work execution lock to prevent concurrent `/work` messages from racing in the same work session/workspace.
- [x] Replace the current `find_waiting_input_task()` single-task assumption with explicit pending-interaction state for the active work conversation.
- [ ] Define the minimal persisted state required for resumable group chat turns:
  - [x] active speaker
  - [x] pending clarification owner
  - [x] manager termination state
  - [x] latest synchronized conversation cursor

### Phase 2: Introduce Group Chat Orchestration Runtime

- [x] Add a new manager-owned group chat orchestration path using `agent_framework.orchestrations.GroupChatBuilder`.
- [x] Reuse the local group-chat pattern already present in `chanakya/subagents.py` as the starting implementation reference.
- [x] Implement a manager-controlled speaker selector instead of round robin.
- [x] Define a termination condition for `/work` group chat that is manager-safe and bounded.
- [ ] Ensure the manager can end the conversation when:
  - [x] the user request has been satisfied
  - [x] clarification is required
  - [x] a failure/blocker must be surfaced
  - [x] max rounds are reached
- [x] Add cleanup/normalization so group-chat orchestration scaffolding is not leaked into visible user-facing messages.

### Phase 3: Flatten Agent Topology

- [x] Remove the current hidden hierarchy assumption where the manager routes to a specialist and that specialist internally drives workers.
- [x] Refactor participant construction so `CTO`, `Informer`, `Developer`, `Researcher`, `Writer`, and `Tester` are all direct participants in the same work conversation.
- [x] Keep `Agent Manager` as orchestrator only, not a hidden worker chain runner.
- [ ] Decide and encode participant role boundaries explicitly:
  - [x] `CTO` gives software direction/review, but does not own a hidden subworkflow
  - [x] `Informer` gives research/writing direction/review, but does not own a hidden subworkflow
  - [x] `Developer` implements
  - [x] `Researcher` gathers facts
  - [x] `Writer` drafts/polishes
  - [x] `Tester` validates
- [ ] Preserve the ability for the manager to pick only the participants needed for a turn instead of forcing all agents to speak.

### Phase 4: Rework Prompting Model

- [x] Rewrite manager prompt for orchestrator behavior instead of route-then-delegate behavior.
- [x] Remove prompt language that assumes strict two-stage pipelines like researcher->writer or developer->tester.
- [ ] Rewrite participant prompts so each agent understands:
  - [x] it is in a shared group chat
  - [x] all participants may see prior messages
  - [x] it should only speak when selected
  - [x] it must stay in role and avoid re-explaining orchestration
  - [x] it must not ask the user directly
- [ ] Add explicit manager rules for clarification:
  - [x] if a selected agent needs user input, it tells the manager
  - [x] the manager causes Chanakya to ask the user
  - [x] the follow-up user answer is reinjected into the same work conversation
- [ ] Reduce repeated sandbox/file policy boilerplate by centralizing reusable prompt addenda.
- [ ] Introduce shorter structured response contracts for specialist/worker turns to improve quality and latency.

### Phase 5: Session and History Synchronization

- [x] Keep stable per-agent work sessions via `ensure_work_agent_session(work_id, agent_id, ...)`.
- [x] Build explicit group-chat history synchronization before each selected turn.
- [ ] Ensure each participant receives the correct work-scoped transcript, not just its isolated agent-local memory.
- [ ] Decide how much history is injected each turn:
  - [x] full visible transcript window
  - [ ] compact manager summary + recent turns
  - [ ] agent-local session history + synchronized group conversation excerpt
- [ ] Refactor `/api/works/<work_id>/history` expectations so the visible work conversation and internal agent sessions remain understandable together.
- [x] Refactor `/api/works/<work_id>/history` expectations so the visible work conversation and internal agent sessions remain understandable together.
- [x] Validate that all visible `/work` transcript turns are reproducible from persisted state after reload.

### Phase 6: Clarification and HITL Flow

- [x] Replace the current developer-only clarification resume path with group-chat-native pending-input handling.
- [x] Allow any participant to trigger a clarification request through the manager.
- [x] Persist which agent requested clarification and why.
- [x] Ensure Chanakya is the only visible agent that asks the user the question.
- [x] On user reply, resume the same work conversation without spawning an unrelated new workflow branch.
- [ ] Keep task/request status transitions accurate for `waiting_input`, `in_progress`, `done`, and `failed`.

### Phase 7: Task/Event Model Refactor

- [ ] Review whether the current task tree still matches the new group-chat execution semantics.
- [ ] Replace misleading sequential workflow task naming such as `developer_execution`, `tester_execution`, `researcher_execution`, `writer_execution` when they are no longer children of a strict pipeline.
- [ ] Decide whether group-chat turns should be represented as:
  - [ ] one root task with turn events only
  - [ ] one root task plus participant turn tasks
  - [ ] one root task plus higher-level contribution tasks
- [ ] Update task events to capture:
  - [x] selected speaker
  - [x] manager selection reason
  - [x] clarification requested
  - [x] visible message emitted
  - [x] conversation terminated
- [ ] Preserve notifications and work completion semantics at the root task level.

### Phase 8: Artifact and Workspace Safety

- [ ] Preserve the current per-work shared sandbox/workspace model.
- [ ] Fix artifact attribution so files created during one request are not ambiguously claimed by later requests in the same work.
- [x] Fix artifact attribution so files created during one request are not ambiguously claimed by later requests in the same work.
- [ ] Decide whether artifacts should be attached to:
  - [ ] current request only
  - [ ] current request plus originating agent turn
  - [ ] work-level lineage chain
- [ ] Ensure all participant prompts consistently require exact workspace path reporting for generated deliverables.

### Phase 9: `/work` UI and API Alignment

- [x] Update `/work` frontend assumptions from “single assistant reply with internal workflow” to “multi-agent visible transcript”.
- [x] Ensure speaker identity is displayed cleanly for each visible agent turn.
- [ ] Add UI-safe handling for manager-selected pauses, waiting-for-input, and terminated group-chat rounds.
- [x] Ensure the existing work history popup still renders the new group-chat model intelligibly.
- [ ] Keep all current `/work` affordances that should remain intact:
  - [ ] work creation
  - [ ] stable work id
  - [ ] saved work reopening
  - [ ] pending notifications
  - [ ] artifacts per work

### Phase 10: Verification and Regression Coverage

- [x] Add tests for manager-selected speaker orchestration in `/work`.
- [x] Add tests for persisted per-agent work sessions under the new group-chat model.
- [x] Add tests for visible transcript ordering across multiple agent turns.
- [x] Add tests for clarification routing through Chanakya only.
- [x] Add tests for resume-after-user-input on non-developer participants.
- [ ] Add tests for concurrent work-message rejection/serialization.
- [ ] Add tests for artifact attribution across multiple requests in one work.
- [x] Add tests for artifact attribution across multiple requests in one work.
- [x] Add tests for work history API correctness under group chat.

## Known Current Bugs / Risks To Address During Refactor

- [x] Remove or redesign the current “exactly one waiting task” assumption.
- [x] Prevent overlapping `/work` messages from corrupting shared session/workspace state.
- [x] Eliminate developer-only clarification resume behavior.
- [ ] Reduce history-loss risk from over-compressed work session context.
- [ ] Remove prompt bloat and repetitive workflow boilerplate that hurts inference speed.

## Suggested Delivery Order

- [ ] First land the state model + execution lock + pending-input redesign.
- [ ] Then land the new manager-led group chat orchestration behind the `/work` path.
- [ ] Then flatten participant topology and replace sequential prompts.
- [ ] Then update task/event persistence and `/work` history rendering.
- [ ] Finally add regression coverage and clean up obsolete sequential code paths.

## Open Questions To Resolve During Build

- [x] Should `Chanakya` be a true speaking participant inside the group chat runtime, or remain an external user-facing bridge that mirrors manager decisions into the visible work transcript?
- [ ] Should the manager ever emit visible content directly, or only select other agents unless summarization/termination is needed?
- [ ] What exact termination policy should end a work conversation without cutting off useful peer review too early?
- [ ] How much prior transcript should be synchronized into each turn to balance correctness against token cost?
