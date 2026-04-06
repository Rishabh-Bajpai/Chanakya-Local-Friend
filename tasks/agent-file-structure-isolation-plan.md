# Agent File Structure and Isolation Implementation Plan

## Objective

Restructure agent runtime assets under `chanakya_data` so each agent has a dedicated folder with:

- `AGENT.md`
- `SKILLS.md`
- `heartbeat.md`

Also enforce that:

1. Every prompt sent to an agent uses that agent's `AGENT.md` content.
2. Relevant skills are selected from that agent's `SKILLS.md` based on task usage.
3. Each agent can read/write only its own folder and cannot access other agents' files.

## Target Directory Contract

Create per-agent folders under:

`chanakya_data/agents/<agent_id>/`

Example:

```text
chanakya_data/
  agents/
    agent_chanakya/
      AGENT.md
      SKILLS.md
      heartbeat.md
      access.json
    agent_manager/
      AGENT.md
      SKILLS.md
      heartbeat.md
      access.json
```

Notes:

- Standardize on uppercase `AGENT.md` and `SKILLS.md`.
- `access.json` is optional but recommended for explicit policy declaration.

## File Content Responsibilities

### AGENT.md

Must contain:

- Agent identity (`agent_id`, display name)
- Role/description
- Personality
- Core operating instructions
- Skills list (names only)

### SKILLS.md

Must contain:

- Skill name
- Skill description
- Trigger conditions or usage guidance
- Optional constraints (when not to use)

### heartbeat.md

Must contain:

- Current heartbeat/status notes
- Pending task signal
- Last updated timestamp

## Implementation Phases

### Phase 1: Define Schema and Parsers

1. Add `chanakya/agent/profile_files.py`.
2. Implement deterministic markdown parsing for `AGENT.md` and `SKILLS.md`.
3. Validate required sections and raise explicit errors for malformed files.
4. Return structured models:
   - `AgentFileProfile`
   - `AgentSkillDefinition`

Deliverable: file-based metadata loader with strong validation.

### Phase 2: Prompt Composition from Agent Files

1. Update prompt-building flow in runtime (currently based on persisted `system_prompt`).
2. Compose final prompt from:
   - Base instructions in agent's `AGENT.md`
   - Selected skill details from `SKILLS.md`
   - Existing tool capability injection
3. Ensure every agent invocation loads its own `AGENT.md`.

Deliverable: file-driven prompt assembly used by all agent runs.

### Phase 3: Skill Selection by Usage

1. Add a `SkillSelector` service to pick relevant skills per request.
2. Start with deterministic routing rules (workflow + keyword matching).
3. Include selected skills in composed prompt context.
4. Log selected skills in task events for observability.

Deliverable: relevant-skill inclusion based on runtime context.

### Phase 4: Enforce Agent File Isolation

1. Add centralized `FileAccessGuard` for all path operations.
2. Enforce policy:
   - Agent `<id>` allowed: `chanakya_data/agents/<id>/**`
   - Deny access to `chanakya_data/agents/<other_id>/**`
3. Integrate validation into path-resolution and file IO call sites.
4. Emit denied-access audit events.

Deliverable: application-level per-agent read/write isolation.

## Heartbeat Migration

1. Replace old shared heartbeat location (`chanakya_data/heartbeats/*.md`) with:
   - `chanakya_data/agents/<agent_id>/heartbeat.md`
2. Update heartbeat path resolver and file creation logic.
3. Keep temporary backward compatibility with warning logs.

Deliverable: heartbeat files fully colocated with agent folders.

## Data and Seed Migration

1. Add a migration utility to generate folder structure for existing agents.
2. Derive initial `AGENT.md` from existing profile metadata:
   - `role`, `personality`, current prompt summary
3. Generate baseline `SKILLS.md` from configured/default skill set.
4. Move or copy legacy heartbeat content into `heartbeat.md`.
5. Add idempotent behavior so reruns are safe.

Deliverable: existing environments upgraded to new structure.

## Backward Compatibility Strategy

During migration window:

1. Prefer file-based prompt data when agent files exist.
2. Fallback to persisted DB fields only when files are missing.
3. Emit warnings for fallback usage.
4. Remove fallback after migration completion and validation.

## Test Plan

### Unit Tests

- Parser validates required sections for `AGENT.md` and `SKILLS.md`.
- Parser rejects malformed markdown contract.
- Skill selector picks expected skills for representative inputs.
- File access guard allows own-folder access and denies cross-agent access.
- Heartbeat resolver accepts only agent-local `heartbeat.md` locations.

### Integration Tests

- Agent run uses prompt assembled from its own `AGENT.md`.
- Selected skills from `SKILLS.md` appear in runtime prompt context.
- Cross-agent read/write attempts fail and are logged.

### Regression Tests

- Existing manager routing behavior remains intact.
- Tool loading and tool injection continue to work.
- Seed loading and API profile operations remain stable.

## Rollout Plan

1. Implement parser, prompt assembly, and migration utility behind feature flag.
2. Migrate seed agents and validate local/test environments.
3. Enable isolation guard in enforce mode.
4. Remove legacy heartbeat/prompt fallback paths.

## Risks and Mitigations

- Risk: Prompt regressions due to malformed markdown.
  - Mitigation: strict parser validation + startup checks.
- Risk: Access guard bypass via unguarded file call sites.
  - Mitigation: centralize all path resolution through guard.
- Risk: Migration inconsistencies across environments.
  - Mitigation: idempotent migration + dry-run mode + test coverage.

## Suggested Initial Task Breakdown

1. Build `profile_files.py` parser + schema + tests.
2. Integrate prompt composition in runtime.
3. Implement skill selector and event logging.
4. Add `FileAccessGuard` and wire into file paths.
5. Migrate heartbeat handling and add migration script.
6. Add integration/regression tests and remove legacy fallbacks after validation.
