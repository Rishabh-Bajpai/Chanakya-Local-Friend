# PRD: Work Context and Multi-Agent Memory

**Document Type:** Product Requirements Document (PRD)
**Version:** V1
**Status:** Approved for implementation
**Owner:** Chanakya app team
**Target Area:** `chanakya/` (Flask UI + domain persistence + manager orchestration)

---

## 1. Problem Statement

The current app stores chat by session, but users cannot group long-running outcomes as a reusable project workspace.

Users need to:

- Create a named unit of work ("Work") in the UI.
- Return to a specific work days later.
- See all agent conversation history for that work.
- Request revisions in that same work without losing context.

---

## 2. Goals

- Add a first-class **Work** entity with durable identity (`work_id`).
- Give every relevant agent a **work-scoped session id**.
- Preserve and reload per-agent history when a user reopens a work.
- Keep existing non-work chat behavior unchanged.
- Add a dedicated UI route for work creation and work-scoped conversations.

---

## 3. Non-Goals (V1)

- Direct user-to-worker-agent chat input controls.
- Fine-grained ACLs across users/workspaces.
- Work archival and deletion lifecycle.
- Cross-work analytics.

---

## 4. Core User Stories

- **US-WORK-001:** User creates a work from the UI and gets a unique `work_id`.
- **US-WORK-002:** User selects a work and sees all agent histories for that work.
- **US-WORK-003:** User sends a new revision request in a selected work; manager and workers execute in the same work context.
- **US-WORK-004:** User switches between works and sees isolated histories per work.
- **US-WORK-005:** Existing `/` route and session-based chat continue working as before.

---

## 5. Functional Requirements

### 5.1 Data Model

- FR-W-1: Add `works` table with id, title, description, status, timestamps.
- FR-W-2: Add `work_agent_sessions` table mapping `work_id + agent_id -> session_id` with uniqueness on `(work_id, agent_id)`.
- FR-W-3: Reuse existing `chat_sessions` and `chat_messages` for message storage.

### 5.2 API

- FR-W-4: `POST /api/works` creates a work.
- FR-W-5: `GET /api/works` lists works.
- FR-W-6: `GET /api/works/<work_id>/sessions` returns mapped agent sessions.
- FR-W-7: `GET /api/works/<work_id>/history` returns message history grouped by agent session.
- FR-W-8: `/api/chat` accepts optional `work_id`; if provided, route request through work-scoped Chanakya session.

### 5.3 Orchestration

- FR-W-9: During manager/specialist/worker execution, agent calls should resolve work-scoped session ids when `work_id` is present.
- FR-W-10: If no `work_id` is provided, existing behavior remains unchanged.

### 5.4 UI

- FR-W-11: Add dedicated `/work` route.
- FR-W-12: Allow creating/selecting works.
- FR-W-13: Show selected work chat with Chanakya.
- FR-W-14: Show per-agent history panels for selected work.

---

## 6. UX Flow

1. User opens `/work`.
2. User creates "Global warming report 2026".
3. System creates `work_id` and initial agent-session mappings.
4. User chats in selected work.
5. Manager delegates; worker conversations are recorded in work-scoped sessions.
6. Days later, user reopens same work and sees all agent histories.
7. User requests revision; execution resumes using same work session mappings.

---

## 7. Acceptance Criteria

- AC-W-1: Creating a work returns a persistent `work_id`.
- AC-W-2: Two different works produce isolated histories.
- AC-W-3: Work history endpoint includes Chanakya + delegated agents with mapped sessions.
- AC-W-4: New message in a selected work appends to that work's Chanakya session.
- AC-W-5: Existing `/` route still works with session-based flow.

---

## 8. Risks and Mitigations

- **Risk:** Existing databases miss new columns/tables.
  - **Mitigation:** Extend `scripts/update_database.py` targets.
- **Risk:** Manager execution context leaks across requests.
  - **Mitigation:** Use request-local context variables for work-aware session resolution.
- **Risk:** UI confusion between global chat and work chat.
  - **Mitigation:** Dedicated `/work` route and explicit selected-work status.

---

## 9. Validation Plan

- Unit tests for store work/session mapping and uniqueness.
- API tests for create/list work and grouped work history.
- Integration test path ensuring work-scoped chat produces persistent messages.
- Manual UI validation: create 2 works, run different tasks, switch and verify isolation.
