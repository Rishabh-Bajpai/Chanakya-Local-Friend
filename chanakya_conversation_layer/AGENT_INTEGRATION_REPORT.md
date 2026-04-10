# Agent Integration Report

This report explains how to reuse the Conversation Intelligence Layer with other agents.

## What This Product Is

The product is a reusable wrapper that sits between a user-facing interface and a core agent.

The wrapper adds:

- transcript persistence
- explicit working memory
- dialogue policy selection
- gradual disclosure
- staged response realization
- resumable state across turns
- interruption handling
- lightweight critique before send
- debug visibility and evaluation support

It is designed so the wrapped agent can keep doing tool use and task execution while the wrapper controls how information is delivered.

## Core Reuse Pattern

For another agent, the integration pattern is:

1. implement `AgentInterface`
2. feed that adapter into `ConversationWrapper`
3. provide a history provider and working-memory store
4. call `ConversationWrapper.handle(ChatRequest(...))`
5. return the resulting `ChatResponse`

## Main Runtime Components

### Request/response contracts

- `conversation_layer/schemas.py`
- `ChatRequest(session_id, message, metadata)`
- `ChatResponse(session_id, response, metadata, messages)`

### Wrapper entry point

- `conversation_layer/services/conversation_wrapper.py`

This is the main orchestration layer. It:

- loads working memory
- applies seeded updates
- infers preference signals
- restores suspended threads when needed
- selects policy or resume behavior
- calls the core agent when needed
- runs critique before final send
- saves transcript, working memory, and episodic summary

### Adapter contract

- `conversation_layer/services/agent_interface.py`
- base required interface: `AgentInterface`

Your agent only needs to implement:

```python
class AgentInterface:
    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        raise NotImplementedError
```

Optional extension points:

```python
class SupportsAgentCapabilities:
    def get_capabilities(self) -> AgentCapabilities: ...


class SupportsAgentDebugState:
    def get_debug_state(self, session_id: str) -> dict: ...
```

`respond(...)` is the only required method. Capabilities and debug state are optional.

## Integration Modes

### Mode A: Wrap an existing prose agent

Use this when your current agent already returns final text.

Best for:

- fast adoption
- minimal changes to the base agent
- preserving current tool logic

What you do:

- create an adapter that converts the agent's output into `ChatResponse`
- let the wrapper decide pacing, disclosure, critique, and resumable flow around it

### Mode B: Wrap a structured-output agent

Use this when your core agent can return facts, options, risks, or summaries separately from final user wording.

Best for:

- stronger disclosure control
- better critique quality
- easier multi-turn planning

Recommended future contract:

- facts discovered
- tool outcomes
- candidate options
- recommended next step
- suggested answer draft

The current codebase can support this through adapter evolution, even though the built-in demo path is still relatively text-first.

## Minimal Example

```python
from conversation_layer.schemas import ChatRequest, ChatResponse
from conversation_layer.services.conversation_wrapper import ConversationWrapper
from conversation_layer.services.agent_interface import AgentCapabilities


class MyAgentAdapter:
    def __init__(self, my_agent) -> None:
        self.my_agent = my_agent

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        text = self.my_agent.reply(chat_request.session_id, chat_request.message)
        return ChatResponse(
            session_id=chat_request.session_id,
            response=text,
            metadata={"source": "my_agent"},
        )

    def get_capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(adapter_name="MyAgentAdapter")


wrapper = ConversationWrapper(
    agent=MyAgentAdapter(my_agent),
    working_memory_store=working_memory_store,
    history_provider=history_provider,
    episodic_summary_store=episodic_summary_store,
)

response = wrapper.handle(
    ChatRequest(session_id="demo", message="What are the updates?")
)
```

## Important Inputs the Wrapper Understands

The wrapper can use request metadata to seed or steer behavior.

### `working_memory_update`

This is the main mechanism for injecting structured state discovered by another system.

Useful fields include:

- `dialogue_phase`
- `user_preference_signals`
- `known_but_undisclosed`
- `pending_questions`
- `conversation_plan`
- `runtime_state`
- `tool_state`
- `suspended_threads`

Example:

```python
ChatRequest(
    session_id="s1",
    message="What are the updates?",
    metadata={
        "working_memory_update": {
            "known_but_undisclosed": [
                {
                    "id": "good_update",
                    "type": "update",
                    "valence": "positive",
                    "summary": "The good news is your promotion was approved.",
                    "detail_ref": "detail_1",
                    "priority": 0.9,
                },
                {
                    "id": "bad_update",
                    "type": "update",
                    "valence": "negative",
                    "summary": "The bad news is your transfer request was denied.",
                    "detail_ref": "detail_2",
                    "priority": 0.8,
                },
            ]
        }
    },
)
```

### `simulate_delay_ms`

Current MVP-only mechanism for demo filler behavior.

### `force_overdisclosure`

Test-only switch used by the deterministic demo adapter.

## What the Wrapper Returns

`ChatResponse` contains:

- `response`: final concatenated assistant text
- `messages`: staged delivery chunks with `delay_ms`
- `metadata`: policy/debug information

Important metadata keys:

- `source`
- `policy_act`
- `policy_reasoning`
- `critique_status`
- `critique_action`
- `disclosed_item_id`
- `interruption_action`
- `resumed_from`
- `filler_used`

## Existing Storage Boundaries

These boundaries matter when integrating with other agents.

### Transcript

- exact user/assistant turns
- file: `core_agent_app/services/history_provider.py`

### Working memory

- active turn state, undisclosed facts, preference signals, resumable state, suspended threads
- file: `conversation_layer/services/working_memory.py`

### Episodic summary

- compressed session trajectory
- file: `conversation_layer/services/episodic_summary.py`

## Dependency Injection Pattern

The normal app-level integration pattern is:

1. construct the raw agent adapter
2. construct transcript/history storage separately
3. construct conversation-layer stores separately
4. inject all of those into `ConversationWrapper(agent=...)`

This keeps the wrapped agent a black box and avoids leaking DB or tool wiring into the reusable layer.

Do not collapse these into one generic blob. The separation is one of the main product ideas.

## Recommended Integration Styles

### Customer support or enterprise copilot

- let the base agent gather facts and tool results
- inject structured findings into `known_but_undisclosed`
- let the wrapper ask preference/order questions before disclosure

### Research or analyst agent

- use the wrapper for pacing and option framing
- preserve partial plans across interruptions
- expose debug state to researchers through `/debug-state`

### Multi-agent platform

- give each specialist agent its own adapter
- keep one shared wrapper contract around them
- standardize state injection into `working_memory_update`

## Operational Commands

### Run tests

```bash
conda activate test
PYTHONPATH="/home/rishabh/github_projects/chanakya_conversation_layer" python -m pytest
```

### Run evaluation harness

```bash
conda activate test
python -m app.evaluation
```

### Run Flask app

```bash
conda activate test
flask --app app run
```

## Debug and Inspection Endpoints

- `/health`
- `/chat`
- `/sessions/<session_id>/history`
- `/sessions/<session_id>/working-memory`
- `/sessions/<session_id>/episodic-summary`
- `/sessions/<session_id>/debug-state`

## Current Limits Other Agent Teams Should Know

- dialogue policy is still rule-based
- several PRD-listed acts are not first-class yet: `ACKNOWLEDGE`, `OFFER_OPTIONS`, `CHECK_READINESS`, `REPAIR`
- long-term memory is not implemented
- interruption recovery is heuristic, not semantic retrieval
- filler is simulated for delayed demo flows, not tied to real tool progress
- critique pass focuses on obvious over-disclosure, not broad style editing

## Recommended Next Integration Upgrade

If another team wants to adopt this wrapper seriously, the best next upgrade is to change the core-agent adapter contract from plain final prose to structured intermediate outputs. That will make disclosure planning, critique, and agent portability much stronger.
