# Issue 4: Replace Hard-Coded Conversation Orchestration with LLM-Powered Planning

## Problem

The conversation layer currently relies on several hard-coded orchestration components to decide how to route and shape responses. The main issue is not only `RuleBasedPolicyEngine` in `app/services/policy_engine.py`, but also downstream response planning components that encode fixed behavior in code.

Current hard-coded behavior includes:

- `app/services/policy_engine.py`
  Uses static dictionaries and keyword heuristics like `CLOSING_MESSAGES`, `AMBIGUOUS_MESSAGES`, `DISTRESS_PHRASES`, topic prefixes, and low-signal detection rules.
- `app/services/disclosure_planner.py`
  Hard-codes how undisclosed items are ordered, selected, and phrased for the user.
- `app/services/response_processor.py`
  Hard-codes policy-specific phrasing, filler text, support phrasing, and response chunking patterns.
- `app/services/critique_pass.py`
  Hard-codes revision logic for staged disclosure and preference prompts.

This creates several limitations:

1. **Hard-coded orchestration**: The conversation layer uses fixed branching rules and prewritten text to decide both what to do and how to say it.
2. **Limited understanding**: It cannot reliably interpret nuanced intent, ambiguity, emotional tone, or user preference beyond simple heuristics.
3. **Weak working memory usage**: It does not fully reason over working memory state when deciding policy, disclosure sequencing, and response framing.
4. **Rigid disclosure behavior**: `disclosure_planner` uses fixed templates instead of adapting disclosure strategy to context.
5. **Rigid response shaping**: `response_processor` and `critique_pass` hard-code phrasing and revision behavior that should ideally be context-sensitive.
6. **Poor extensibility**: Expanding the behavior requires code changes rather than prompt/configuration changes.

## Expected Behavior

The conversation orchestration layer should become LLM-powered so it can:
- Analyze the full conversation context and working memory state
- Make intelligent decisions about whether to answer directly, ask for clarification, disclose items, pause, or yield
- Decide how disclosure should be staged instead of relying on fixed `DisclosurePlanner` wording/ordering rules
- Generate response plans and phrasing in a context-sensitive way instead of relying on fixed `ResponseProcessor` templates
- Revise or critique unsafe/over-disclosing drafts using LLM reasoning rather than only hard-coded critique rules
- Properly process and utilize working memory signals such as preference signals, undisclosed items, dialogue phase, runtime state, and pending questions
- Provide reasoned explanations for decisions that can be inspected and audited

## Suggested Approach

1. Create an LLM-powered orchestration layer that can handle at least:
   - policy selection
   - disclosure sequencing/planning
   - response planning / response spec generation
   - critique or revision when needed
2. The LLM should receive:
   - current conversation history
   - working memory state (undisclosed items, preference signals, dialogue phase, pending questions, runtime state)
   - available policy acts and response-plan capabilities
   - any relevant agent/context metadata needed for safe orchestration
3. The LLM should return structured outputs for at least:
   - selected policy act
   - reasoning for the decision
   - response plan / response spec
   - disclosure choice or disclosure ordering when applicable
   - working memory updates
4. Keep the current rule-based components as a fallback for cases where LLM orchestration is unavailable or disabled
5. Prefer a design where orchestration components are separable but share a common LLM-backed strategy, rather than scattering fixed conversation text across multiple classes

## Sub-Issues / Implementation Parts

### Part 1: Replace Primary Hard-Coded Planning Components

This part covers the highest-value orchestration components that currently decide what to do and how to say it.

Scope:
- `app/services/policy_engine.py`
- `app/services/disclosure_planner.py`
- `app/services/response_processor.py`
- `app/services/critique_pass.py`

Goal:
- replace fixed branching rules and canned phrasing with LLM-backed planning
- produce structured outputs for policy selection, disclosure choice, response planning, and critique/revision
- keep deterministic fallbacks where needed

Expected result:
- the conversation layer stops relying on static keyword routing and hard-coded response templates for core orchestration

### Part 2: Replace or Hybridize Secondary Flow Managers

This part covers the more stateful control-flow helpers that are also currently heuristic-heavy, but may be better implemented as hybrid systems rather than fully free-form LLM logic.

Scope:
- `app/services/interruption_manager.py`
- `app/services/resume_manager.py`
- `app/services/preference_signals.py`

Goal:
- reduce hard-coded keyword and prefix matching for interruption detection, resume selection, and preference inference
- use LLM reasoning where interpretation is needed
- keep deterministic state transitions and storage where predictability matters

Expected result:
- interruption/resume/preference behavior becomes more context-aware without losing control of thread state and working-memory updates

Recommended implementation strategy:
1. finish Part 1 first
2. keep Part 2 hybrid unless there is strong evidence that fully LLM-driven flow control is reliable enough

## Acceptance Criteria

1. **LLM-powered orchestration exists**: A new LLM-backed orchestration path is created to replace hard-coded decision and response-planning behavior.

2. **Context-aware decisions**: The LLM receives and utilizes:
   - Full conversation history
   - Working memory state (undisclosed items, preference signals, dialogue phase, pending questions)
   - Available policy acts with their descriptions
   - Relevant runtime state for disclosure/resume behavior

3. **Structured output**: The LLM returns structured orchestration output including:
   - Selected policy act from `PolicyAct` enum
   - Reasoning for the decision
    - Response plan or response spec data
    - Disclosure item selection or sequencing data when relevant
   - Any working memory updates

4. **Disclosure planning is no longer purely hard-coded**: `DisclosurePlanner` behavior is replaced or driven by LLM output rather than fixed response templates and ordering heuristics alone.

5. **Response planning is no longer purely hard-coded**: `ResponseProcessor` policy phrasing, filler selection, and chunk planning are replaced or driven by LLM output where appropriate.

6. **Critique behavior is improved**: `critique_pass` uses LLM reasoning for staged disclosure / revision decisions, or is clearly integrated into the same orchestration flow.

7. **Graceful fallback**: When the LLM is unavailable or fails, the system falls back to current rule-based components without crashing.

8. **Maintains interface compatibility where practical**: New LLM-backed components integrate cleanly with `ConversationWrapper` and existing response structures.

9. **Configurable**: The orchestration path can be switched between LLM-powered and rule-based via configuration.

10. **Test coverage**: Tests verify that:
- the LLM-powered path makes reasonable decisions for varied conversation scenarios
- disclosure planning is context-sensitive and structured
- response planning output matches expected schema/contracts
- fallback works correctly when the LLM is unavailable

## Part-Specific Acceptance Criteria

### Part 1 Acceptance Criteria

1. `policy_engine`, `disclosure_planner`, `response_processor`, and `critique_pass` are no longer primarily driven by fixed text templates and keyword branching.
2. The LLM-backed path returns structured outputs that `ConversationWrapper` can consume safely.
3. Disclosure sequencing and response planning adapt to working memory and conversation context.
4. Fallback behavior exists for each replaced component.

### Part 2 Acceptance Criteria

1. `InterruptionManager`, `ResumeManager`, and `PreferenceSignalInferer` no longer rely only on token/prefix heuristics.
2. Interruption and resume handling use context-aware interpretation while preserving deterministic state restoration.
3. Preference inference becomes more robust than fixed token matching.
4. The resulting flow remains inspectable in the debug dashboard and does not make hidden state transitions.

## Related Files

- `app/services/policy_engine.py` - Current rule-based implementation
- `app/services/disclosure_planner.py` - Hard-coded disclosure planning
- `app/services/response_processor.py` - Hard-coded response shaping and filler text
- `app/services/critique_pass.py` - Hard-coded critique/revision rules
- `app/services/response_realizer.py` - Realization layer that may need to consume richer response plans
- `app/services/interruption_manager.py` - Hard-coded interruption and thread-restore heuristics
- `app/services/resume_manager.py` - Hard-coded resume branching and support flows
- `app/services/preference_signals.py` - Hard-coded preference token matching
- `app/services/working_memory.py` - Working memory data structures
- `app/schemas.py` - ChatRequest and related schemas

## Related Issues

- `issues/issue_1.md` - Split conversation layer into an independent stack
- `issues/issue_2.md` - Build a developer debug dashboard for full conversation and agent trace visibility
- `issues/issue_3.md` - Fix broken resume flow handoff to core agent
- `issues/issue_5.md` - Add a decorator-based integration API on top of the conversation wrapper
- `issues/issue_6.md` - Support MAF A2A connector for external coding agent servers
