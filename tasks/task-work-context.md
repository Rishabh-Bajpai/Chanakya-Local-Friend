# Work Context Feature Tracker

## Source

- PRD: `tasks/prd-work-context.md`
- App: `chanakya/`

## Objective

Implement a dedicated Work route and work-scoped multi-agent memory while preserving existing chat/session behavior.

## Milestones

### Milestone W1 - Domain and Persistence

- [X] Add `WorkModel` in `chanakya/model.py`
- [X] Add `WorkAgentSessionModel` in `chanakya/model.py`
- [X] Add store repositories/methods for works and work-agent session mapping
- [X] Add DB updater support in `scripts/update_database.py`

Validation:

- Create a work record and verify it persists.
- Ensure `(work_id, agent_id)` maps to one stable session id.

### Milestone W2 - APIs and Chat Wiring

- [X] Add `POST /api/works`
- [X] Add `GET /api/works`
- [X] Add `GET /api/works/<work_id>/sessions`
- [X] Add `GET /api/works/<work_id>/history`
- [X] Add optional `work_id` support to `/api/chat`

Validation:

- Create and list works via API.
- Send chat with `work_id` and verify session is reused for that work.

### Milestone W3 - Manager/Worker Work-Scoped Sessions

- [X] Pass `work_id` through chat service to manager execution context
- [X] Resolve work-scoped session ids for profile prompt execution
- [X] Keep non-work execution unchanged

Validation:

- Trigger delegated workflow under a work and verify worker histories are recorded under mapped sessions.

### Milestone W4 - Work UI Route

- [X] Add `/work` route and `work.html` template
- [X] Add create/select work UI
- [X] Show Chanakya work chat
- [X] Show grouped per-agent histories for selected work

Validation:

- Create multiple works and switch between them.
- Confirm histories are isolated per work.

### Milestone W5 - Tests and Polish

- [X] Add focused tests for new store/API behavior
- [X] Run targeted pytest suite
- [X] Update tracker checkboxes and done log

## Done Log

- 2026-04-04: Created PRD `tasks/prd-work-context.md` and this work-context tracker.
- 2026-04-04: Implemented work domain models, work/session APIs, work-aware manager execution context, dedicated `/work` UI, and coverage tests.
