# Agent Hierarchy And Delegation Plan

## Goal

Refactor the current delegation model so that every user request is routed through a strict hierarchical chain:

1. `agent_chanakya` receives every user message.
2. `agent_chanakya` immediately delegates the request to `agent_manager`.
3. `agent_manager` intelligently routes the request to exactly one top-level specialist:
   - `agent_cto` for software-development-related work
   - `agent_informer` for all non-software tasks
4. Each top-level specialist then coordinates its own worker agents through ordered, dependency-aware workflows.

This plan also includes making delegated agents tool-capable, so routing is not only prompt-based but also operationally correct.

An explicit implementation requirement for this design is that prompting quality must be high and deliberate. The hierarchy should not rely on loose or generic prompts. It should use carefully designed, role-specific, structured prompts combined with deterministic MAF workflow orchestration so the system remains predictable, debuggable, and robust.

## Desired Hierarchy

### Top-Level Flow

```text
User -> agent_chanakya -> agent_manager -> {agent_cto | agent_informer} -> worker agents -> supervisor -> agent_manager -> agent_chanakya -> user
```

### Software Development Chain

```text
User
  -> agent_chanakya
  -> agent_manager
  -> agent_cto
  -> agent_developer
  -> agent_tester
  -> agent_cto
  -> agent_manager
  -> agent_chanakya
  -> final response
```

### Information / Writing Chain

```text
User
  -> agent_chanakya
  -> agent_manager
  -> agent_informer
  -> agent_researcher
  -> agent_writer
  -> agent_informer
  -> agent_manager
  -> agent_chanakya
  -> final response
```

## Current State Summary

The current implementation partially resembles the desired model but does not enforce it.

### What Exists Today

- `agent_chanakya` and `agent_manager` both exist as persisted profiles.
- `agent_manager` can delegate requests.
- Child tasks are persisted under a root task.
- The UI already renders nested task trees and displays dependencies.
- Task dependency storage already exists in the database and repository layer.

### What Is Missing Today

- `agent_chanakya` does not always delegate to `agent_manager`.
- `agent_manager` uses keyword-based routing instead of intelligent routing.
- `agent_manager` directly chooses `developer/tester` or `researcher/writer`.
- There is no explicit `CTO` agent.
- There is no explicit `Informer` agent.
- `developer -> tester` ordering is not enforced.
- `researcher -> writer` ordering is not enforced.
- Delegated workers do not currently use their saved tool configuration when run under manager workflows.

## New Agents To Introduce

## `agent_cto`

### Purpose

Top-level software-delivery supervisor used only by `agent_manager`.

### Responsibilities

- Interpret software-related user requests.
- Convert the request into an implementation and validation plan.
- Delegate implementation to `agent_developer`.
- Delegate validation to `agent_tester` only after development output is available.
- Review both outputs.
- Return a concise engineering summary to `agent_manager`.

### Proposed Description

`agent_cto` is a software delivery supervisor. It translates product or engineering requests into an implementation brief, coordinates development and validation through downstream agents, reviews the quality of their outputs, and returns a grounded engineering summary with status, scope, risks, and verification results.

## `agent_informer`

### Purpose

Top-level information and writing supervisor used only by `agent_manager`.

### Responsibilities

- Interpret non-software user requests.
- Decide the research and writing shape needed for the request.
- Delegate fact gathering to `agent_researcher`.
- Delegate structured response creation to `agent_writer` after research is complete.
- Review the final response for clarity, completeness, and grounding.
- Return a polished summary to `agent_manager`.

### Proposed Description

`agent_informer` is an information and communication supervisor. It handles research, fact-gathering, explanatory writing, and polished user-facing responses by coordinating research and writing agents in sequence and ensuring that the final answer is accurate, structured, and easy to understand.

## Updated Role Boundaries

### `agent_manager`

- Receives all delegated requests from `agent_chanakya`
- Chooses between `agent_cto` and `agent_informer`
- Does not directly delegate to `agent_developer`, `agent_tester`, `agent_researcher`, or `agent_writer`
- Produces the final aggregated response returned to the user

### `agent_cto`

- Used only for software-related tasks
- Supervises `agent_developer` and `agent_tester`
- Reviews implementation and testing outcomes before returning to `agent_manager`

### `agent_informer`

- Used only for non-software tasks
- Supervises `agent_researcher` and `agent_writer`
- Reviews researched and written output before returning to `agent_manager`

### `agent_developer`

- Implementation only
- Produces a structured handoff for testing
- Does not report directly to `agent_manager`
- Reports to `agent_cto`

### `agent_tester`

- Validation only
- Starts only after `agent_developer` completes
- Reports to `agent_cto`

### `agent_researcher`

- Fact gathering and structured notes only
- Produces a clean research handoff for `agent_writer`
- Reports to `agent_informer`

### `agent_writer`

- Converts research output into a polished user-facing answer
- Starts only after `agent_researcher` completes
- Reports to `agent_informer`

## Routing Strategy

## Principle

Replace the current keyword-marker delegation logic with a hybrid routing strategy:

- Primary path: intelligent prompt-based routing by `agent_manager`
- Backup path: lightweight deterministic fallback if prompt output is invalid or unavailable

The important constraint is that the intelligent prompt layer must not be the only control surface. Prompting should be used to make high-quality decisions, but the actual execution path must be enforced by deterministic MAF workflow composition. In other words, prompts choose within a constrained schema, while workflows enforce the legal sequence of delegation and completion.

## Manager Routing Contract

`agent_manager` should receive:

- the current user message
- optional recent chat context
- the list of top-level specialist agents
- each specialist's role and responsibility summary
- a strict structured output requirement

The manager prompt must be written as a narrow decision-making prompt, not a conversational one. It should:

- clearly enumerate only the allowed routing targets
- explicitly forbid direct problem solving
- require concise reasoning grounded in the request
- require a machine-parseable result
- be optimized for stable, repeatable outputs across similar requests

### Expected Structured Output

```json
{
  "selected_agent_id": "agent_cto",
  "selected_role": "cto",
  "reason": "The request is primarily about implementing and validating software changes.",
  "execution_mode": "software_delivery"
}
```

### Validation Rules

- `selected_agent_id` must be either `agent_cto` or `agent_informer`
- `selected_role` must match the selected agent
- `reason` must be non-empty
- `execution_mode` should be a normalized internal label

### Failure Handling

- First invalid response: retry once with a stricter repair prompt
- Second invalid response: fallback to deterministic classification

### Fallback Heuristic

- Route to `agent_cto` for requests strongly related to coding, implementation, debugging, building, testing, architecture, refactoring, or software delivery
- Route to `agent_informer` for everything else

## Workflow Strategy

## Why Current Group Chat Is Not Enough

The current `GroupChatBuilder`-based model is too loose for the desired supervisor chain. It allows multi-agent discussion, but it does not naturally encode strict order such as:

- developer must complete before tester starts
- researcher must complete before writer starts

## Recommended MAF Features

Use the following Agent Framework patterns:

- deterministic workflow orchestration as the default model
- `SequentialBuilder` for dependent phase execution
- `Agent.run(...)` only inside tightly-scoped, structured decision/review steps
- optionally retain `GroupChatBuilder` for future brainstorming or comparison workflows

### Rationale

`SequentialBuilder` is a better match for deterministic chains where one agent's output becomes the next agent's input.

This should be treated as a core architectural choice, not an implementation detail. The intended behavior is:

- routing is deterministic after the manager emits a valid route decision
- worker ordering is deterministic and encoded by workflow construction
- illegal execution orders are impossible by design
- prompt intelligence improves decision quality, while workflow structure preserves control and predictability

## Deterministic Workflow Requirement

The full hierarchy should be implemented through explicit workflow definitions, not through ad hoc nested `if/else` logic alone.

### Required Design Rule

For each supported request category, there must be a deterministic workflow path with fixed legal transitions.

Examples:

- `agent_manager -> agent_cto -> agent_developer -> agent_tester -> agent_cto -> agent_manager`
- `agent_manager -> agent_informer -> agent_researcher -> agent_writer -> agent_informer -> agent_manager`

### Implications

- `agent_manager` should not directly invoke worker agents outside the selected workflow
- `agent_cto` should not skip `agent_developer` and jump directly to `agent_tester`
- `agent_informer` should not skip `agent_researcher` and jump directly to `agent_writer`
- the workflow definition itself should encode the permitted sequence

### Preferred MAF Usage

- use `SequentialBuilder` for the `developer -> tester` and `researcher -> writer` chains
- use structured supervisor review steps before returning to the parent supervisor
- use deterministic orchestration wrappers around route selection so the overall flow is inspectable and reproducible

### Non-Goal

Do not rely on free-form multi-agent conversations for core routing or dependency handling. Free-form collaboration can be added later for special cases, but the default production path should be deterministic.

## Detailed Software Workflow

### Desired Flow

1. `agent_manager` selects `agent_cto`
2. `agent_cto` reframes the user request into an implementation brief
3. `agent_developer` receives the implementation brief and produces:
   - implementation summary
   - assumptions
   - risks
   - testing focus areas
4. `agent_tester` receives the developer handoff and produces:
   - validation summary
   - checks performed
   - defects or risks
   - pass/fail recommendation
5. `agent_cto` reviews both outputs and returns a final engineering summary to `agent_manager`
6. `agent_manager` returns the final user-facing response

### Required Dependency

`agent_tester` must not run until `agent_developer` has completed.

### CTO Review Responsibilities

- verify implementation and validation coherence
- surface unresolved risks
- report the outcome in a concise engineering summary

## Detailed Informer Workflow

### Desired Flow

1. `agent_manager` selects `agent_informer`
2. `agent_informer` reframes the request into a research and writing brief
3. `agent_researcher` gathers facts, references, and structured notes
4. `agent_writer` receives the research handoff and produces a polished response
5. `agent_informer` reviews the response and returns a final summary to `agent_manager`
6. `agent_manager` returns the final user-facing response

### Required Dependency

`agent_writer` must not run until `agent_researcher` has completed.

### Informer Review Responsibilities

- verify grounding and relevance
- preserve uncertainty when facts are incomplete
- ensure the response is clear and well-structured

## Prompting Strategy

## General Principles

- Use explicit role boundaries in every system prompt
- Prevent supervisors from doing worker work directly unless recovery is needed
- Require structured handoffs between dependent agents
- Ask each supervisor to review downstream outputs before escalation upward

Prompting quality is a first-class requirement in this plan. Every prompt used in routing, delegation, handoff, review, and summarization should be intentionally designed for:

- high signal and low ambiguity
- constrained output shape
- stable behavior across similar requests
- clear separation of authority between agents
- minimal opportunity for role drift

Prompts should be treated as part of the workflow contract, not as incidental strings.

## Prompt Engineering Standards

Each major prompt should contain, where applicable:

- role and authority boundary
- goal of the current step
- allowed inputs
- required output schema or format
- explicit non-goals
- escalation target
- quality bar for the response

### Structured Output Preference

For routing, handoff, review, and aggregation steps, prefer structured outputs over free-form prose.

Examples of structured outputs to require:

- route decisions
- implementation handoffs
- testing reports
- research handoffs
- supervisor review summaries

### Prompt Review Requirement

Before implementation is considered complete, prompts for `agent_manager`, `agent_cto`, `agent_informer`, `agent_developer`, `agent_tester`, `agent_researcher`, and `agent_writer` should be reviewed for:

- overlap in responsibility
- ambiguity in escalation behavior
- missing structured output instructions
- missing quality constraints
- opportunities for hallucinated authority or task skipping

## Manager Prompt Requirements

`agent_manager` prompt should emphasize:

- you are a routing and coordination supervisor
- you must choose exactly one top-level specialist: `agent_cto` or `agent_informer`
- do not solve the request directly
- return only the requested structured routing output

## CTO Prompt Requirements

`agent_cto` prompt should emphasize:

- software-delivery ownership
- delegation to `agent_developer` first
- delegation to `agent_tester` after implementation is complete
- review of both outputs
- concise engineering summary with scope, outcome, tests, and risks

It should also explicitly forbid bypassing the required sequence except in controlled failure reporting paths.

## Developer Prompt Requirements

`agent_developer` prompt should emphasize:

- implementation only
- no premature testing conclusion
- produce a structured handoff for testing
- clearly state assumptions, unknowns, and areas needing verification

## Tester Prompt Requirements

`agent_tester` prompt should emphasize:

- validation only
- evaluate the developer output and resulting implementation state
- report pass/fail with evidence and residual risks
- recommend next action to `agent_cto`

## Informer Prompt Requirements

`agent_informer` prompt should emphasize:

- non-software task supervision
- routing research to `agent_researcher`
- routing polished response generation to `agent_writer`
- review of final wording before escalation to `agent_manager`

It should also explicitly forbid generating the final polished answer without the research handoff unless the workflow enters a defined failure-recovery path.

## Researcher Prompt Requirements

`agent_researcher` prompt should emphasize:

- fact gathering and structured notes
- clarity on uncertainty and source quality
- no final user-facing prose unless explicitly required
- produce a writer handoff package

## Writer Prompt Requirements

`agent_writer` prompt should emphasize:

- convert research handoff into polished output
- preserve nuance and caveats
- avoid inventing unsupported claims
- optimize for readability and structure

## Task Graph And Persistence Model

## Existing Support

The current task model already supports:

- parent-child hierarchy
- dependency lists
- task ownership
- lifecycle status tracking
- event logging

This should be reused rather than replaced.

## Proposed Task Shape For Software Requests

1. Root request task owned by `agent_chanakya`
2. Manager orchestration task owned by `agent_manager`
3. CTO supervision task owned by `agent_cto`
4. Developer execution task owned by `agent_developer`
5. Tester execution task owned by `agent_tester`

### Dependency Rule

- tester task depends on developer task

### Optional Extension

If useful for observability, the final CTO review can be persisted as a separate child task under the CTO supervision task instead of only being captured in task result JSON.

## Proposed Task Shape For Informer Requests

1. Root request task owned by `agent_chanakya`
2. Manager orchestration task owned by `agent_manager`
3. Informer supervision task owned by `agent_informer`
4. Researcher execution task owned by `agent_researcher`
5. Writer execution task owned by `agent_writer`

### Dependency Rule

- writer task depends on researcher task

## Recommended Task Status Usage

- supervisor tasks: `in_progress` while coordinating
- downstream tasks waiting on prerequisites: `ready` or `blocked`
- active worker task: `in_progress`
- successful completion: `done`
- failure: `failed`

## Tool Capability Requirement

## Problem

Delegated worker agents are currently instantiated in a way that ignores their saved tool configuration. This means worker profiles may have `tool_ids`, but those tools are not actually attached when those workers are run inside manager-driven workflows.

## Requirement

Make delegated agents tool-capable in the same implementation change.

## Expected Outcome

- `agent_researcher` can use research-oriented tools such as web/fetch tools
- future delegated agents can use their configured tools without special-casing
- delegated runtime behavior becomes consistent with direct `MAFRuntime` behavior

## Recommended Implementation Direction

Introduce a reusable delegated runtime path that respects `AgentProfileModel.tool_ids_json` and reuses the same tool loading and prompt injection logic already present in the direct runtime layer.

### Design Principles

- avoid bare `Agent(...)` construction for delegated workers when tools are needed
- centralize profile-to-agent construction
- keep tool selection tied to persisted agent configuration
- preserve traceability for delegated runs where possible

## Error Handling And Recovery

## Routing Failures

- invalid manager routing output should trigger one structured retry
- repeated invalid output should fall back safely to deterministic classification

## Worker Failures In Software Flow

- if developer fails, tester should not run
- CTO should summarize failure and return it to manager
- manager should return a grounded failure summary to the user

- if tester fails, CTO should report:
  - implementation status
  - validation failure
  - suggested next step

## Worker Failures In Informer Flow

- if researcher fails, writer should not run
- Informer should summarize the failure and return it to manager

- if writer fails after research succeeds, Informer should report:
  - research completion status
  - writing failure
  - suggested next step

## Files Likely To Change

- `chanakya/seeds/agents.json`
- `chanakya/chat_service.py`
- `chanakya/agent_manager.py`
- `chanakya/agent/runtime.py` or a new delegated runtime helper module
- `chanakya/test/test_agent_manager.py`
- potentially other focused tests for hierarchy and tool-enabled delegation

## Proposed Implementation Phases

## Phase 1

Add and document the new agent roles:

- `agent_cto`
- `agent_informer`

Update role descriptions and prompts for all related agents.

## Phase 2

Refactor chat entry so all user requests go through `agent_manager`.

## Phase 3

Replace keyword-based top-level routing with intelligent prompt-based route selection plus deterministic fallback.

This phase must also define the deterministic workflow envelope that consumes the route decision so the route result is not just advisory but operationally enforced.

## Phase 4

Implement the `CTO` software workflow with enforced `developer -> tester` sequencing and CTO review.

This phase should use explicit workflow composition rather than informal step ordering.

## Phase 5

Implement the `Informer` workflow with enforced `researcher -> writer` sequencing and Informer review.

This phase should also use explicit workflow composition rather than informal step ordering.

## Phase 6

Make delegated agents tool-capable via profile-aware runtime construction.

## Phase 7

Expand tests and validate the hierarchical task graph and task dependencies in the UI.

## Testing Plan

## Automated Tests

Add or update tests for:

1. every request being routed through `agent_manager`
2. `agent_manager` selecting `agent_cto` for software requests
3. `agent_manager` selecting `agent_informer` for non-software requests
4. invalid structured route output retry and fallback behavior
5. `agent_cto` workflow creating developer then tester tasks
6. tester task depending on developer task
7. `agent_informer` workflow creating researcher then writer tasks
8. writer task depending on researcher task
9. delegated workers using persisted agent profiles and tool IDs
10. final responses flowing back through supervisor hierarchy

## Manual Validation Scenarios

### Software

Input:

```text
Implement and test login rate limiting for the authentication service.
```

Expected chain:

```text
Chanakya -> Manager -> CTO -> Developer -> Tester -> CTO -> Manager
```

### Research / Writing

Input:

```text
Research the weather in Berlin and write a concise answer.
```

Expected chain:

```text
Chanakya -> Manager -> Informer -> Researcher -> Writer -> Informer -> Manager
```

### Essay / Informational Writing

Input:

```text
Write a short essay about solar energy.
```

Expected chain:

```text
Chanakya -> Manager -> Informer -> Researcher -> Writer -> Informer -> Manager
```

### Routing Failure Recovery

Simulate malformed route output from `agent_manager`.

Expected behavior:

- one repair attempt
- safe fallback selection
- no crash

## Open Decisions

These should be finalized before or during implementation:

1. Should manager and supervisor decisions be persisted as dedicated tasks, events, or both?
2. Should `CTO` and `Informer` use `SequentialBuilder` directly, or should the sequence be implemented manually with explicit `Agent.run(...)` calls for tighter persistence control?
3. Should delegated tool invocation traces be recorded with the same fidelity as direct runtime tool traces in the first implementation pass?
4. Should root task ownership remain with `agent_chanakya`, or should it be transferred to `agent_manager` once delegation begins?

## Recommended Initial Direction

For the first implementation pass:

- keep root task ownership with `agent_chanakya`
- create explicit manager and specialist child tasks for observability
- use high-quality, role-specific, structured prompts for all routing and delegation steps
- use prompt-based intelligent routing with strict JSON validation and fallback
- implement deterministic MAF workflow execution for all supported paths
- implement deterministic sequential worker execution
- make delegated workers tool-capable in the same change
- defer richer cross-agent conversation modes until the hierarchy is stable

## Implementation Success Criteria

The change is successful when all of the following are true:

- every request enters through `agent_chanakya` and is routed to `agent_manager`
- `agent_manager` only chooses between `agent_cto` and `agent_informer`
- software work always flows through `CTO -> Developer -> Tester -> CTO`
- non-software work always flows through `Informer -> Researcher -> Writer -> Informer`
- downstream worker dependencies are persisted and visible
- delegated agents respect their saved tool configuration
- routing and delegation are enforced by deterministic MAF workflows rather than loose keyword checks or free-form chat alone
- prompts are role-specific, structured, and strong enough to produce stable delegation decisions and handoffs
- final responses remain concise, grounded, and supervisor-reviewed
