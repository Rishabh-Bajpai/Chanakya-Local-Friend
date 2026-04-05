# Workflow Architecture Analysis Report

**Author:** Analysis Agent  
**Date:** 2026-04-05  
**Scope:** `chanakya/` module — workflow execution, delegation, state management, and task orchestration

---

## Executive Summary

This report analyzes the workflow architecture in Chanakya, identifying fundamental design issues that cause suboptimal behavior — particularly around **incremental updates** and **follow-up modifications**. The core issue is that every user message triggers a complete workflow execution from scratch, rather than intelligently detecting whether the message is a follow-up to existing work.

**Key finding:** The system lacks awareness of previous work context. When a user says "change X" in `/work` mode, it treats it as a completely new request rather than a modification to existing output.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Workflow Execution Flow](#2-workflow-execution-flow)
3. [Issues Identified](#3-issues-identified)
4. [Root Cause Analysis](#4-root-cause-analysis)
5. [Recommendations](#5-recommendations)
6. [Appendix: Code Locations](#6-appendix-code-locations)

---

## 1. Architecture Overview

### 1.1 Core Concepts

| Concept | Description |
|---------|-------------|
| **Work** | A container for a long-running task with multiple agent sessions |
| **Request** | A single user message + its associated task tree |
| **Task** | A unit of work with status, input, output, and parent/child relationships |
| **Workflow** | A predefined execution pattern (software_delivery or information_delivery) |

### 1.2 Workflow Types

```python
WORKFLOW_SOFTWARE = "software_delivery"  # CTO → Developer → Tester
WORKFLOW_INFORMATION = "information_delivery"  # Informer → Researcher → Writer
```

### 1.3 Task Hierarchy

```
root_task (created from user message)
    │
    ├── manager_task (Agent Manager orchestration)
    │       │
    │       ├── specialist_task (CTO or Informer)
    │       │       │
    │       │       ├── developer_task
    │       │       │       │
    │       │       │       └── [temp subagents...]
    │       │       │
    │       │       └── tester_task
    │       │               │
    │       │               └── [temp subagents...]
```

### 1.4 Entry Points

| Path | Handler | Purpose |
|------|---------|---------|
| `/api/chat` | `app.py:177` | Main chat endpoint |
| `/work` | `app.py:170` | Work-specific UI |
| `/api/works` | `app.py:394` | Create new work |

---

## 2. Workflow Execution Flow

### 2.1 New Request Flow

```
POST /api/chat {message: "..."}
        │
        ▼
chat_service.chat(session_id, message)
        │
        ▼
create request + root_task
        │
        ▼
_triage_message(message) ──► decides "delegate" or "direct"
        │
        ├─► [delegate] AgentManager.execute()
        │        │
        │        ▼
        │    _select_route() ──► chooses cto vs informer
        │        │
        │        ▼
        │    _execute_software_workflow() or _execute_information_workflow()
        │        │
        │        ▼
        │    specialist (CTO/Informer) ──► worker (Developer/Tester)
        │
        └─► [direct] MAFRuntime.run()
                 │
                 ▼
             direct agent response
```

### 2.2 Key Observation: Every Message Creates New Request

Looking at `chat_service.py:140-264`:

```python
def chat(self, session_id: str, message: str, *, work_id: str | None = None) -> ChatReply:
    # Always creates new request_id
    request_id = make_id("request")
    
    # Creates new root_task
    root_task_id = self.store.create_task(...)
```

**The system does not track whether this message is related to previous work.**

### 2.3 Work Mode Flow

When `work_id` is provided in `/api/chat`:

```python
# app.py:185-213
if work_id is not None:
    work_record = store.get_work(work_id)
    session_id = store.ensure_work_agent_session(work_id=work_id, ...)
    
# Then same flow as above
reply = chat_service.chat(session_id, message, work_id=work_id)
```

**Issue:** The `work_id` groups sessions together, but does NOT influence how the agent processes the message. The manager still executes from scratch.

---

## 3. Issues Identified

### 3.1 🔴 Critical: No Incremental Update Detection

**Problem:** When a user makes a small edit request to existing work, the system regenerates everything from scratch.

**Example scenario:**
1. User creates a work: "Write a report about AI trends"
2. System runs: Informer → Researcher → Writer → produces full report
3. User says: "Make the tone more formal"
4. System runs: **completely new workflow** → "Write a report about AI trends" → new full report

**Expected behavior:**
- Detect this is a modification request
- Pass the previous Writer output to the Writer with: "Make this more formal"
- Only the Writer stage runs

**Current behavior:**
- Creates new request_id
- Creates new root_task
- Runs full workflow from scratch with the original message

**Code location:**
- `chat_service.py:140` — `request_id = make_id("request")` always generates new ID
- `chat_service.py:249` — `manager_result = self.manager.execute(message=message)` always runs full workflow

---

### 3.2 🟠 High: No Context Preservation Between Work Messages

**Problem:** The system doesn't track what work has been done in a work session, so it can't determine what needs to be redone vs. what can be reused.

**Evidence:**
- `store.py:790-798` — `find_work_id_by_session()` only finds work_id, not previous outputs
- No method exists to get: "What was the last output from the Writer agent?"
- When resuming work, the original message is always used

**Code location:**
- `agent_manager.py:663-664` — Original message always used:
```python
developer_prompt = self._build_developer_stage_prompt(message, implementation_brief)
tester_prompt = self._build_tester_stage_prompt(message, implementation_brief)
```

**What's missing:**
- No storage of stage outputs in a way that's accessible to subsequent requests
- No "last known good state" for each stage
- No diff detection between previous and current requests

---

### 3.3 🟠 High: Work Mode Doesn't Influence Workflow Execution

**Problem:** Passing `work_id` to `/api/chat` only changes the session handling, not how the workflow processes the message.

**Code evidence:**
```python
# chat_service.py:140
def chat(self, session_id: str, message: str, *, work_id: str | None = None) -> ChatReply:
    # work_id is passed through but never used to influence workflow behavior
    ...
    manager_result = self.manager.execute(message=message, ...)  # Same as non-work mode
```

**What's needed:**
- If `work_id` exists, check for prior outputs
- If prior outputs exist, determine if this is an edit/modification request
- If edit, run targeted stage instead of full workflow

---

### 3.4 🟡 Medium: No Request Relationship Tracking

**Problem:** Requests within a work are not linked. There's no "parent request" or "follows of" relationship.

**Code evidence:**
- `model.py` — `RequestModel` has no `parent_request_id` or `follows_request_id` field
- Each request is independent

**What's needed:**
- Link requests within a work: `previous_request_id` field
- Detect when a request is a modification of a previous one
- Store stage outputs keyed by work/request for later retrieval

---

### 3.5 🟡 Medium: Workflow Always Re-runs from CTO Brief

**Problem:** Even when not needed, the CTO brief is regenerated for every message.

**Code evidence:**
```python
# agent_manager.py:658-662
implementation_brief = self._run_specialist_prompt(
    specialist_profile,
    self._build_cto_brief_prompt(message),  # Always regenerated
    step="brief",
)
```

**What's needed:**
- Cache the implementation_brief for the work
- Only regenerate if the request scope changes significantly

---

### 3.6 🟢 Low: No Diff Detection Between Requests

**Problem:** The system can't determine what changed between requests to optimize workflow execution.

**What's needed:**
- Simple diff of: "previous message" vs "current message"
- Determine: same task? modification? new task?
- Use this to decide which stages to re-run

---

### 3.7 🟢 Low: Task Inputs Not Used for Optimization

**Problem:** The system stores task inputs but never uses them to determine if work can be skipped or modified.

**Code evidence:**
```python
# agent_manager.py:665-681 — inputs are stored but never consulted later
self.store.update_task(
    developer_task_id,
    input_json={
        "message": message,
        "supervisor_brief": implementation_brief,
        "effective_prompt": developer_prompt,
    },
)
```

---

### 3.8 🟢 Low: Recovery Paths Always Rebuild Prompts

**Problem:** Recovery prompts (for invalid outputs) rebuild the entire context rather than using cached outputs.

**Code evidence:**
```python
# agent_manager.py:1407-1418
def _build_tester_handoff_prompt(...):
    return (
        "The developer completed the implementation handoff below..."
        f"Developer handoff: {developer_output}"  # Full output passed again
    )
```

**What's needed:**
- Instead of rebuilding prompts with full context, use a reference to cached output
- This becomes more important as outputs grow larger

---

### 3.9 🔵 Info: Temporary Subagents Have No Work Awareness

**Problem:** Temporary subagents don't know about the work context, so they can't help with incremental changes.

**Code evidence:**
- `subagents.py` — no work_id parameter in subagent creation
- Subagents are created fresh each time

---

### 3.10 🔵 Info: No Work-Level Output Caching

**Problem:** Work outputs are only stored in task results, not in a work-level cache for quick retrieval.

**What's needed:**
- Store each stage's final output keyed by work_id
- Enable "get last Writer output for Work X"
- Enable "update Writer output for Work X with modifications"

---

## 4. Root Cause Analysis

### The Core Problem

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CURRENT EXECUTION MODEL                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   User Message ──► New Request ──► New Task Tree ──► Full Workflow    │
│         │                 │              │                    │        │
│         │                 │              │                    │        │
│         ▼                 ▼              ▼                    ▼        │
│   "Make it formal"  request_456     task_root_789    CTO→Dev→Test    │
│                                                                     │
│   Everything is new. No awareness of previous work.               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### What Should Happen

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    DESIRED EXECUTION MODEL                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   User Message                                                         │
│        │                                                               │
│        ▼                                                               │
│   Detect: Is this a follow-up to existing work?                      │
│        │                                                               │
│   ┌────┴────┐                                                          │
│   │         │                                                          │
│   ▼         ▼                                                          │
│  [Yes]    [No]                                                        │
│   │         │                                                          │
│   │         ▼                                                          │
│   │    New Request → Full Workflow                                    │
│   │                                                               │
│   ▼                                                               │
│   Determine: What type of follow-up?                                  │
│   ┌──────────────┬────────────────┬─────────────────┐               │
│   │              │                │                 │               │
│   ▼              ▼                ▼                 ▼               │
│  Modification  Continuation   Clarification   New Sub-task          │
│   (edit stage)   (next stage)   (pause/resume)  (add new work)       │
│                                                                         │
│   Only run the necessary stage(s), reusing previous outputs         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Missing Components

1. **Request Relationship Model** — Link requests within a work
2. **Work Output Cache** — Store stage outputs for quick retrieval
3. **Intent Detection** — Classify message as new vs. modification vs. continuation
4. **Stage Skipping Logic** — Determine which stages to run based on intent
5. **Context Injection** — Pass previous outputs to agents for modifications

---

## 5. Recommendations

### 5.1 Immediate Actions (Critical)

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🔴 Critical | No incremental updates | Add intent detection: parse message for modification keywords ("change", "update", "modify", "fix", "make X more Y") |
| 🔴 Critical | No context preservation | Add `previous_output` parameter to `_build_*_stage_prompt()` methods |
| 🟠 High | Work mode ignored | Make `work_id` influence workflow execution: if work exists, check for prior outputs first |

### 5.2 Short-Term Improvements

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🟡 Medium | No request linking | Add `previous_request_id` to RequestModel; link requests in same work |
| 🟡 Medium | Brief always regenerated | Cache implementation_brief per work; only regenerate on scope change |
| 🟡 Medium | No output caching | Add work-level output cache store: `store.get_work_output(work_id, stage)` |

### 5.3 Medium-Term Enhancements

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| 🟢 Low | No diff detection | Add message diff: compare with previous request to determine scope change |
| 🟢 Low | No stage skipping | Add logic: if modification targets Writer, only run Writer stage |
| 🟢 Low | Recovery rebuilds | Use cached outputs in recovery prompts instead of rebuilding full context |

### 5.4 Implementation Sketch

```python
# Example: Intent detection for work mode
def detect_message_intent(message: str, work_id: str | None) -> str:
    """Returns: 'new' | 'modification' | 'continuation' | 'clarification'"""
    
    if not work_id:
        return 'new'
    
    modification_keywords = [
        "change", "modify", "update", "revise", "fix", "make it",
        "more formal", "less formal", "shorter", "longer",
        "add section", "remove section", "improve", "enhance"
    ]
    
    message_lower = message.lower()
    if any(kw in message_lower for kw in modification_keywords):
        return 'modification'
    
    # Check if previous request exists
    previous = store.get_last_request_for_work(work_id)
    if previous and is_continuation(previous.message, message):
        return 'continuation'
    
    return 'new'


def chat_service.chat_with_work_intent(
    session_id: str, 
    message: str, 
    work_id: str | None
) -> ChatReply:
    intent = detect_message_intent(message, work_id)
    
    if intent == 'modification' and work_id:
        # Get previous outputs
        previous_outputs = store.get_work_outputs(work_id)
        
        # Determine which stage needs updating
        target_stage = determine_modification_target(message, previous_outputs)
        
        # Run only that stage with previous output
        return run_targeted_stage(
            target_stage=target_stage,
            previous_output=previous_outputs[target_stage],
            modification_request=message,
            work_id=work_id,
        )
    
    # Default: full workflow (existing behavior)
    return existing_chat_flow(...)
```

---

## 6. Appendix: Code Locations

### Core Files

| File | Purpose |
|------|---------|
| `chanakya/chat_service.py` | Request handling, routing decision |
| `chanakya/agent_manager.py` | Workflow execution, task orchestration |
| `chanakya/store.py` | Persistence, work/session management |
| `chanakya/model.py` | ORM models (Request, Task, Work) |
| `chanakya/app.py` | Flask routes, API endpoints |
| `chanakya/templates/work.html` | Work UI |

### Key Method Locations

| Method | Line | Issue |
|--------|------|-------|
| `chat_service.chat()` | 140 | 3.1, 3.2, 3.3 |
| `chat_service._triage_message()` | 228 | 3.1 |
| `agent_manager.execute()` | 147 | 3.1 |
| `agent_manager._select_route()` | 1301 | 3.5 |
| `agent_manager._execute_software_workflow()` | 612 | 3.5 |
| `agent_manager._build_developer_stage_prompt()` | 1393 | 3.2, 3.8 |
| `agent_manager._build_tester_handoff_prompt()` | 1407 | 3.8 |
| `store.create_request()` | 195 | 3.4 |
| `store.ensure_work_agent_session()` | 710 | 3.3 |

### Missing Methods (Needed for Fixes)

| Method | Purpose |
|--------|---------|
| `store.get_work_outputs(work_id)` | Get all stage outputs for a work |
| `store.get_last_request_for_work(work_id)` | Get most recent request |
| `store.save_work_output(work_id, stage, output)` | Cache stage output |
| `agent_manager.run_targeted_stage()` | Run single stage with context |
| `detect_message_intent()` | Classify message as new/modify/continue |

---

## Conclusion

The workflow architecture is well-structured for executing new tasks from scratch, but lacks the intelligence to handle **incremental modifications**. The user's specific complaint — that a small change triggers full regeneration — is a fundamental design gap.

**Root cause:** Every message creates a new request with no awareness of previous work outputs.

**Fix direction:** 
1. Track previous outputs per work
2. Detect message intent (new vs. modification)
3. Run targeted stages instead of full workflows when appropriate
4. Pass previous outputs to agents for modification requests

This is a moderate refactoring effort, primarily involving:
- Adding output caching to store layer
- Adding intent detection in chat service
- Modifying workflow execution to support targeted stages
- Adding UI hints for modification mode
