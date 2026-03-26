# RPD: Chanakya MVP Feasibility Validation for Microsoft Agent Framework

**Document Type:** Reality Proving Document (RPD)
**Purpose:** Validate whether Microsoft Agent Framework (MAF) is a suitable foundation for the Chanakya app before building the larger multi-agent system.
**Output Location:** `RPD.md`

---

## 1. Introduction / Overview

Chanakya is a personal assistant (PA) that acts as the single entry point for the user. The user should not need to know which agent, team, or workflow handles a request. Chanakya either:

1. responds directly,
2. uses simple tools such as weather, reminders, or calendar,
3. or forwards the request to an Agent Manager for orchestration.

The larger vision includes persistent worker agents, independent conversational agents, temporary subagents, teams, schedules, and heartbeat-based activity. However, this RPD is **not** for building the full product. It is for proving whether MAF can support the most important patterns needed by the product.

This MVP test should answer a simple question:

> Can MAF reliably support Chanakya as a PA, an Agent Manager layer, multi-agent delegation, dependency-aware task execution, task state tracking, tool use, and a user-input loop?

If the answer is yes, the product can move to the next phase with confidence. If the answer is no, the architecture should be revised before more time is invested.

---

## 2. Goals

- Prove that Chanakya can serve as a single user-facing assistant layer.
- Prove that simple requests can be handled directly without invoking the Agent Manager.
- Prove that tool-based requests can be handled by the PA layer.
- Prove that complex requests can be delegated from Chanakya to an Agent Manager.
- Prove that the Agent Manager can decompose work into subtasks.
- Prove that subtasks can be assigned to specialized agents.
- Prove that task dependencies can be enforced, such as testing waiting for development.
- Prove that task state can be persisted and updated throughout execution.
- Prove that an agent can pause execution to request missing user input.
- Prove that the final result can be aggregated and returned back through Chanakya.
- Produce enough implementation evidence to decide whether MAF should be used for the full application.

---

## 3. User Stories

### US-001: Submit all requests through Chanakya

**Description:** As a user, I want to send every request to Chanakya so that I do not have to decide which internal agent should handle it.

**Acceptance Criteria:**

- [ ] The system provides one primary conversational entry point named Chanakya.
- [ ] A user can submit a request in natural language.
- [ ] The request is assigned a unique request or task identifier.
- [ ] Chanakya classifies the request into one of three routes: direct response, tool execution, or manager delegation.
- [ ] The route decision is logged for later inspection.
- [ ] Typecheck/lint passes.

---

### US-002: Handle a direct response without delegation

**Description:** As a user, I want Chanakya to answer simple conversational requests directly so that I get a fast response when orchestration is not needed.

**Acceptance Criteria:**

- [ ] The system supports at least one direct-response request path that does not invoke the Agent Manager.
- [ ] The response is returned in the Chanakya conversation.
- [ ] The system logs that the request was handled directly.
- [ ] The response includes no hidden dependency on the task workflow engine.
- [ ] Typecheck/lint passes.

---

### US-003: Execute a simple tool request through Chanakya

**Description:** As a user, I want Chanakya to use simple tools such as weather so that common requests do not need a multi-agent workflow.

**Acceptance Criteria:**

- [ ] Chanakya can detect a weather-related request.
- [ ] Chanakya can call a weather tool and return a useful response.
- [ ] The tool invocation is logged with timestamp and result status.
- [ ] The response clearly identifies the requested location or states when the location is missing.
- [ ] The weather path does not invoke the Agent Manager.
- [ ] Typecheck/lint passes.

---

### US-004: Delegate a complex task to the Agent Manager

**Description:** As a user, I want Chanakya to forward complex work to an Agent Manager so that specialized agents can complete the task.

**Acceptance Criteria:**

- [ ] Chanakya can create a structured task payload from a user request.
- [ ] The payload includes request text, known context, and any known constraints.
- [ ] Chanakya sends the payload to the Agent Manager.
- [ ] The Agent Manager acknowledges receipt of the task.
- [ ] The user receives a visible acknowledgement that the task has been delegated.
- [ ] Typecheck/lint passes.

---

### US-005: Decompose delegated work into subtasks

**Description:** As an Agent Manager, I want to break complex work into smaller subtasks so that specialized agents can complete them.

**Acceptance Criteria:**

- [ ] The Agent Manager creates a parent task.
- [ ] The Agent Manager creates at least two subtasks for the test workflow: development and testing.
- [ ] Each subtask has its own identifier, status, owner, and parent relationship.
- [ ] The decomposition is stored in a persistent task store.
- [ ] The decomposition can be inspected after execution.
- [ ] Typecheck/lint passes.

---

### US-006: Assign subtasks to specialized agents

**Description:** As an Agent Manager, I want to assign subtasks to specialized agents so that work is routed correctly.

**Acceptance Criteria:**

- [ ] A Developer agent can be assigned the development subtask.
- [ ] A Tester agent can be assigned the testing subtask.
- [ ] Assignment decisions are visible in task records.
- [ ] Assigned agents can read the task they are responsible for.
- [ ] The system records when an agent starts and completes a task.
- [ ] Typecheck/lint passes.

---

### US-007: Enforce dependency-aware execution

**Description:** As a user, I want dependent tasks to wait for prerequisite work so that the system behaves predictably and correctly.

**Acceptance Criteria:**

- [ ] The Tester subtask depends on successful completion of the Developer subtask.
- [ ] The Tester subtask cannot enter active execution before the Developer subtask is complete.
- [ ] If the Developer task fails, the Tester task does not run.
- [ ] The dependent task moves to a blocked or waiting state when prerequisites are incomplete.
- [ ] The dependency relationship is visible in persistent task data.
- [ ] Typecheck/lint passes.

---

### US-008: Track task state across execution

**Description:** As a user, I want the system to track task progress so that I can understand what is happening during orchestration.

**Acceptance Criteria:**

- [ ] The system supports at least these states: created, ready, assigned, in_progress, waiting_input, blocked, done, failed.
- [ ] State changes are recorded in a persistent task store.
- [ ] The parent task status reflects the combined outcome of child tasks.
- [ ] The state history is queryable after execution.
- [ ] The state model is documented clearly enough for a junior developer or AI agent to implement.
- [ ] Typecheck/lint passes.

---

### US-009: Pause execution when user input is needed

**Description:** As a user, I want the system to request clarification only when necessary so that tasks can continue with minimal interruption.

**Acceptance Criteria:**

- [ ] At least one test scenario causes an agent or manager to request additional user input.
- [ ] The current task moves to waiting_input.
- [ ] Chanakya asks the user for the missing information.
- [ ] The user’s reply is linked back to the correct task.
- [ ] The task resumes after the missing information is provided.
- [ ] Typecheck/lint passes.

---

### US-010: Aggregate and return the final result

**Description:** As a user, I want Chanakya to give me one clear final answer so that I do not need to inspect internal agent conversations.

**Acceptance Criteria:**

- [ ] The Agent Manager collects outputs from all required subtasks.
- [ ] The parent task is marked done only after all required subtasks succeed.
- [ ] The Agent Manager sends a final summary to Chanakya.
- [ ] Chanakya presents the final result in a user-friendly way.
- [ ] If the task failed or is blocked, Chanakya clearly explains why.
- [ ] Typecheck/lint passes.

---

### US-011: Validate MAF suitability for future expansion

**Description:** As a product builder, I want this MVP to reveal whether MAF can support future features so that I can make an informed architecture decision.

**Acceptance Criteria:**

- [ ] The implementation documents which tested capabilities map cleanly to MAF concepts.
- [ ] The implementation documents which desired features feel natural, awkward, or unsupported.
- [ ] The implementation includes a short decision summary: proceed, proceed with constraints, or reconsider framework.
- [ ] The implementation identifies the highest-risk missing features for the full product vision.
- [ ] Typecheck/lint passes.

---

## 4. Functional Requirements

### Core Routing

1. **FR-1:** The system must provide one user-facing assistant named Chanakya.
2. **FR-2:** Chanakya must classify incoming requests into direct response, tool execution, or Agent Manager delegation.
3. **FR-3:** The route decision must be recorded for each request.
4. **FR-4:** Chanakya must be able to return either an immediate answer, a delegated-task acknowledgement, or a clarification request.

### Tool Support

5. **FR-5:** Chanakya must support at least one working simple tool integration for weather.
6. **FR-6:** The weather tool path must be executable without using the Agent Manager.
7. **FR-7:** Tool runs must be logged with request ID, tool name, status, and output summary.

### Agent Manager

8. **FR-8:** The Agent Manager must accept a structured task payload from Chanakya.
9. **FR-9:** The Agent Manager must create a parent task for each delegated request.
10. **FR-10:** The Agent Manager must decompose the parent task into at least two subtasks in the MVP workflow.
11. **FR-11:** The Agent Manager must assign subtasks to specialized agents.
12. **FR-12:** The Agent Manager must aggregate subtask results into a final parent-task result.

### Agents

13. **FR-13:** The MVP must include a Developer agent.
14. **FR-14:** The MVP must include a Tester agent.
15. **FR-15:** Agents must execute only when assigned a task in this MVP.
16. **FR-16:** Agents must be able to report status updates and results back to the Agent Manager.

### Task Model

17. **FR-17:** The task system must support parent-child task relationships.
18. **FR-18:** Each task must include a unique ID, description, owner, status, dependencies, and result field.
19. **FR-19:** The system must persist task data in a way that can be inspected after execution.
20. **FR-20:** The system must support a task state machine with clear allowed transitions.
21. **FR-21:** The system must support dependency links between subtasks.

### Dependency Handling

22. **FR-22:** The Tester subtask must not run until the Developer subtask is complete.
23. **FR-23:** If a dependency fails, downstream tasks must not proceed automatically.
24. **FR-24:** The system must mark downstream tasks as blocked or waiting when prerequisites are unmet.

### User Input Loop

25. **FR-25:** The system must support pausing a task while waiting for user input.
26. **FR-26:** Chanakya must present user-input requests in the same conversation where the original task began.
27. **FR-27:** User-provided follow-up information must be linked to the existing task rather than creating a new unrelated task.
28. **FR-28:** The system must support resuming a paused task after user input is received.

### Observability

29. **FR-29:** The implementation must log task creation, task routing, state transitions, tool usage, and final outcomes.
30. **FR-30:** The implementation must expose enough runtime evidence to manually verify whether MAF is a good fit for future expansion.

---

## 5. Non-Goals (Out of Scope)

The MVP and this RPD must **not** attempt to validate the full long-term vision. The following are explicitly out of scope:

- Full persistent agent ecosystem
- Friend agents, therapist agents, advisor agents, or social circles
- Direct user chat handoff to independent agents
- Scheduled agents or heartbeat-based automation
- Agent-to-agent social conversations
- Multi-team project structures
- Marketing, blogging, research, or friend teams
- Temporary subagent creation
- Rich UI dashboards, kanban boards, or mission-control views
- Multi-user access or SaaS tenancy
- Advanced permission systems
- Production-grade memory design
- Full tool catalog beyond the minimum needed for the test
- Building the real product instead of a feasibility probe

---

## 6. Design Considerations

- The experience should feel like one assistant first, not a cluster of exposed internal components.
- Internal orchestration details should remain hidden from the user except for useful status summaries.
- The user should clearly understand whether a request is complete, in progress, waiting for input, blocked, or failed.
- The implementation should favor clarity and inspectability over optimization.
- The MVP should use a minimal but explicit domain model so future phases can evolve from it.
- The system should be easy for another agent or junior developer to understand and extend after reading this document.

---

## 7. Technical Considerations

- The implementation should be built specifically to test Microsoft Agent Framework, not as a generic simulation.
- The software should be organized so that the PA layer, manager layer, agent layer, and task store are easy to separate and inspect.
- The task model should be persistent enough that execution can be reviewed after the run completes.
- The implementation should document which parts are clean MAF fits and which parts require custom glue code.
- The test should prefer a small number of components with good logging over a larger feature surface with weak observability.
- The implementation should use realistic interfaces for future growth, even if the first version uses simplified internal logic.

---

## 8. Success Metrics

This RPD is successful if the implementation demonstrates the following:

- At least one direct-response request is handled correctly by Chanakya.
- At least one weather request is handled correctly via a tool path.
- At least one delegated request is handled through the Agent Manager.
- The delegated request is decomposed into development and testing subtasks.
- The Tester task does not run before the Developer task completes.
- Task states are persisted and visible for inspection.
- At least one task can pause for user input and then resume.
- The final result is returned to the user through Chanakya.
- The implementation produces a short written conclusion about whether MAF is suitable for the broader product vision.

Suggested interpretation:

- **Pass:** MAF supports the tested flows naturally and clearly.
- **Partial Pass:** MAF supports the flows, but some key behaviors require awkward custom logic.
- **Fail:** MAF does not support the target orchestration model cleanly enough for the intended product.

---

## 9. Open Questions

- Should the MVP use truly persistent storage or a simple lightweight local store for initial feasibility testing?
- What is the minimum amount of MAF-native orchestration needed to count as a fair framework evaluation?
- How much of the routing logic should live inside Chanakya versus the Agent Manager?
- Should the test include one intentionally failing workflow to prove dependency blocking behavior?
- What format should the final framework suitability summary use so it can guide the next architecture phase?
- If MAF succeeds for this MVP, what is the next highest-risk feature to validate: persistence, scheduling, or independent conversational agents?

---

## 10. Test Scenarios

The implementation must include these explicit test scenarios.

### TS-001: Direct response path

**Input:** A simple conversational request that does not require tools or delegation.
**Expected Result:** Chanakya answers directly and logs the request as direct.

### TS-002: Weather tool path

**Input:** A weather-related request with a location.
**Expected Result:** Chanakya uses the weather tool and returns the result without involving the Agent Manager.

### TS-003: Delegated feature task

**Input:** A request that clearly requires development and testing work.
**Expected Result:** Chanakya delegates to the Agent Manager, which creates a parent task and child tasks for development and testing.

### TS-004: Dependency enforcement

**Input:** A delegated task where the Developer task is not complete.
**Expected Result:** The Tester task remains blocked or waiting and does not execute.

### TS-005: Failure path

**Input:** A delegated task where the Developer task fails.
**Expected Result:** The parent task reflects failure or blocked downstream status, and Chanakya reports that clearly to the user.

### TS-006: Missing input path

**Input:** A delegated task that requires extra detail from the user to continue.
**Expected Result:** The task pauses, Chanakya asks for the missing detail, the user responds, and the task resumes.

### TS-007: Final aggregation

**Input:** A delegated task whose subtasks complete successfully.
**Expected Result:** The Agent Manager aggregates the outputs and Chanakya returns a clear final answer.

---

## 11. Required Deliverables from the Builder Agent

The builder agent should produce all of the following:

1. A working MVP implementation focused on MAF feasibility.
2. A short README explaining how to run the test scenarios.
3. A clear task model definition used by the MVP.
4. A record of task state transitions for each major test scenario.
5. A short conclusion document answering:
   - What worked well in MAF?
   - What required custom work?
   - What felt awkward or risky?
   - Should the product continue with MAF?

---

## 12. Suggested Implementation Boundaries

The builder agent should keep the MVP intentionally small.

### Required Components

- Chanakya PA
- Agent Manager
- Developer agent
- Tester agent
- Weather tool
- Task store
- Logging / trace output

### Optional Only If Easy

- Reminder or calendar tool
- Better state history visualization
- Additional reporting helpers

### Do Not Add Yet

- Social agents
- Therapist or friend agents
- Team directories
- Task boards
- Scheduling
- Heartbeat agents
- Temporary subagents
- Independent agent chat switching
- Long-term memory systemssure

---

## 13. Final Instruction to the Builder Agent

Build the smallest implementation that can **honestly test** whether MAF is a good foundation for Chanakya.

Do **not** optimize for polish or feature breadth.
Do **not** build the future system yet.
Do **not** invent extra scope.

The implementation should answer one question only:

> Can Microsoft Agent Framework support the core execution pattern needed by Chanakya?

If yes, document why.
If no, document why not.

---
