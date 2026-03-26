# MAF Capability Matrix (US + FR)

This matrix maps each PRD item to implementation evidence and effort profile:

- `Native MAF`: direct framework fit
- `MAF + light glue`: MAF handles core behavior, app adds policy/state/logging glue
- `Custom-heavy`: mostly app-owned design with limited direct MAF primitives

## User Stories (US)

| ID     | Requirement                               | Fit              | Primary MAF Class/Pattern                 | Notes                                                   |
| ------ | ----------------------------------------- | ---------------- | ----------------------------------------- | ------------------------------------------------------- |
| US-001 | Single entry via Chanakya + route logging | MAF + light glue | `Agent`, `OpenAIChatClient`           | Routing policy and request IDs are app-owned.           |
| US-002 | Direct response without manager           | Native MAF       | `Agent.run(...)`                        | Direct path is clean and natural.                       |
| US-003 | Weather tool via PA, no manager           | Native MAF       | `@tool`, `Agent(tools=[...])`         | Strong fit; tool invocation is straightforward.         |
| US-004 | Delegate complex task to manager          | MAF + light glue | `WorkflowBuilder`, `@executor`        | Delegation contract/payload shaping is app logic.       |
| US-005 | Decompose into parent + subtasks          | MAF + light glue | `WorkflowBuilder` graph                 | Parent/child persistence model is custom store.         |
| US-006 | Assign subtasks to specialized agents     | MAF + light glue | executor-per-role pattern                 | Assignment records and ownership metadata are custom.   |
| US-007 | Dependency-aware execution                | MAF + light glue | workflow edges + executor checks          | Hard dependency semantics are app-defined.              |
| US-008 | Persisted task lifecycle tracking         | Custom-heavy     | app `TaskStore` + transition log        | MAF does not prescribe this exact task schema.          |
| US-009 | Pause for missing input and resume        | MAF + light glue | workflow output + resumed invocation      | Waiting-input linkage to request/task IDs is app-owned. |
| US-010 | Aggregate final result to Chanakya        | Native MAF       | terminal `@executor` + `yield_output` | Very natural workflow pattern.                          |
| US-011 | Suitability evaluation for scale          | MAF + light glue | MAF runtime + evidence docs               | Requires architectural interpretation beyond API use.   |

## Functional Requirements (FR)

| ID    | Requirement                                 | Fit              | Primary MAF Class/Pattern                 | Notes                                                        |
| ----- | ------------------------------------------- | ---------------- | ----------------------------------------- | ------------------------------------------------------------ |
| FR-1  | One user-facing assistant named Chanakya    | MAF + light glue | `Agent`                                 | Naming/entrypoint shell is app layer.                        |
| FR-2  | Route direct/tool/manager                   | MAF + light glue | `Agent`, `@tool`, `WorkflowBuilder` | Classifier logic is custom policy.                           |
| FR-3  | Record route decision                       | Custom-heavy     | app logger                                | MAF telemetry exists, but business route log is custom.      |
| FR-4  | Immediate/delegated/clarification responses | MAF + light glue | `Agent.run`, workflow output            | Clarification contract and phrasing are app-owned.           |
| FR-5  | Weather tool integration                    | Native MAF       | `@tool`                                 | Clean direct fit.                                            |
| FR-6  | Weather without manager                     | Native MAF       | `Agent` with tools                      | Works as independent tool-agent path.                        |
| FR-7  | Tool run logging schema                     | Custom-heavy     | app logger                                | Structured audit fields are custom.                          |
| FR-8  | Manager accepts structured payload          | MAF + light glue | workflow input message                    | Payload validation/versioning is app-owned.                  |
| FR-9  | Manager creates parent task                 | Custom-heavy     | app `TaskStore`                         | Domain task objects are custom.                              |
| FR-10 | Decompose into dev/test subtasks            | MAF + light glue | multi-executor workflow                   | Subtask persistence/IDs are custom.                          |
| FR-11 | Assign subtasks to agents                   | MAF + light glue | executor role mapping                     | Explicit assignment records are custom metadata.             |
| FR-12 | Aggregate subtask results                   | Native MAF       | terminal aggregation executor             | Strong fit with workflow outputs.                            |
| FR-13 | Include Developer agent                     | MAF + light glue | `@executor` role step                   | Could be `AgentExecutor` too; executor is simpler for MVP. |
| FR-14 | Include Tester agent                        | MAF + light glue | `@executor` role step                   | Same pattern as developer step.                              |
| FR-15 | Agents execute only when assigned           | MAF + light glue | workflow-controlled invocation            | Assignment checks are app constraints.                       |
| FR-16 | Agents report status/results to manager     | MAF + light glue | `WorkflowContext.send_message`          | Persisted status/result model is custom.                     |
| FR-17 | Parent-child task relationships             | Custom-heavy     | app `Task` schema                       | Implemented outside framework core.                          |
| FR-18 | Task fields (ID, owner, deps, result)       | Custom-heavy     | app `Task` schema                       | Entire domain model is app-owned.                            |
| FR-19 | Persistent inspectable task data            | Custom-heavy     | SQLite `TaskStore`                      | Explicitly custom persistence design.                        |
| FR-20 | Task state machine with transitions         | Custom-heavy     | app transition map                        | Could integrate checkpoints later, still app semantics.      |
| FR-21 | Dependency links between subtasks           | MAF + light glue | workflow edge + dependency metadata       | Link semantics in store are custom.                          |
| FR-22 | Tester waits until developer complete       | MAF + light glue | `@executor` dependency gate             | Enforced in tester step logic.                               |
| FR-23 | Downstream halt on dependency fail          | MAF + light glue | tester blocking branch                    | Halt semantics are app policy.                               |
| FR-24 | Mark blocked/waiting when unmet deps        | MAF + light glue | workflow + app state store                | Status vocabulary is app-owned.                              |
| FR-25 | Pause task waiting user input               | MAF + light glue | workflow output state                     | Pause semantics and storage are custom.                      |
| FR-26 | Ask in same conversation                    | MAF + light glue | same Chanakya request context             | Conversation identity mapping is app-owned.                  |
| FR-27 | Link follow-up to existing task             | Custom-heavy     | app waiting map + task IDs                | Strong app-specific glue.                                    |
| FR-28 | Resume after user input                     | MAF + light glue | rerun workflow with updated context       | Resume orchestration exists; linkage is custom.              |
| FR-29 | Log tasks/states/tools/outcomes             | Custom-heavy     | app JSONL + SQLite transitions            | MAF telemetry does not replace business audit log.           |
| FR-30 | Expose runtime evidence for fit decision    | MAF + light glue | workflow results + logs + docs            | Requires curated reporting by app.                           |

## Overall Interpretation

- Strongest native fit: conversational agent execution, tool invocation, workflow composition, aggregation.
- Most glue required: routing policy, explicit task domain model, state persistence, and audit-grade observability.
- Architectural implication: proceed with MAF for execution plane; keep a clear app-owned orchestration domain layer.
