# Slim Conversation Layer PRD

## Goal

Refactor the app into a much simpler system where the conversation layer is only a delivery/manipulation layer in front of the core agent.

The conversation layer must not behave like a second full assistant.

## Target Product Behavior

The system should have only three LLM calls in the main path:

1. one working-memory management call
2. one core-agent call
3. one human-conversation planning call

The conversation layer should decide whether to:

1. answer from current-response working memory
2. or call the core agent

The conversation layer should then convert the final answer into more human-like multi-step delivery.

## Core Constraints

1. Working memory is only for the current response lifecycle, not the whole session.
2. Working memory must be refreshed for every new response.
3. Working memory must not retain old core-agent responses.
4. The conversation layer should not have long-term memory.
5. The conversation layer should not have episodic memory.
6. The conversation layer should not have its own persistent database.
7. `session_id` is only needed for the core agent side.
8. User conversation preferences should be provided through the WM/system prompt context, not stored in a new conversation-layer DB.
9. Once a core-agent call happens, the old WM queue must be cleared and replaced with a new queue for the new response.
10. Before the core-agent call, the core-agent-visible chat history should reflect the latest human-conversation-layer delivered dialog rather than raw prior core responses.
11. The queue should not be persisted.
12. All timing and delivery logic should happen in the backend, not in the UI.

## Required Simplification

Remove the current overbuilt conversation stack concepts from the live path:

1. long-term user profiles
2. episodic summaries
3. critique pass
4. disclosure planner
5. resume manager
6. topic scope style
7. orchestration mode matrix
8. post-processing-only runtime path
9. separate debug dashboard product surface
10. conversation-layer persistence

## Runtime Model

### Core Agent

The core agent remains the real agent.

It should:

1. keep its own session-based history
2. support local transport and A2A transport
3. expose more tools for complex tasks, including web fetch and free web search support where available
4. not contain conversation-layer instructions in its system prompt
5. not contain hardcoded behavioral hacks like topic style selection

### Conversation Layer

The conversation layer should only:

1. inspect the incoming user message
2. inspect the current in-memory delivery queue
3. run a WM-management LLM call
4. decide whether the response can be produced from WM or whether the core agent is needed
5. call the core agent if needed
6. run a human-conversation planning LLM call to split the final answer into sequential human-style messages
7. keep an in-memory queue of upcoming assistant messages
8. deliver those messages one by one from the backend
9. remove each message from WM immediately after it is delivered
10. adjust the remaining queue when the user interrupts

## Working Memory Shape

Working memory should be reduced to current-response state only.

Suggested fields:

1. current user message
2. latest visible assistant messages for the active response
3. current pending delivery queue
4. delivery status
5. interruption status
6. user conversation preferences injected for planning
7. latest core-agent response for the active response only

Working memory should be plain in-memory process state.

## Queue and Delivery Behavior

The human-conversation planner should output multiple sequential assistant messages.

Delivery rules:

1. first message can be immediate
2. next messages should wait 5 seconds each by default
3. backend owns the waiting and release of queued messages
4. UI should request or receive the next backend-approved message rather than simulate delay itself
5. when a queued message is delivered, delete it from the queue immediately
6. when a new user message arrives before the queue finishes, pending future messages should be paused or discarded and replanned
7. a manual UI button should also allow pausing the next queued delivery

## Interruption Behavior

Interruptions should be simple.

When the user speaks before the next queued message is delivered:

1. pause pending delivery
2. run WM-management again using the new user message and remaining queue
3. decide whether to continue from WM or call the core agent again
4. generate a new human-conversation queue if needed

The manual pause button should trigger the same pause path even if the user has not typed yet.

## History Model

There should be one meaningful transcript shown in the main UI for the core agent history.

That history should reflect the humanized delivered assistant conversation, not raw old core-agent answers.

The conversation layer should not maintain a separate persistent transcript store.

## UI Scope

Keep only a simple main UI.

It should show:

1. the current core-agent raw response for the active turn
2. the current in-memory WM values
3. one scrollable list for the core-agent chat history
4. a button to pause next queued delivery

Do not preserve the current debug dashboard.

## Storage Scope

Keep storage only where needed for the core agent.

The conversation layer should not have a DB.

This means:

1. no conversation DB
2. no episodic summary tables
3. no user profile tables
4. no evaluation/reporting tables as part of the runtime product path

## Startup Surface

Provide one simple way to run the main app and related services.

The app should also keep A2A support in scope.

## Out of Scope For This Refactor

1. preserving old wrapper abstractions for compatibility
2. preserving the debug dashboard
3. preserving post-processing-only mode
4. preserving rule-heavy conversation heuristics
5. preserving conversation-layer persistent memory

## Success Criteria

1. a simple request does not trigger multiple extra conversation-layer LLM calls beyond the three-call budget
2. the core-agent prompt is clean and core-agent-specific
3. no hardcoded demo behavior remains in the live path
4. the conversation layer has no persistent DB
5. pending assistant messages are delivered from the backend in sequence
6. interruptions and manual pause both stop the next queued delivery and replan correctly
7. the main UI is simpler and shows only the necessary runtime state
