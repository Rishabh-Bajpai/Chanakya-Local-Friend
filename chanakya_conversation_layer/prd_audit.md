# PRD Audit

This file audits `prd.md` against the implemented MVP.

## Executive Status

- `tasks.md`: complete through Step 13.
- MVP slice in `prd.md`: substantially complete.
- Full PRD: not 100% complete yet.

## Fully Implemented

- reusable wrapper around a core agent
- Flask app and browser GUI
- transcript storage by `session_id`
- explicit working memory with structured schema
- episodic session summary
- rule-based dialogue policy before final wording
- disclosure planning with unrevealed-item tracking
- response realization and staged delivery messages
- yield/resume flow via saved runtime state
- short-term preference signals
- interruption handling with suspended threads
- lightweight critique pass before send
- optional filler for delayed answers
- debug endpoints for transcript, working memory, episodic summary, and aggregated debug state
- offline evaluation harness with pass/fail reporting by feature area

## Done In Spirit, But Narrower Than The Full PRD

- `state machine / graph runtime`: implemented as explicit saved-state flow and resumable wrapper logic, but not as a generalized node graph engine
- `core agent structured returns`: adapter architecture exists, but the default demo path still often works from prose-like responses rather than a richer structured fact contract
- `dual-mode UI`: developer inspection is strong; user-mode polish is still limited
- `success metrics`: offline evaluation exists, but product-grade human ratings, dashboards, and A/B testing are not implemented

## Not Fully Implemented Yet

- all PRD-listed dialogue acts are not present; implemented acts are:
  - `DIRECT_ANSWER`
  - `ASK_CLARIFICATION`
  - `ASK_PREFERENCE`
  - `DISCLOSE_ONE_ITEM`
  - `SUMMARIZE_THEN_PAUSE`
  - `YIELD_TO_USER`
  - `CLOSE`
- PRD-listed but not yet implemented as first-class acts:
  - `ACKNOWLEDGE`
  - `OFFER_OPTIONS`
  - `CHECK_READINESS`
  - `REPAIR`
- long-term memory / persistent preference profile system
- human rubric workflow from the PRD evaluation plan
- A/B testing of base agent vs wrapper variants
- product analytics for naturalness, pacing, abandonment, and complaint rates
- real latency-based filler with live tool progress instead of request-metadata simulation
- richer multi-agent or multi-core-agent packaging conventions

## Bottom Line

If the question is whether all items in `tasks.md` are done, the answer is yes.

If the question is whether every element mentioned anywhere in `prd.md` is fully implemented, the answer is no. The MVP core is complete, but several post-MVP PRD elements remain future work.
