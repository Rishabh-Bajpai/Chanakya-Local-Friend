# RPD: Chanakya MVP Feasibility Validation Results

## Scope

This document captures the implementation evidence for the MVP defined in `PRD.md`.

## Tested Capabilities vs MAF-Oriented Design

- Single assistant entry point (`ChanakyaPA`) with route classification (`direct`, `tool`, `manager`).
- Delegation layer (`AgentManager`) that creates parent/child task structure.
- Specialized execution agents (`DeveloperAgent`, `TesterAgent`).
- Dependency-aware execution (tester depends on developer completion).
- Persistent task and transition storage (SQLite task store).
- Input-loop pause/resume (`waiting_input` and follow-up linkage to original task).
- Final aggregation and user-facing completion/failure messages through Chanakya.

## What Worked Well

- The routing and orchestration boundaries are clean and easy to reason about.
- The task state machine and transition log provide strong inspectability.
- Parent/child task decomposition with explicit dependencies maps naturally to manager-led orchestration.
- Pausing for missing input and resuming against the same task ID is straightforward.

## What Required Custom Work

- Request classification and orchestration policy are implemented as explicit application logic.
- Dependency checks and blocked-state semantics are implemented in manager/agent logic.
- Transition persistence and workflow observability are implemented in a custom task store/logger.

## What Feels Awkward or Risky

- Endpoint/model integration for LLM routing is currently heuristic-first and only environment-aware.
- Real MAF SDK runtime wiring is not yet deeply exercised in this first cut; adapter-level integration should be expanded next.
- Advanced long-running orchestration (scheduling, heartbeats, independent chat handoff) remains untested by design.

## Decision Summary

**Proceed with constraints.**

The core execution pattern is feasible and coherent for Chanakya’s MVP needs. However, before full-product commitment, the next phase should deepen native MAF runtime usage and validate higher-risk lifecycle features (durable worker lifetimes and scheduling behaviors).

## Highest-Risk Missing Features

1. Durable long-running worker behavior across process restarts.
2. Scheduling/heartbeat-style orchestration.
3. Independent conversational agent handoff while preserving parent-context integrity.
