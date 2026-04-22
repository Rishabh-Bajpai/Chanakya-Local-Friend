Classic Chat Artifact Redesign Tasks

- [X] Save redesign plan and problem statement for reference
- [X] Extend `ArtifactModel` with first-class metadata: `title`, `summary`, `latest_request_id`, `supersedes_artifact_id`
- [X] Extend artifact repository methods for explicit create and update flows
- [X] Add a first-class MCP artifact tools server modeled after work tools
- [X] Register `mcp_artifact_tools` in MCP config and make it available to Chanakya
- [X] Update Chanakya prompt and seed policy for direct-first, ask-before-artifact, rare-work behavior
- [X] Keep `mcp_filesystem` available but instruct Chanakya to prefer artifact tools for user-facing deliverables
- [X] Refactor classic chat reply assembly to use explicit artifact records created during the request
- [X] Remove workspace-scan artifact collection heuristics from `ChatService`
- [X] Remove response-text artifact materialization heuristics from `ChatService`
- [X] Remove followup artifact-forcing LLM calls from `ChatService`
- [X] Preserve or improve artifact API serialization with richer metadata
- [X] Update frontend rendering only if needed for richer artifact metadata or clearer messaging (no additional frontend change was required for this backend-first pass)
- [X] Add tests for explicit artifact creation in classic chat
- [X] Add tests for artifact update flows
- [X] Add tests proving classic chat still does not auto-create work
- [X] Add tests proving work-scoped artifacts still retain `work_id`
- [X] Run targeted tests and fix regressions
- [X] Keep this checklist updated as implementation progresses

## Follow-up Fixes

- [X] Store all artifacts under a single global artifact root with per-artifact folders
- [X] Stop creating blank per-request classic chat folders just for prompt context
- [X] Make artifact tools return constructive recovery hints and candidate artifact lists on bad IDs
- [X] Add artifact locate and delete tools for classic chat follow-ups
- [X] Make live chat artifact links render consistently across immediate and queued messages
- [X] Apply conversation layer to classic tool-assisted replies
- [X] Add regression coverage for helpful artifact-tool failures and global artifact storage
- [X] Re-run focused artifact and classic-chat regression tests

## Extended MCP Feedback

- [X] Add constructive recovery feedback for work tools on bad work IDs and missing required fields
- [X] Add constructive recovery feedback for filesystem tools on bad paths or workspace scope errors
- [X] Add constructive recovery hints for sandbox execution workspace failures and timeouts
- [X] Add regression tests covering helpful failure payloads across custom MCP servers
