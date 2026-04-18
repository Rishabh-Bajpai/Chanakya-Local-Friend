# Final Release Checklist

Use this checklist before declaring the slim conversation layer release complete.

## 1) Scope and Architecture Locks

- [X] Keep slim three-call path intact: WM routing -> core agent -> delivery planner.
- [X] Confirm no conversation-layer persistent DB is used in runtime path.
- [X] Confirm removed legacy paths stay removed (`/chat/raw`, `/chat/post-processing-only`, debug dashboard).
- [X] Confirm both model selectors work in runtime options and request metadata:
  - [X] `core_agent`
  - [X] `conversation_orchestration`

## 2) Conversation Reliability Locks

- [X] Queue invariants hold in tests:
  - [X] delivered items are never replayed
  - [X] `ack_continue` preserves pending queue
  - [X] adapt modes only replace undelivered content
- [X] Numbered outputs preserve completeness and marker integrity.
- [X] Structured/poem/fetch responses preserve formatting and source fidelity.
- [X] Detailed follow-ups do not regress into over-compressed summaries.
- [X] Low-information follow-ups (`ok`, `nice`, `next`, `continue`) do not cause unintended resets.

## 3) Verification Commands

- [X] `pytest tests/test_conversation_wrapper.py`
- [X] `pytest tests/test_runtime_routes.py`
- [X] `pytest`

## 4) Manual Smoke Checks

- [X] Start app and verify both model dropdowns load options.
- [X] Run one queueing flow: initial answer -> pending queue -> delayed delivery.
- [X] Run one interruption flow with same-topic adaptation.
- [X] Run one clear new-topic query and verify reset behavior.
- [X] Run one pause + `next` flow and verify pending queue is preserved.

## 5) Release Notes and Handoff

- [X] Update `docs/transcript_regression_hardening_tasks.md` with final status.
- [X] Summarize resolved regressions and known limits.
- [X] Keep next work transcript-driven: reproduce -> test -> minimal fix -> verify.

## Known Limits

- Planner quality can still vary by model; wrapper guardrails remain the source of safety for queue integrity and response fidelity.
- Fresh-query detection is heuristic; future multilingual edge cases should continue to be handled through transcript-driven tests.
