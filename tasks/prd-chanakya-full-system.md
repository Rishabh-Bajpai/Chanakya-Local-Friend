# PRD: Chanakya – Personal Multi-Agent Operating System

**Document Type:** Product Requirements Document (PRD)
**Version:** V1 (Post-MVP Architecture Validated)
**Purpose:** Define the full product built on top of Microsoft Agent Framework (MAF) as an execution layer, with a custom domain layer for task orchestration, persistence, and system intelligence.
**Output Location:** `tasks/prd-chanakya-full-system.md`

---

# 1. Introduction / Overview

Chanakya is a **personal multi-agent operating system** that allows a user to interact with a single assistant while a network of intelligent agents collaborates in the background to complete tasks, provide insights, and maintain ongoing workflows.

The system consists of:

- **Chanakya (PA)** → the single user-facing interface
- **Agent Manager** → orchestrates agents and workflows
- **Domain Layer (Core System)** → owns tasks, state, dependencies, memory, and routing
- **Agents (Execution Layer via MAF)** → perform work

The system supports:

- Task execution (e.g., development, research, automation)
- Continuous workflows (scheduled / heartbeat agents)
- Social interactions (friends, therapist, advisors)
- Multi-agent collaboration
- Persistent agent identities with roles, skills, and personalities

---

# 2. Core Product Philosophy

> Chanakya is NOT a chatbot.
> It is a **task-driven operating system powered by agents**.

Key principles:

- Single interface, many agents
- Tasks are the source of truth (not conversations)
- Agents are workers, not decision-makers
- The system controls orchestration, not agents
- Execution is delegated, control is centralized

---

# 3. Goals

- Provide a **single intelligent assistant (Chanakya)** for all user interactions
- Enable **automatic delegation and orchestration of tasks**
- Support **persistent agents with roles, personalities, and tools**
- Enable **multi-agent collaboration with dependencies**
- Support **both task execution and conversational agents**
- Provide **task lifecycle visibility and control**
- Enable **scheduled and continuous agent behaviors**
- Build a **scalable architecture for future expansion**

---

# 4. User Stories

## Core Interaction

### US-001: Unified interface

- User interacts only with Chanakya
- No need to choose agents manually

---

### US-002: Smart routing

- Chanakya decides:
  - direct response
  - tool usage
  - delegation to Agent Manager

---

## Task Execution

### US-003: Task delegation

- User submits a complex request
- Chanakya sends it to Agent Manager

---

### US-004: Task decomposition

- Agent Manager creates:
  - parent task
  - subtasks
  - dependencies

---

### US-005: Multi-agent execution

- Tasks assigned to:
  - developers
  - testers
  - researchers
  - writers

---

### US-006: Dependency handling

- Tasks respect execution order
- Example: testing waits for development

---

### US-007: Task tracking

- User can understand:
  - in progress
  - blocked
  - waiting
  - completed

---

### US-008: User input loop

- System pauses when needed
- Requests clarification
- Resumes after input

---

### US-009: Final result delivery

- Agent Manager aggregates outputs
- Chanakya presents final result

---

## Agent System

### US-010: Persistent agents

- System can create agents like:
  - developer
  - tester
  - researcher
  - blogger

---

### US-011: Agent roles & identity

- Each agent has:
  - role
  - personality
  - tools
  - workspace

---

### US-012: Temporary subagents

- Agents can create short-lived subagents
- Automatically cleaned up after tasks

---

## Social / Independent Agents

### US-013: Direct agent interaction

- User can “call” an agent
- Example: talk to Friend 1

---

### US-014: Social circles

- Agents can belong to groups
- Example: friend group conversations

---

### US-015: Isolated agents

- Some agents (e.g., therapist) do not interact with others

---

## Continuous / Scheduled Agents

### US-016: Scheduled execution

- Agents run at fixed times
- Example: daily vulnerability scan

---

### US-017: Heartbeat agents

- Agents run continuously in background

---

## System Behavior

### US-018: Workspace isolation

- Each agent has its own context
- No unintended data leakage

---

### US-019: Observability

- System logs:
  - tasks
  - decisions
  - agent actions

---

# 5. Functional Requirements

## Chanakya (PA)

- FR-1: Single conversational interface
- FR-2: Request classification (direct/tool/delegate)
- FR-3: Tool usage (weather, reminders, calendar)
- FR-4: Communication with Agent Manager

---

## Domain Layer (Core System – YOU BUILD THIS)

- FR-5: Task schema with:

  - id
  - parent_id
  - status
  - dependencies
  - owner
  - result
- FR-6: Task state machine:

  - created
  - ready
  - in_progress
  - waiting_input
  - blocked
  - done
  - failed
- FR-7: Task persistence (DB or equivalent)
- FR-8: Dependency engine
- FR-9: Task routing logs
- FR-10: User input linking
- FR-11: Task history tracking

---

## Agent Manager

- FR-12: Task decomposition
- FR-13: Agent selection
- FR-14: Workflow orchestration
- FR-15: Result aggregation

---

## Agents (MAF Execution Layer)

- FR-16: Worker agents (dev, tester, etc.)
- FR-17: Independent agents (friend, therapist)
- FR-18: Temporary subagents
- FR-19: Tool usage
- FR-20: Status reporting

---

## Scheduling System

- FR-21: Time-based triggers
- FR-22: Recurring jobs
- FR-23: Background execution tracking

---

## Communication

- FR-24: Manager → Chanakya updates
- FR-25: Chanakya → User responses
- FR-26: Agent → Manager reporting

---

# 6. System Architecture

## Layer 1: Chanakya (Interface)

- User interaction
- Routing
- Response delivery

---

## Layer 2: Domain Layer (Core)

- Task system
- State machine
- Dependency graph
- Persistence
- Logs

---

## Layer 3: Agent Manager

- Workflow orchestration
- Task decomposition
- Agent coordination

---

## Layer 4: Execution Layer (MAF)

- Agents
- Tools
- Workflows

---

# 7. Non-Goals

- Fully autonomous agents without control
- Unbounded agent communication
- Real-time avatar/voice systems
- Large-scale SaaS multi-tenancy (initially)
- Fully self-improving agents
- Replacing task system with agent memory

---

# 8. Success Metrics

- ≥90% correct routing decisions
- Reliable multi-agent task completion
- Dependency enforcement correctness
- Task lifecycle fully traceable
- Successful scheduled agent execution
- Clear user understanding of system outputs
- Stable execution across multiple tasks

---

# 9. Risks

- Over-reliance on agent reasoning instead of system logic
- Complexity explosion with too many agents
- Poor task modeling leading to brittle workflows
- State inconsistency if TaskStore is not robust
- Tool misuse without proper permissions

---

# 10. Open Questions

- How should memory be shared between agents?
- What is the promotion path for temporary → persistent agents?
- How to resolve conflicting outputs from multiple agents?
- What level of autonomy is safe for agents?
- How to visualize tasks for the user (future UI)?

---

# 11. Key Insight (Critical)

> Chanakya is not an agent system.
> It is a **task orchestration system powered by agents**.

- Tasks = source of truth
- Agents = execution layer
- MAF = runtime engine
- Domain layer = real product
