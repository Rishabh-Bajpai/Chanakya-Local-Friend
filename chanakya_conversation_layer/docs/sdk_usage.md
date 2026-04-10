# Using the Conversation Layer as an SDK

The `conversation_layer` package provides a reusable conversational intelligence layer that sits on top of any core agent. It provides response-scoped working memory, delayed delivery chunking, intelligent topic queueing, conversational continuity planning, and interruption handling.

It is designed to be easily integrated into any Python application, either by wrapping a custom local agent or an external Agent-to-Agent (A2A) model.

---

## Installation

The project uses `pyproject.toml`. You can install the package directly into your virtual environment:

```bash
pip install .
```

If you need development dependencies (for testing):

```bash
pip install -e .[dev]
```

---

## Core Classes & Architecture

The layer is designed around a clean protocol and a wrapper object. Below are all the core classes you will interact with.

### 1. `AgentInterface`

This is the protocol your core agent must implement. The Conversation Layer doesn't care how your agent works, as long as it accepts a `ChatRequest` and returns a `ChatResponse`.

```python
from conversation_layer.schemas import ChatRequest, ChatResponse

class AgentInterface:
    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        ...
```

### 2. `ConversationWrapper`

The `ConversationWrapper` is the main entry point. It wraps your `AgentInterface` implementation and intercepts calls to handle conversational state, working memory, and delivery planning.

Instead of instantiating it directly, it is highly recommended to use the provided `with_conversation_layer` convenience function.

```python
from conversation_layer.integration import with_conversation_layer

wrapped_agent = with_conversation_layer(
    agent=my_agent,
    orchestration_agent=orchestration_agent,
    state_store=memory_store,      # Optional
    history_provider=history_store # Optional
)
```

### 3. `MAFOrchestrationAgent`

The conversation layer needs its own LLM access to perform tasks like routing (deciding if the queue should be kept or cleared) and delivery planning (chunking text for human reading). This is handled by passing a configured `MAFOrchestrationAgent`.

This agent uses Microsoft Agent Framework (`Agent` and `OpenAIChatClient`) under the hood to output strict JSON schemas determining the conversation flow.

### 4. Memory Stores (`ResponseStateStore`)

The layer maintains the conversation's active "working memory" during an ongoing topic. There are two primary implementations:

- **`InMemoryResponseStateStore`** (Default): Stores state in a Python dictionary. Good for single-process applications.
- **`RedisResponseStateStore`**: Stores state in Redis. Required for multi-process (e.g., standard Flask/Gunicorn deployments) or distributed environments.

---

## Full Guide: Memories & Storage

Understanding how Chanakya handles memory is crucial. It uses a dual-memory system:

### 1. Core History Transcript (Long-term)
This is the standard chat history (User says X, Assistant says Y).
- **Managed by:** `history_provider` (passed to `with_conversation_layer`).
- **Scope:** The entire lifetime of the `session_id`.
- **Usage:** This is what the *Core Agent* looks at to understand the user's historical context. The conversation layer can optionally append to this automatically if provided.

### 2. Response-Scoped Working Memory (Short-term)
This is the internal state of the current "topic" or "response". Because the conversation layer chunks long responses and delivers them over time (delayed delivery), it needs to remember what it has delivered so far, what is pending in the queue, and how to handle interruptions.
- **Managed by:** `ResponseStateStore` (`InMemoryResponseStateStore` or `RedisResponseStateStore`).
- **Scope:** A single active topic. Once a topic changes (e.g. user asks a completely new question), this memory is cleared and reset.
- **Usage:** The `MAFOrchestrationAgent` uses this to route messages.

#### How Routing Works (The WM Manager)
When a user sends a message, the `ConversationWrapper` checks the Working Memory.
1. If the user says "Next" (an `ack_continue`), the `MAFOrchestrationAgent` looks at the Working Memory, sees there are pending items in the queue, and decides to **preserve the queue** without calling the Core Agent.
2. If the user asks a new question, the `MAFOrchestrationAgent` decides to **reset** the Working Memory and call the Core Agent for a fresh response.
3. If the user interrupts with a constraint ("Don't make the next joke about dogs"), the Orchestrator preserves the delivered memory, calls the Core Agent for a new response, and replaces the remaining queue.

---

## Integrating a Local MAF Agent

Here is a complete example of wrapping a local Microsoft Agent Framework (MAF) agent.

```python
import os
from agent_framework import OpenAIChatClient, Agent, Message
from conversation_layer.schemas import ChatRequest, ChatResponse
from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent
from conversation_layer.integration import with_conversation_layer
from conversation_layer.services.working_memory import InMemoryResponseStateStore

# 1. Define your Core Agent implementing AgentInterface
class MyLocalMAFAgent:
    def __init__(self):
        client = OpenAIChatClient(
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            model_id=os.environ.get("OPENAI_CHAT_MODEL_ID"),
        )
        self.agent = Agent(client=client, name="MyCoreAgent", instructions="You are a helpful assistant.")

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        # Note: MAF >= 1.0 requires Message(role, [content])
        import asyncio
        result = asyncio.run(self.agent.run(chat_request.message))

        return ChatResponse(
            session_id=chat_request.session_id,
            response=result.text,
            messages=[],
            metadata={"source": "local_maf"}
        )

# 2. Configure the Orchestration Agent for the Conversation Layer
chat_client = OpenAIChatClient(
    base_url=os.environ.get("CONVERSATION_OPENAI_BASE_URL", os.environ.get("OPENAI_BASE_URL")),
    api_key=os.environ.get("CONVERSATION_OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY")),
    model_id=os.environ.get("CONVERSATION_OPENAI_CHAT_MODEL_ID", os.environ.get("OPENAI_CHAT_MODEL_ID")),
)
orchestration_agent = MAFOrchestrationAgent(
    model=chat_client.model_id,
    base_url=chat_client.base_url,
    api_key=chat_client.api_key,
    env_file_path=""
)

# 3. Create the Wrapper
my_core_agent = MyLocalMAFAgent()
memory_store = InMemoryResponseStateStore()

wrapped_agent = with_conversation_layer(
    agent=my_core_agent,
    orchestration_agent=orchestration_agent,
    state_store=memory_store
)

# 4. Use the wrapped agent
response = wrapped_agent.handle(ChatRequest(session_id="user-123", message="Write a long poem."))
print("Initial Delivery:", response.response)
```

---

## Integrating an A2A Agent

Integrating an A2A agent is functionally identical to the Local MAF approach. The Conversation Layer does not know the difference. The only change is in how your `AgentInterface` adapter is implemented.

```python
from agent_framework_a2a.client import A2AAgentClient
from conversation_layer.schemas import ChatRequest, ChatResponse
from conversation_layer.integration import with_conversation_layer

# 1. Define your Core Agent implementing AgentInterface for A2A
class MyA2AAgentAdapter:
    def __init__(self, endpoint_url: str):
        self.client = A2AAgentClient(endpoint_url=endpoint_url)

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        result = self.client.run(
            user_message=chat_request.message,
            session_id=chat_request.session_id
        )
        return ChatResponse(
            session_id=chat_request.session_id,
            response=result.response,
            metadata={"source": "a2a"}
        )

# 2. Create the Adapter
a2a_adapter = MyA2AAgentAdapter("http://localhost:18770")

# 3. Create the Wrapper (Using the exact same orchestration_agent from above)
wrapped_a2a_agent = with_conversation_layer(
    agent=a2a_adapter,
    orchestration_agent=orchestration_agent, # Same as Local MAF example
)

# 4. Use the wrapped A2A agent
response = wrapped_a2a_agent.handle(ChatRequest(session_id="user-123", message="Write a long poem."))
print("Initial Delivery:", response.response)
```

---

## Managing State and Delivery Queue

Because the conversation layer chunks long responses into a queue for delayed delivery, your application needs to handle polling or requesting the next message.

### Requesting the Next Message

When a response indicates there are pending messages (`response.metadata["pending_delivery_count"] > 0`), the host app should periodically request the next message.

```python
delivery_status = wrapped_agent.deliver_next_message(session_id="user-123")

if delivery_status["status"] == "delivered":
    # A new message chunk is ready to be sent to the user
    message = delivery_status["message"]
    print("New message chunk:", message["text"])

elif delivery_status["status"] == "waiting":
    # The message is not yet ready (respecting delay_ms)
    print(f"Wait {delivery_status['retry_after_ms']} ms")

elif delivery_status["status"] == "idle":
    # The queue is empty
    pass
```

### Pausing Delivery

If the user wants to pause the ongoing delivery of a multi-part response:

```python
wrapped_agent.request_manual_pause(session_id="user-123")
```

When paused, `deliver_next_message` will return `{"status": "paused"}` until a new interaction resumes the flow.

### Debugging State

You can inspect the working memory and the current queue for a session at any time:

```python
debug_info = wrapped_agent.list_debug_view(session_id="user-123")
print(debug_info)
```
