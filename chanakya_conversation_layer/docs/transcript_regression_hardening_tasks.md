 # Transcript Regression Hardening Tasks

 Please keep this file updated with new tasks, completed tasks, and any status changes as work progresses. And use `conda activate test` when required.

 ## Phase 1: Build Transcript Regression Coverage

 ### [X] Step 1: Capture Real Transcript Scenarios

- [X] collect recent real user interruption transcripts that exposed queue or WM issues
- [X] normalize each transcript into a compact test scenario with expected state transitions
- [X] group scenarios by acknowledgment, continue, adapt, and reset behavior

 ### [X] Step 2: Add Transcript-Driven Wrapper Tests

- [X] add focused regression cases to `tests/test_conversation_wrapper.py`
- [X] cover low-information follow-ups like `ok`, `nice`, `next`, and `continue`
- [X] cover same-topic constraint changes that must not reset topic state
- [X] cover genuine topic pivots that must clear prior pending state

 ## Phase 2: Lock Queue Invariants

### [X] Step 3: Add Queue Integrity Assertions

- [X] assert that delivered messages are never replayed
- [X] assert that `ack_continue` preserves pending items exactly
- [X] assert that `adapt_remaining` and `adapt_remaining_with_core` only replace undelivered items
- [X] assert that structured numbered-list responses are not silently truncated
- [X] assert that long free-form multi-sentence core responses are not reduced to a single intro line when planner output is incomplete

  ### [X] Step 4: Add State Transition Matrix Tests

  - [X] cover queue present vs queue absent
  - [X] cover manual pause vs no manual pause
  - [X] cover same-topic vs new-topic routing
  - [X] cover core-call-needed vs no-core-call paths

 ## Phase 3: Minimal Wrapper Hardening

  ### [X] Step 5: Tighten Low-Information Follow-Up Handling

  - [X] review current WM-manager outputs for short follow-up messages
  - [X] make the smallest wrapper changes needed to preserve queue correctness
  - [X] keep the latest user message as a hard constraint during same-topic replanning

### [X] Step 6: Keep Wrapper Guardrails Stronger Than Planner Drift

- [X] preserve `ack_continue` behavior from queued state snapshots
- [X] keep structured-response expansion in the wrapper when planner output is incomplete
- [X] add a wrapper fallback for incomplete free-form planner outputs when they drop most of the core response
- [X] ensure queue metadata reflects actual state transitions

 ## Phase 4: Verify and Decide on UI Follow-Up

  ### [X] Step 7: Run Focused Verification

  - [X] run `pytest tests/test_conversation_wrapper.py`
  - [X] run any additional focused tests impacted by wrapper changes
  - [X] manually replay at least one previously failing transcript if needed

  ### [X] Step 8: Evaluate a Small Continue UI Affordance

  - [X] decide whether a simple continue control would reduce ambiguity enough to justify the UI change
  - [X] no additional UI control added in this phase; current pause and queue behavior plus wrapper hardening were sufficient
