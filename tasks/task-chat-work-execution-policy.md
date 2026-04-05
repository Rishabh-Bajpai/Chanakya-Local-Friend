# Chat And Work Execution Policy Plan

**Date:** 2026-04-05
**Status:** In Progress
**Owner:** OpenCode

## Goal

Redefine execution policy so normal chat optimizes for speed and direct completion, while `/work` optimizes for deliberate multi-agent accuracy.

## Agreed Behavior

### Normal Chat

1. Chanakya should complete as many short, low-latency tasks as possible itself.
2. Chanakya should delegate only when the task is clearly complex, long-running, multi-step, or specialist-heavy.
3. When delegation happens, the user should see and keep a stored assistant message indicating the task is being transferred to an expert.
4. Agent Manager should remain orchestration-first, but may solve directly only when no suitable persistent specialist/worker exists.
5. CTO and Informer should remain supervision-first and only solve directly under the same fallback condition.

### /work Mode

1. Chanakya should still handle trivial requests directly.
2. For most non-trivial work, Chanakya should prefer delegation earlier than in normal chat.
3. Agent Manager, CTO, and Informer should keep their current strict orchestration-heavy behavior.
4. `/work` prioritizes accuracy and completeness over speed.

## Implementation Phases

### Phase 1 - Mode-Aware Chanakya Routing

- [x] Add explicit execution policy split between normal chat and `/work`.
- [x] Expand direct handling in normal chat for low-latency tasks.
- [x] Keep `/work` delegation threshold stricter for non-trivial tasks.

### Phase 2 - Visible Delegation Notice

- [x] Persist an assistant message before delegated execution in normal chat.
- [x] Keep the delegation notice visible in chat history and work with refresh.

### Phase 3 - Manager Fallback Logic

- [x] Keep manager strict by default.
- [x] Add direct fallback only when required specialist/worker coverage is unavailable.
- [x] Log why fallback was used.

### Phase 4 - Specialist Fallback Logic

- [x] Keep CTO and Informer supervision-first.
- [ ] Add direct fallback only when downstream worker coverage is unavailable.

### Phase 5 - Prompt Updates

- [ ] Introduce distinct Chanakya guidance for normal chat vs `/work` at runtime.
- [x] Add fallback clause to manager prompt.
- [x] Add minimal fallback clause to CTO and Informer prompts.

### Phase 6 - Tests And Observability

- [x] Add tests for direct-vs-delegate behavior in both normal chat and `/work`.
- [x] Add tests for stored delegation notice in normal chat.
- [x] Add task/event metadata for routing source and delegation reason.

## Progress Summary

Implemented:

1. Mode-aware routing split between normal chat and `/work`.
2. Normal chat delegation notice persisted as an assistant message.
3. Manager direct fallback when required specialist/worker coverage is unavailable.
4. Prompt updates for Chanakya, Agent Manager, CTO, and Informer.
5. Regression coverage for new routing and fallback behavior.

Remaining:

1. Add runtime-distinct Chanakya prompt variants for normal chat vs `/work` instead of relying mainly on routing policy plus shared seed prompt.
2. Add direct fallback execution for CTO and Informer when their downstream worker coverage is missing.

## First Slice

1. Save this plan.
2. Implement mode-aware Chanakya routing.
3. Add stored delegation notice for normal chat.
4. Add/adjust tests for the first slice.
