# Task Model Definition

This MVP uses a persistent SQLite-backed task model that is intentionally explicit and inspectable.

## Entities

- `Task`
  - `id`: unique ID (example: `task_ab12cd34ef`)
  - `description`: human-readable work description
  - `owner`: `agent_manager`, `developer_agent`, or `tester_agent`
  - `status`: one of `created`, `ready`, `assigned`, `in_progress`, `waiting_input`, `blocked`, `done`, `failed`
  - `dependencies`: list of task IDs that must be completed first
  - `parent_task_id`: optional parent ID for child tasks
  - `result`: result summary or failure reason
  - `metadata`: context and constraints (e.g., `feature_scope`, simulated failure flags)
  - `created_at`, `updated_at`: timestamps

- `task_transitions`
  - immutable transition events for status changes
  - includes `from_status`, `to_status`, `reason`, and `timestamp`

## Allowed Status Transitions

- `created -> ready | failed`
- `ready -> assigned | blocked | failed`
- `assigned -> in_progress | blocked | failed`
- `in_progress -> waiting_input | done | failed | blocked`
- `waiting_input -> ready | failed`
- `blocked -> ready | failed`
- terminal: `done`, `failed`

## Parent/Child Pattern

- For delegated work, Agent Manager creates:
  - one parent task (`owner=agent_manager`)
  - one developer child task (`owner=developer_agent`)
  - one tester child task (`owner=tester_agent`, depends on developer child)

## Dependency Enforcement

- Tester task checks dependency statuses before running.
- If developer task is not `done`, tester becomes `blocked`.
- If developer task is `failed`, tester remains `blocked` with dependency-failed reason.

## Waiting Input Loop

- Developer task moves to `waiting_input` when `feature_scope` is missing.
- Parent task also moves to `waiting_input`.
- Follow-up user input is linked to the existing parent task.
- Developer transitions back to `ready` and resumes execution.
