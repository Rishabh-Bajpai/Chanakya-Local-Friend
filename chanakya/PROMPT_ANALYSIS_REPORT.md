# Prompt Handling Analysis Report

**Author:** Analysis Agent  
**Date:** 2026-04-05  
**Scope:** `chanakya/` module — prompt construction, injection, and execution flow

---

## Executive Summary

This report provides a deep analysis of how prompts are handled throughout the Chanakya application. The system uses a two-layer prompt architecture:

1. **Static Identity Prompts** — defined in `chanakya/seeds/agents.json`, loaded at runtime
2. **Dynamic Workflow Prompts** — constructed programmatically in `agent_manager.py` and `subagents.py`

Overall, the prompts are handled **correctly for the current architecture**, and this report has now been re-validated against the current code. Most concerns are valid, but a few were overstated or have already been partially mitigated (notably control-history contamination and stateless execution for control prompts).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prompt Sources and Loading](#2-prompt-sources-and-loading)
3. [Prompt Flow Through the System](#3-prompt-flow-through-the-system)
4. [Issues Identified](#4-issues-identified)
5. [Recommendations](#5-recommendations)
6. [Implementation Plan (What We Will Do)](#6-implementation-plan-what-we-will-do)
7. [Appendix: Affected Code Locations](#7-appendix-affected-code-locations)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          PROMPT SOURCES                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────┐      ┌──────────────────────────────────────┐ │
│  │  agents.json        │      │  agent_manager.py                    │ │
│  │  (static identity) │      │  (dynamic workflow prompts)          │ │
│  │                    │      │                                      │ │
│  │  - system_prompt   │      │  - _build_manager_route_prompt()     │ │
│  │  - personality    │      │  - _build_developer_stage_prompt()   │ │
│  │  - tool_ids        │      │  - _build_tester_handoff_prompt()    │ │
│  │  - role            │      │  - _build_worker_clarification_prompt│ │
│  └─────────────────────┘      │  - etc.                              │ │
│                               └──────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  subagents.py                                                      │ │
│  │  (temporary subagent prompts)                                      │ │
│  │                                                                    │ │
│  │  - build_subagent_planning_prompt()                               │ │
│  │  - build_subagent_decision_prompt()                               │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         PROMPT PROCESSING                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  load_agent_prompt()  ──►  inject_tools_into_prompt()  ──►  MAF Agent   │
│         │                            │                        instructions │
│         │                            │                                   │
│    (profile_files.py)          (agent/prompt.py)                     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXECUTION PATH                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Flask Request                                                         │
│       │                                                                │
│       ▼                                                                │
│  chat_service.py  ──►  AgentManager.run()  ──►  MAF Runtime           │
│                            │                                            │
│              ┌────────────┴────────────┐                               │
│              │                         │                               │
│              ▼                         ▼                               │
│      Direct Response          Delegated Workflow                      │
│      (MAFRuntime)              (_run_profile_prompt)                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Prompt Sources and Loading

### 2.1 Static Identity Prompts (`agents.json`)

**Location:** `chanakya/seeds/agents.json`

Each agent has a `system_prompt` field defining their identity:

```json
{
  "id": "agent_developer",
  "name": "Developer",
  "role": "developer",
  "system_prompt": "You are a software implementation worker. You implement only..."
}
```

**Loading flow:**

1. `seed.py` loads agents.json on app startup
2. `AgentProfileModel` records are created in SQLite
3. `runtime.py` → `load_agent_prompt()` retrieves the prompt
4. Optional: If `AGENT.md` / `SKILLS.md` exist in `chanakya_data/agents/{agent_id}/`, they override the JSON prompt

### 2.2 Tool-Augmented Prompts

**Location:** `chanakya/agent/prompt.py`

```python
def inject_tools_into_prompt(
    profile: AgentProfileModel,
    tools_cache: list[MCPStdioTool],
    *,
    base_prompt: str | None = None,
) -> str:
    base_prompt = str(base_prompt if base_prompt is not None else profile.system_prompt)
    if not tools_cache:
        return base_prompt

    extensions = ["\n\n# Available External Capabilities\n"]
    for tool in tools_cache:
        funcs = tool.functions
        for func in funcs:
            extensions.append(f"- Tool Name: `{func.name}`")
            if func.description:
                extensions.append(f"  Description: {func.description}")

    return base_prompt + "\n".join(extensions)
```

**Observation:** Tool descriptions are appended as a simple list without structured formatting. This works but could be improved.

### 2.3 Dynamic Workflow Prompts

**Location:** `chanakya/agent_manager.py`

19 prompt builder methods construct task-specific prompts:

| Method | Purpose |
|--------|---------|
| `_build_manager_route_prompt()` | Routing decision (cto vs informer) |
| `_build_cto_brief_prompt()` | Convert request to implementation brief |
| `_build_developer_stage_prompt()` | Developer task instructions |
| `_build_tester_stage_prompt()` | Tester task instructions |
| `_build_tester_handoff_prompt()` | Pass developer output to tester |
| `_build_worker_clarification_prompt()` | Determine if clarification needed |
| `_build_writer_handoff_prompt()` | Pass researcher output to writer |
| etc. | ... |

---

## 3. Prompt Flow Through the System

### 3.1 Direct Response Path

```
User Message
      │
      ▼
chat_service.py::handle_message()
      │
      ▼
MAFRuntime.run(session_id, text)
      │
      ▼
build_profile_agent(profile, session_factory, usage_text=text)
      │
      ├─► load_agent_prompt()        ──► profile.system_prompt (from agents.json)
      │                                  or AGENT.md if exists
      │
      ├─► inject_tools_into_prompt() ──► system_prompt + tool descriptions
      │
      ▼
MAF Agent(instructions=system_prompt, tools=cached_tools)
      │
      ▼
Agent.run(user_message=text)
```

### 3.2 Delegated Workflow Path

```
User Message
      │
      ▼
chat_service.py::handle_delegated_message()
      │
      ▼
AgentManager.run(message)
      │
      ├─► _select_route() ──► _build_manager_route_prompt() ──► _run_route_prompt()
      │                                                                   │
      │                                    Uses manager's system_prompt  │
      │                                                                   │
      ▼                                                                   │
RoutingDecision (cto vs informer)                                       │
      │                                                                   │
      ▼                                                                   │
_run_workflow() ──► _build_cto_brief_prompt() ──► _run_profile_prompt()  │
      │                                                                   │
      │                         Uses manager's system_prompt            │
      │                                                                   │
      ▼                                                                   │
Developer + Tester execution ──► _build_developer_stage_prompt()        │
                                  _build_tester_stage_prompt()          │
                                        │                                │
                                        ▼                                │
                             Worker agents execute with their           │
                             system_prompt from agents.json             │
```

### 3.3 History Integration

**Location:** `chanakya/history_provider.py`

The `SQLAlchemyHistoryProvider` class provides conversation history to MAF agents:

- Loads prior chat messages from SQLite
- Filters control history rows (JSON payloads used for routing decisions)
- Stores input/output messages after each run

```python
class SQLAlchemyHistoryProvider(BaseHistoryProvider):
    async def get_messages(self, session_id: str | None) -> list[Message]:
        # Load from SQLite...
        
    async def after_run(self, *, agent, session, context, state):
        # Store messages...
```

---

## 4. Issues Identified

Validation note: each issue below is now marked with a verification verdict based on direct code inspection in the current branch.

### 4.1 🔴 Critical: No Prompt Sanitization Between Agent Handoffs

**Validation verdict:** ✅ Confirmed

**Description:** When prompts are passed between agents (e.g., developer output → tester handoff), the content is concatenated directly without sanitization. If a worker agent's output contains text that could be interpreted as instructions, it could cause the next agent to misbehave.

**Affected code:**
- `agent_manager.py:1413-1417` — `_build_tester_handoff_prompt()`
- `agent_manager.py:1479-1481` — `_build_writer_handoff_prompt()`

**Example:**
```python
# Current code concatenates raw output directly
f"Developer handoff: {developer_output}"
```

**Risk:** Prompt injection from agent outputs.

---

### 4.2 🟠 High: Forced Subagent Prompts Include Raw Context Inline

**Validation verdict:** ✅ Confirmed

**Description:** When `CHANAKYA_FORCE_SUBAGENTS=true`, the system creates helper prompts that concatenate the parent's full execution prompt without clear boundaries.

**Affected code:**
- `agent_manager.py:2174-2177` — `_build_default_forced_helper()`
```python
instructions=(
    "You are a temporary implementation scout. ...\n\n"
    f"Parent worker prompt: {effective_prompt}"
)
```

**Risk:** The helper may follow instructions from the parent's prompt instead of its own role.

---

### 4.3 🟠 High: Double Execution in Recovery Paths

**Validation verdict:** ⚠️ Partially Confirmed (priority should be reduced)

**Description:** Recovery paths sometimes try multiple execution strategies (specialist_runner then _run_profile_prompt), which can lead to inconsistent results and unnecessary API calls.

**Affected code:**
- `agent_manager.py:1866-1880` — Writer recovery
- `agent_manager.py:1900-1907` — Tester recovery

```python
# Writer recovery tries twice
if self.specialist_runner is not None:
    candidate = str(self.specialist_runner(writer_profile, handoff_prompt, "recovery"))
if not recovered:
    recovered = self._run_profile_prompt(writer_profile, handoff_prompt).strip()
```

**Risk:** Potential extra calls and output variance in some paths, but not uniformly across production flow.

**Validation detail:**
- Writer recovery can run multiple attempts (`specialist_runner` candidate path, then primary run, then repair run if invalid).
- Tester recovery uses a primary run plus one repair run when needed (normal fallback behavior, not necessarily a defect).
- The strongest "double execution" concern appears mainly when `specialist_runner` is configured (commonly in tests or advanced runtime wiring), so this should be treated as **targeted medium priority**, not blanket high severity.

---

### 4.4 🟡 Medium: Manual String Construction for Prompts

**Validation verdict:** ✅ Confirmed

**Description:** All workflow prompts are built using f-string concatenation. This is fragile — changes to wording can affect model behavior unexpectedly, and there's no validation that all required variables are present.

**Affected code:**
- All `_build_*_prompt()` methods in `agent_manager.py`

**Example of current pattern:**
```python
def _build_developer_stage_prompt(self, message: str, implementation_brief: str) -> str:
    return (
        "Research and implement the software change described below. ...\n\n"
        f"Original request: {message}\n\n"
        f"Implementation brief: {implementation_brief}"
    )
```

**Risk:** Hard to maintain, test, or version-control prompt behavior.

---

### 4.5 🟡 Medium: No Schema Validation for Prompt Outputs

**Validation verdict:** ✅ Confirmed

**Description:** JSON outputs from prompts (routing decisions, subagent plans, clarification requests) are parsed with relaxed JSON parsing that falls back to regex extraction. Invalid outputs trigger repair prompts rather than being caught early.

**Affected code:**
- `agent_manager.py:1308-1315` — Route repair flow
- `agent_manager.py:1816-1838` — `_parse_json_object_relaxed()`

---

### 4.6 🟢 Low: Inconsistent Prompt Formatting

**Validation verdict:** ✅ Confirmed

**Description:** Different prompts use different formatting conventions:

- Some use `\n\n` for section separation, others use single `\n`
- Some include example JSON schemas, others don't
- Some specify "Return only JSON", others don't

**Affected code:**
- Compare `_build_manager_route_prompt()` with `_build_developer_stage_prompt()`

---

### 4.7 🟢 Low: Hardcoded JSON Schema Examples in Prompts

**Validation verdict:** ✅ Confirmed

**Description:** Example JSON schemas are hardcoded in prompt text. If the schema changes, prompts must be manually updated.

**Affected code:**
- `agent_manager.py:1333` — `{"selected_agent_id":"agent_cto",...}`
- `agent_manager.py:1330` — Format examples in routing prompt

---

### 4.8 🟢 Low: Route Repair Creates Recursive Prompts

**Validation verdict:** ✅ Confirmed

**Description:** When routing fails, the repair prompt is concatenated to the original prompt, making it longer and potentially causing the model to get confused.

**Affected code:**
- `agent_manager.py:1312`
```python
repaired = self._run_route_prompt(f"{prompt}\n\n{repair_prompt}")
```

---

### 4.9 🔵 Info: Unused Profile File Feature

**Validation verdict:** ✅ Confirmed (informational)

**Description:** The `load_agent_prompt()` function can load prompts from `AGENT.md` / `SKILLS.md` files in `chanakya_data/agents/{agent_id}/`, but this feature appears to be optional fallback — the system works with JSON prompts alone.

---

### 4.10 🔵 Info: Test Runners Bypass Normal Prompt Flow

**Validation verdict:** ✅ Confirmed (intentional for unit tests)

**Description:** In tests, `route_runner`, `summary_runner`, etc. are set as lambdas that bypass `_run_profile_prompt`, making it harder to test the full prompt construction flow.

**Affected code:**
- `chanakya/test/test_agent_manager.py` — Multiple test files

---

### 4.11 Already Mitigated Since Initial Failures (Important Context)

These mitigations are already present in current code and should be considered when prioritizing new prompt work:

1. **Control-prompt runs are stateless in key paths**
   - Route, subagent-decision, and subagent-plan prompts are executed with history disabled and storage off in relevant control flows.
2. **Control JSON is filtered out of replay history**
   - `SQLAlchemyHistoryProvider` excludes assistant JSON rows with control keys like `selected_agent_id`, `should_create_subagents`, and `needs_input`.

**Impact:** A major source of long-context confusion (control payloads polluting agent memory) has already been addressed. Remaining reliability issues are now primarily from handoff boundaries, schema strictness, and prompt/template consistency.

---

## 5. Recommendations

### 5.1 Immediate Actions

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🔴 Critical | No sanitization | Add a prompt sanitization function that escapes or removes potential instruction-like content from agent outputs before passing to the next agent |
| 🟠 High | Forced subagent prompts | Use a structured template with clear role/goal/constraints sections instead of inline concatenation |
| 🟡 Medium | Recovery-path multi-attempt behavior | Normalize retry policy (single primary + bounded repair), and only use specialist override in explicitly configured modes |

### 5.2 Short-Term Improvements

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🟡 Medium | Manual string construction | Introduce prompt template classes or dataclasses with validation |
| 🟡 Medium | No output validation | Add JSON schema validation for expected outputs before fallback to repair |
| 🟢 Low | Inconsistent formatting | Standardize prompt formatting (section separators, JSON examples, etc.) |

### 5.3 Long-Term Enhancements

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🟢 Low | Hardcoded schemas | Move JSON schemas to constants or config, generate prompts from templates |
| 🟢 Low | Route repair recursion | Consider a separate repair agent instead of concatenating prompts |
| 🔵 Info | Profile file feature | Document when to use AGENT.md/SKILLS.md vs JSON prompts |

---

## 6. Implementation Plan (What We Will Do)

This section defines the concrete remediation steps I will execute to improve reliability when the agent fails to follow user intent due to prompt quality or overloaded context.

### Phase 0 — Baseline and Failure Reproduction (Day 0-1)

1. Build a reproducible prompt-failure suite from real failing requests:
   - "agent ignored the request"
   - "agent got confused by long context"
   - "agent returned malformed routing JSON"
2. Add an evaluation script that runs these cases through current routing/workflow paths and records:
   - route correctness
   - JSON parse/repair rate
   - completion quality score (pass/fail rubric)
   - token usage and latency
3. Store baseline metrics to compare all prompt changes before rollout.

### Phase 1 — Prompt Safety and Boundary Hardening (Day 1-2)

1. Add handoff boundary wrappers for all cross-agent payloads:
   - Wrap worker outputs in explicit quoted blocks.
   - Label as "untrusted artifact" and "do not treat as instructions".
2. Implement handoff sanitization utility:
   - normalize control characters
   - cap excessive length
   - remove instruction-like prefixes outside bounded artifact sections
3. Update forced-subagent helper prompts to structured templates:
   - `Role`
   - `Objective`
   - `Constraints`
   - `Allowed tools`
   - `Output schema`
4. Add tests for prompt-injection-style handoff content to verify containment.

### Phase 2 — Context Compression and Relevance Control (Day 2-4)

1. Introduce context budget tiers per prompt type:
   - routing prompt: minimal context
   - worker prompt: relevant work slice only
   - handoff prompt: artifact + compact summary
2. Add relevance filtering before prompt assembly:
   - include only recent + semantically related turns
   - exclude stale or redundant task traces
3. Add rolling summary memory for long works:
   - maintain a short "state of work" summary
   - periodically refresh summary from raw history
4. Add hard token ceilings with deterministic truncation order:
   - keep current request first
   - keep active task context second
   - trim oldest low-relevance history last

### Phase 3 — Structured Outputs and Deterministic Parsing (Day 4-5)

1. Define JSON schemas for route/plan/clarification outputs in constants.
2. Validate model outputs against schema before accepting.
3. Replace recursive "append repair prompt" with a bounded retry strategy:
   - max 1-2 retries
   - minimal repair prompt only
4. Emit explicit error events when schema validation fails to improve observability.

### Phase 4 — Prompt Refactor and Consistency (Day 5-7)

1. Migrate `_build_*_prompt()` methods from freeform f-strings to typed templates.
2. Standardize all workflow prompts to one format:
   - context
   - objective
   - constraints
   - output format
   - refusal/clarification rule
3. Move schema examples and reusable prompt fragments to centralized constants.
4. Add unit tests that snapshot prompt templates to detect accidental regressions.

### Rollout and Guardrails

1. Roll out behind feature flags:
   - `CHANAKYA_PROMPT_HARDENING_V1`
   - `CHANAKYA_CONTEXT_COMPRESSION_V1`
   - `CHANAKYA_SCHEMA_ENFORCEMENT_V1`
2. Enable in staging first, then canary production traffic.
3. Compare against baseline from Phase 0 and promote only if all acceptance criteria pass.

### Acceptance Criteria

- 40%+ reduction in route-repair attempts.
- 50%+ reduction in malformed JSON handling incidents.
- No increase in user-visible wrong-route failures on benchmark cases.
- 20%+ reduction in average prompt token size on long-running works.
- Recovery path executes at most one fallback strategy per failure class.

### Immediate First PRs

1. **PR-1 (Safety):** handoff sanitization + untrusted artifact boundaries + tests.
2. **PR-2 (Context):** context budget + relevance filter + rolling summary memory.
3. **PR-3 (Schema):** schema constants + validator + bounded repair retries.
4. **PR-4 (Refactor):** standardized prompt templates + snapshot tests.

---

## 7. Appendix: Affected Code Locations

### Core Files

| File | Purpose | Issues |
|------|---------|--------|
| `chanakya/seeds/agents.json` | Static identity prompts | 4.7 |
| `chanakya/agent_manager.py` | Dynamic workflow prompts | 4.1, 4.2, 4.3, 4.4, 4.5, 4.8 |
| `chanakya/subagents.py` | Subagent prompts | 4.2 |
| `chanakya/agent/prompt.py` | Tool injection | 4.5 |
| `chanakya/agent/runtime.py` | Agent construction | 4.9 |
| `chanakya/agent/profile_files.py` | Profile file loading | 4.9 |
| `chanakya/history_provider.py` | History integration | — |
| `chanakya/chat_service.py` | Request routing | — |

### Key Method Locations

| Method | Line | Issue |
|--------|------|-------|
| `_build_manager_route_prompt` | 1324 | 4.4, 4.6 |
| `_build_developer_stage_prompt` | 1393 | 4.4, 4.6 |
| `_build_tester_handoff_prompt` | 1407 | 4.1, 4.4 |
| `_build_writer_handoff_prompt` | 1475 | 4.1, 4.4 |
| `_build_worker_clarification_prompt` | 1785 | 4.4 |
| `_build_default_forced_helper` | 2163 | 4.2 |
| `_run_profile_prompt` | 1914 | 4.3 |
| `_parse_json_object_relaxed` | 1816 | 4.5 |

---

## Conclusion

The prompt handling architecture is fundamentally sound — it separates identity prompts from task prompts and correctly passes them to MAF agents. After validation, the highest-impact unresolved risks are 4.1 and 4.2 (handoff safety and forced-helper prompt boundaries), followed by schema strictness and template consistency.

Key risks still causing user-visible misses include:

1. **Prompt injection** from agent outputs
2. **Unexpected agent behavior** from conflicting instructions
3. **Malformed or weakly-validated control outputs** that trigger brittle repair behavior

Addressing the validated priorities in the implementation plan should significantly improve reliability, reduce confusion on long tasks, and improve instruction-following consistency.
