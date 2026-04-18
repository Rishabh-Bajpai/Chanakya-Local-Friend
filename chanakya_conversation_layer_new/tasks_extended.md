# Extended Roadmap: Post-MVP Development

This roadmap starts after `tasks.md` Step 13 and is aligned with the current issue set in `issues/`.

Please keep this file updated with new tasks, completed tasks, and any status changes as work progresses. And use "conda activate test" when required.

## Phase 1: Separate Boundaries and Add Core Observability

### [X] Step 14: Split the System into Two Independent Parts

- [X] create a reusable `conversation_layer` stack that owns wrapper logic, working memory, episodic summary, and orchestration
- [X] create or preserve a separate `core_agent_app` stack that owns agent-specific DB, tools, and bootstrap
- [X] remove direct conversation-layer dependence on the agent's `db.py`
- [X] define clear boundaries between conversation-layer state and agent-owned state
- [X] align this step with `issues/issue_1.md`

### [X] Step 15: Introduce a Stable Agent Interface

- [X] formalize the wrapper-facing agent contract so the conversation layer can treat agents as black boxes
- [X] support local/in-process adapters first
- [X] make dependency injection the normal integration model at the app level
- [X] keep `ConversationWrapper(agent)` as the primary implementation model
- [X] align this step with `issues/issue_1.md`

### [X] Step 16: Add Structured Trace Capture and Agent-Boundary Visibility

- [X] move beyond plain logs and capture structured per-turn traces suitable for UI rendering
- [X] capture the exact wrapper-to-agent boundary, including agent inputs and raw outputs where available
- [X] add backend endpoints for turn traces, session traces, agent-boundary payloads, and debug snapshots
- [X] keep this as the minimum useful subset of developer visibility before larger refactors
- [X] align this step with `issues/issue_2.md`

### [X] Step 17: Fix Resume-Flow Logic Bugs

- [X] fix clarification/rephrase resume so it hands control to the core agent instead of echoing the user message back
- [X] fix topic-scoping resume so it leads to the actual answer instead of stopping at acknowledgement text
- [X] make resume control flow explicit and testable
- [X] add regression tests for both broken paths
- [X] align this step with `issues/issue_3.md`

## Phase 2: Replace Hard-Coded Orchestration

### [X] Step 18: Replace Primary Hard-Coded Planning Components

- [X] replace hard-coded behavior in `policy_engine.py`, `disclosure_planner.py`, `response_processor.py`, and `critique_pass.py`
- [X] add an LLM-powered orchestration path for policy selection, disclosure planning, response planning, and critique/revision
- [X] preserve deterministic fallbacks for unavailable or failing LLM orchestration
- [X] ensure outputs remain structured and safe for `ConversationWrapper` consumption
- [X] align this step with Part 1 of `issues/issue_4.md`

### [X] Step 19: Hybridize Secondary Flow Managers

- [X] reduce heuristic-heavy logic in `interruption_manager.py`, `resume_manager.py`, and `preference_signals.py`
- [X] use hybrid designs where interpretation can be LLM-assisted but state transitions remain deterministic
- [X] improve interruption detection, resume selection, and preference inference beyond token matching
- [X] keep resulting state transitions inspectable and testable
- [X] align this step with Part 2 of `issues/issue_4.md`

### [ ] Step 20: Expand Dialogue Act Coverage After Orchestration Refactor

- [X] add first-class support for `ACKNOWLEDGE`
- [X] add first-class support for `OFFER_OPTIONS`
- [X] add first-class support for `CHECK_READINESS`
- [X] add first-class support for `REPAIR`
- [X] ensure new acts work in both LLM-powered and fallback orchestration paths

## Phase 3: Productize the Integration Surface

### [ ] Step 21: Add Convenience Integration API

- [X] add a convenience helper such as `with_conversation_layer(agent, ...)`
- [X] keep the wrapper as the source of truth and avoid duplicated orchestration logic
- [X] optionally add class-decorator support later only if it remains simple and unsurprising
- [X] add examples showing raw-agent and wrapped-agent usage side by side
- [X] align this step with `issues/issue_5.md`

### [ ] Step 22: Build the Full Developer Debug Dashboard

- [X] create a developer-only debug dashboard with a timeline-plus-panels layout
- [X] show user message, working memory before/after, policy decisions, critique steps, episodic summaries, and final response
- [X] expose the exact boundary between the conversation layer and the core agent
- [X] keep conversation-layer memory and agent memory/history/debug state visibly separate
- [X] make trace visibility work for both wrapped-agent and raw-agent paths where possible
- [X] align this step with `issues/issue_2.md`

### [ ] Step 23: Add Observability and Evaluation Reporting

- [ ] persist evaluation runs and scenario reports
- [ ] expose metrics for policy act distribution, critique revisions, interruption recovery, disclosure behavior, and fallback frequency
- [ ] add developer-oriented summaries for recent sessions and evaluation history
- [ ] keep evaluation artifacts aligned with the debug dashboard and trace model

## Phase 4: Support Multiple Agent Backends

### [ ] Step 24: Add MAF A2A Support for Remote Coding Agents

- [X] create an `A2ACoreAgentAdapter` or equivalent remote adapter using MAF's A2A connector
- [X] keep `ConversationWrapper` transport-agnostic
- [X] support local and A2A-backed agents through the same wrapper-facing interface
- [X] preserve remote conversation continuity identifiers such as `context_id` when available
- [X] align this step with `issues/issue_6.md`

### [ ] Step 25: Add Safe A2A Fallback and Capability Handling

- [X] handle remote timeouts, malformed payloads, and transport failures safely
- [X] support capability-limited remote agents without breaking the conversation layer
- [X] add fallback continuity behavior when remote context continuity fails
- [X] keep debug visibility for the wrapper-to-A2A boundary when available
- [X] align this step with `issues/issue_6.md`

### [ ] Step 26: Support Multiple Host Agent Modes

- [X] provide one host-app path for a raw agent and one for a wrapped agent
- [X] make backend selection configuration-driven rather than code-edited
- [X] support side-by-side evaluation of local agents vs remote A2A-backed agents
- [X] maintain a consistent integration contract across all supported agent backends

## Phase 5: Memory and UX Improvements

### [ ] Step 27: Add Long-Term Memory and Stable User Profiles

- [X] create a persistent profile store separate from working memory and episodic summary
- [X] promote repeated short-term preference signals into durable preferences when evidence is strong
- [X] let the wrapper load stable preferences at session start
- [X] add guardrails so transient state does not pollute long-term memory

### [ ] Step 28: Replace Simulated Filler With Real Async Presence

- [X] emit filler from real slow operations instead of request metadata
- [X] track time-to-first-filler and time-to-final-answer
- [X] integrate live tool progress when available
- [X] add tests for real delayed-tool orchestration

### [ ] Step 29: Improve Conversation Quality

- [X] improve option framing, readiness checks, and repair behavior
- [X] refine pacing and disclosure behavior using evaluation feedback
- [X] improve interruption recovery quality once hybrid flow managers are in place
- [X] reduce robotic phrasing without reintroducing hard-coded brittle templates

## Phase 6: Stronger Evaluation and Research Readiness

### [X] Step 30: Human Rubric Evaluation Workflow

- [X] create rubric forms for naturalness, pacing, appropriateness, coherence, usefulness, and non-robotic delivery
- [X] support exporting conversations for human review
- [X] collect side-by-side ratings for base agent vs wrapped agent

### [X] Step 31: A/B Testing Harness

- [X] compare base agent, post-processing-only mode, and full wrapper mode
- [X] compare local-agent and A2A-backed-agent paths where relevant
- [X] collect outcome metrics and human preference ratings
- [X] define promotion criteria for shipping orchestration changes safely

### [X] Step 32: Stress and Safety Testing

- [X] add high-volume interruption tests, noisy-input tests, and adversarial disclosure tests
- [X] verify state isolation across many concurrent sessions
- [X] test failure paths for core-agent errors, remote-agent errors, and partial tool failures

## Ongoing Maintenance Tracks

- [ ] keep `app/evaluation.py` scenario suite growing with every new behavior
- [ ] keep `README.md`, `prd_audit.md`, issue docs, and integration docs aligned with actual implementation
- [ ] maintain strict separation between transcript, working memory, episodic summary, long-term memory, and agent-owned memory
- [ ] keep the debug dashboard and trace model aligned with evolving agent interfaces and backends
