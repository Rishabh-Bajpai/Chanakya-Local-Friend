# Issue 1: Split Conversation Layer into an Independent Stack

## Problem

Currently, the conversation layer and core agent live in the same app stack and share the same `db.py` file, creating tight coupling:

```
Current structure:
┌─────────────────────────────────────┐
│           app/db.py                │
│  - history_messages (agent)        │
│  - working_memory (wrapper)        │
│  - episodic_summaries (wrapper)   │
└─────────────────────────────────────┘
```

This is problematic because:
1. The wrapper directly accesses agent persistence concerns
2. The conversation layer cannot be reused cleanly with many different MAF agents
3. Agent persistence is mixed with wrapper persistence
4. If agent schema or tools change, the wrapper risks breaking
5. The repo does not clearly separate reusable conversation-layer code from example/demo agent code

## Goal

Make the conversation layer completely independent from the agent as its own reusable stack, and keep the core chatbot app as a separate host app that demonstrates both:

1. a raw agent without the conversation layer
2. the same or similar agent with the conversation layer applied

## Desired Architecture

```
Repository / product structure:

┌──────────────────────────────────────────────────────────┐
│                conversation_layer/                      │
│  Reusable package/library                              │
│  - conversation_wrapper                                │
│  - policy_engine                                       │
│  - working_memory                                      │
│  - episodic_summary                                    │
│  - preference_signals                                  │
│  - disclosure_planner                                  │
│  - conversation DB/store                               │
│  - agent interface / protocol                          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                   core_agent_app/                       │
│  Host chatbot application                              │
│  - MAF agents                                           │
│  - agent db.py                                          │
│  - agent tools/config                                   │
│  - route/app using raw agent                            │
│  - route/app using agent wrapped by conversation layer  │
└──────────────────────────────────────────────────────────┘

Interaction model:

Conversation layer -> Agent interface -> Agent implementation

The conversation layer should treat the agent as a black box.
It should not know whether the agent uses a DB, which tools it has internally, or how its state is stored.
```

## Required Changes

### 1. Separate the Code into Two Independent Parts

Create two clear boundaries in the codebase:
- `conversation_layer` (reusable package/module)
- `core_agent_app` (host/example chatbot app)

The conversation layer should be portable and usable by any app that has MAF agents.

### 2. Create Separate Storage for the Conversation Layer

Create conversation-layer-owned storage such as `conversation_db.py` (or an equivalent storage abstraction) for:
- `working_memory` table
- `episodic_summaries` table

This storage must be owned by the conversation layer, not by the agent app.

### 3. Remove Agent Dependencies from Conversation Files

Update these files to NOT import from `app/db.py`:
- `app/services/working_memory.py`
- `app/services/episodic_summary.py`
- `app/services/conversation_wrapper.py`

### 4. Create Abstract Agent Interface

Create an interface/protocol that the conversation layer uses to talk to agents without knowing agent internals:

```python
# app/services/agent_interface.py
from typing import Protocol
from dataclasses import dataclass

@dataclass
class AgentContext:
    history: list[dict]  # or proper message types
    tools: list[dict]

class AgentInterface(Protocol):
    def get_context(self, conversation_id: str) -> AgentContext: ...
    def execute(self, message: str, context: AgentContext) -> str: ...
```

The exact methods can change, but the interface should stay small and stable.

### 5. Update `conversation_wrapper.py`

Instead of:
```python
from app.db import history_messages  # BAD - tight coupling
```

Use:
```python
from app.services.agent_interface import AgentInterface

class ConversationWrapper:
    def __init__(self, agent: AgentInterface, conversation_id: str):
        self.agent = agent
        self.conversation_id = conversation_id
```

### 6. Use Dependency Injection at the App Level

In `app.py` or wherever the wrapper is instantiated, inject the specific agent:

```python
agent = MAFAgent(agent_id="agent_123")  # Any MAF agent
wrapper = ConversationWrapper(agent=agent, conversation_id="conv_456")
```

### 7. Expose Both Raw and Wrapped Agents in the Host App

The core chatbot app should provide two runnable paths:
- a raw agent path with no conversation layer
- a wrapped agent path with the conversation layer applied

This gives a clean demo, evaluation baseline, and integration example for future apps.

### 8. Support Wrapper-First Integration

The primary implementation should be a wrapper object, for example:

```python
raw_agent = SupportAgent(...)
wrapped_agent = ConversationWrapper(raw_agent, conversation_store=...)
```

Optionally, a decorator can be added later as a convenience API, but the wrapper should remain the core implementation.

## Files to Modify

- `app/db.py` - Keep agent-only persistence or move into the host agent app boundary
- Create conversation-layer-owned storage module such as `app/services/conversation_db.py`
- `app/services/working_memory.py` - Use conversation-layer storage instead of `app.db`
- `app/services/episodic_summary.py` - Use conversation-layer storage instead of `app.db`
- Create `app/services/agent_interface.py` - Abstract interface for agent communication
- `app/services/conversation_wrapper.py` - Use `AgentInterface` instead of direct DB access
- `app/services/history_provider.py` - Update to read through the interface or conversation-owned storage
- `app/routes.py` - Update instantiation to inject raw vs wrapped agent paths
- Repo/package structure - likely needs reorganization to reflect `conversation_layer` vs `core_agent_app`

## Acceptance Criteria

1. **Two independent parts exist**: The codebase clearly separates the reusable conversation layer from the host core agent app
2. **Conversation layer owns its state**: Working memory and episodic summaries are stored outside the agent DB and are owned by the conversation layer
3. **No import coupling**: Conversation-layer files do not import agent persistence directly from `app/db.py`
4. **AgentInterface exists**: An abstract interface/protocol is defined for agent communication
5. **Wrapper uses interface**: `ConversationWrapper` communicates with agents only through the interface
6. **Agent is a black box**: The conversation layer does not know agent implementation details such as agent DB schema or internal tools wiring
7. **Raw and wrapped paths both exist**: The host app exposes one agent path without the conversation layer and one with the conversation layer applied
8. **Reusable with many MAF agents**: A new MAF agent can be wrapped without changing conversation-layer internals
9. **Wrapper-first integration works**: The main supported integration is `ConversationWrapper(agent)`; decorator support is optional and secondary
10. **Tests pass**: Existing tests pass or are updated appropriately, including tests for both raw-agent and wrapped-agent behavior
