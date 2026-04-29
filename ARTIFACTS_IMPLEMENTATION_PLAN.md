## Artifact Delivery Plan

### Goal

Chanakya should keep conversational responses short while still exposing the exact generated deliverable as a downloadable artifact.

### Agreed scope

- Support both code artifacts and research/report artifacts.
- Do not create a lightweight work just for classic chat artifacts.
- For classic chat, tie artifacts to the classic chat request and session.
- Initial UX is download-only.

### Design

1. Persist artifact metadata in the database.
2. Store artifact files in the existing shared workspace.
3. For work-backed execution, reuse the existing `work_id` workspace.
4. For classic chat without a work, use a request-scoped workspace rooted at the request id.
5. Let responsible agents save deliverables into the workspace using filesystem tools.
6. Detect new or modified files created during a request and register them as artifacts.
7. Keep the conversation layer presentation-only; it must not hide artifact metadata.

### Backend changes

- Add an artifact model and store methods.
- Extend `ChatReply` with an `artifacts` field.
- Capture workspace changes during direct and delegated execution.
- Attach persisted artifacts to assistant message metadata and API responses.
- Add artifact list and download endpoints.
- Validate download paths against the workspace root before serving files.

### Agent changes

- Give `agent_chanakya`, `agent_researcher`, and `agent_writer` filesystem tool access.
- Update prompts so code/report tasks save the exact deliverable into the current workspace and return a concise summary in chat.

### UI changes

- Render artifact download links below assistant messages in classic chat.
- Preserve artifact links in session history by reading assistant message metadata.

### Testing

- Add persistence tests for artifacts.
- Add API tests for list and download behavior.
- Add chat flow tests to verify artifact metadata survives conversation-layer wrapping.
