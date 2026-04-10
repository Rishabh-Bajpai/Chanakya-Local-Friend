# Chanakya Conversation Layer

Chanakya is now a slim Flask app with:

- one core agent
- one thin conversation delivery layer
- one persistent core-agent transcript history keyed by `session_id`
- one response-scoped in-memory working memory for the conversation layer

The current implementation uses Microsoft Agent Framework with `Agent`, `Message`, `OpenAIChatClient`, optional `A2AAgent`, and a custom `SQLAlchemyHistoryProvider`.

## Current architecture

- `conversation_layer/`: slim response-scoped delivery logic, WM-manager LLM call, and conversation planner LLM call
- `core_agent_app/`: Flask app, core-agent adapters, transcript storage, and routes
- `a2a_example_app/`: example OpenCode/A2A bridge app kept separate from the main app

## What is implemented

- Flask app with a browser UI at `/`
- main API routes:
  - `/health`
  - `/chat`
  - `/sessions/<session_id>/history`
  - `/sessions/<session_id>/working-memory`
  - `/sessions/<session_id>/pause`
  - `/sessions/<session_id>/next-message`
  - `/sessions/<session_id>/debug-state`
- one local core-agent adapter using Agent Framework + OpenAI-compatible model
- one A2A core-agent adapter using Agent Framework A2A
- SQLAlchemy-backed core history only
- response-scoped in-memory conversation working memory only
- one WM-manager LLM call for routing/replanning
- one core-agent call when needed
- one human-conversation planner LLM call for staged delivery
- backend-owned delayed assistant delivery with interruption support
- manual pause for the next queued assistant message
- core-agent tools:
  - current UTC time
  - web search
  - URL fetch

## Configuration

The app reads settings from `.env` at the project root.

Required values:

```bash
OPENAI_BASE_URL="http://192.168.1.51:1234/v1"
OPENAI_CHAT_MODEL_ID="google/gemma-4-26b-a4b"
OPENAI_API_KEY="lm-studio"
DATABASE_URL="sqlite:////home/rishabh/github_projects/chanakya_conversation_layer/chanakya.db"
CHANAKYA_DEBUG=true
```

Optional conversation-layer model values:

```bash
CONVERSATION_OPENAI_BASE_URL="http://192.168.1.51:1234/v1"
CONVERSATION_OPENAI_CHAT_MODEL_ID="google/gemma-4-26b-a4b"
CONVERSATION_OPENAI_API_KEY="lm-studio"
```

If omitted, the conversation-layer planner defaults to the core agent's OpenAI settings.

Optional core-agent backend values:

```bash
CORE_AGENT_BACKEND="local"  # or a2a
A2A_AGENT_URL="http://127.0.0.1:18770"
```

When `CORE_AGENT_BACKEND="a2a"`, `A2A_AGENT_URL` is required.

## Setup

```bash
conda activate test
python -m pip install -e .[dev]
```

## Run

Start just the main app:

```bash
./start_stack.sh app
```

Start the example OpenCode/A2A bridge only:

```bash
./start_stack.sh a2a
```

Start both the main app and the example OpenCode/A2A bridge:

```bash
./start_stack.sh app+a2a
```

Stop everything started by these scripts:

```bash
./stop_stack.sh
```

Manual app-only run:

```bash
conda activate test
flask --app app run
```

Then open `http://127.0.0.1:5000/` to use the chat UI.

## Example requests

Health check:

```bash
curl http://127.0.0.1:5000/health
```

Chat request:

```bash
curl -X POST http://127.0.0.1:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"My name is Rishabh."}'
```

The JSON response includes:

- `response`: concatenated final assistant text for compatibility
- `messages`: ordered assistant message chunks with `delay_ms`
- `metadata`: current routing/planner/debug fields

## SDK Usage

To use the conversation layer in your own projects as an SDK (wrapping any local or A2A agent), see the detailed guide: **[Using the Conversation Layer as an SDK](docs/sdk_usage.md)**.

## Integration API

The primary implementation remains explicit wrapper construction:

```python
from conversation_layer import ConversationWrapper

wrapped_agent = ConversationWrapper(
    agent,
    history_provider=history_provider,
)
```

For host apps that want less repeated wiring, the package also exposes a thin convenience helper:

```python
from conversation_layer import with_conversation_layer

wrapped_agent = with_conversation_layer(
    agent,
    history_provider=history_provider,
)
```

`with_conversation_layer(...)` returns a normal `ConversationWrapper`, so the raw agent remains inspectable as `wrapped_agent.agent`.

Inspect transcript history:

```bash
curl http://127.0.0.1:5000/sessions/demo/history
```

Inspect working memory:

```bash
curl http://127.0.0.1:5000/sessions/demo/working-memory
```

Inspect aggregated debug state:

```bash
curl http://127.0.0.1:5000/sessions/demo/debug-state
```

## Tests

```bash
conda activate test
pytest tests/test_history_provider.py tests/test_conversation_wrapper.py tests/test_a2a_adapter.py
```

Current focused coverage includes:

- history persistence and rewrite behavior
- slim conversation wrapper routing
- backend queue delivery
- interruption replanning
- manual pause
- A2A context reuse and fallback handling

## Stable Agent Interface

The required core-agent contract remains small:

```python
from conversation_layer.schemas import ChatRequest, ChatResponse


class AgentInterface:
    def respond(self, chat_request: ChatRequest) -> ChatResponse: ...
```

Transcript/history access remains a separate injected dependency and is not required of every wrapped agent.

## Key files

- `core_agent_app/__init__.py`: host app factory and dependency wiring
- `conversation_layer/services/conversation_wrapper.py`: wrapper pipeline and stable agent boundary
- `conversation_layer/services/agent_interface.py`: required and optional agent protocols
- `core_agent_app/services/core_agent.py`: Agent Framework adapter
- `core_agent_app/services/history_provider.py`: SQLAlchemy transcript storage
- `core_agent_app/routes.py`: raw and wrapped HTTP routes
- `tests/`: route, wrapper, and transcript tests
