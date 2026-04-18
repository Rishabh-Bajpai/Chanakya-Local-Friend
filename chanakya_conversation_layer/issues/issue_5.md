# Issue 5: Add a Decorator-Based Integration API on Top of the Conversation Wrapper

## Problem

After splitting the system into two independent parts:

1. `conversation_layer` as a reusable package
2. `core_agent_app` as a host chatbot app

the main integration path should remain explicit and stable:

```python
wrapped_agent = ConversationWrapper(agent)
```

However, if the conversation layer is meant to be adopted across many MAF agents, ergonomics will matter. Requiring every caller to manually instantiate `ConversationWrapper(...)` is clear, but can become repetitive across many agents and apps.

We want an easier adoption path without compromising the architecture.

## Goal

Provide a decorator-based integration API as a convenience layer, while keeping the wrapper object as the primary implementation.

This should allow developers to use the conversation layer in two ways:

### Explicit wrapper usage

```python
agent = SupportAgent(...)
wrapped_agent = ConversationWrapper(agent, conversation_store=store)
```

### Decorator convenience usage

```python
@with_conversation_layer()
class SupportAgent:
    ...
```

or potentially:

```python
@with_conversation_layer(store_factory=my_store_factory)
class SupportAgent:
    ...
```

The decorator should not become the core implementation. It should be a thin convenience layer around `ConversationWrapper`.

## Why This Matters

1. It improves adoption across many MAF agents
2. It keeps the architecture clean by preserving wrapper-first design
3. It gives app developers a more ergonomic API for simple use cases
4. It avoids duplicating wrapper logic in each host app
5. It makes the conversation layer feel like a reusable product rather than app-specific glue

## Design Principles

### 1. Wrapper-First Architecture

`ConversationWrapper` remains the canonical implementation.

The decorator must internally construct or apply the wrapper rather than implementing separate conversation logic.

### 2. Minimal Agent Assumptions

The decorator should work with agents that conform to the expected interface/protocol. It should not assume agent internals such as:
- DB implementation
- tool registration internals
- hidden state layout
- routing implementation details

### 3. Explicit Configuration

The decorator should support explicit configuration for things like:
- conversation store / storage backend
- policy engine selection
- optional working memory settings
- optional enable/disable flags

### 4. Low Surprise API

Using the decorator should not obscure how the system behaves.

A developer should still be able to understand that:
- the original agent exists
- the conversation layer wraps the agent
- the wrapper owns conversation-layer state

## Proposed API Options

### Option A: Class decorator

```python
@with_conversation_layer()
class SupportAgent(BaseMAFAgent):
    ...
```

Possible behavior:
- decorate the class constructor so instances are returned already wrapped, or
- attach a helper method such as `.with_conversation_layer()`

### Option B: Instance decorator/factory

```python
agent = SupportAgent(...)
wrapped_agent = with_conversation_layer(agent, conversation_store=store)
```

This is simpler and more explicit than a class decorator, and may be easier to debug.

### Option C: Both class and instance APIs

```python
wrapped_agent = with_conversation_layer(agent)

@with_conversation_layer()
class SupportAgent:
    ...
```

This provides maximum flexibility, but only if implementation complexity stays low.

## Recommendation

Implement the convenience API in this order:

1. **Primary**: `ConversationWrapper(agent, ...)`
2. **First convenience API**: `with_conversation_layer(agent, ...)`
3. **Optional later enhancement**: class decorator form `@with_conversation_layer(...)`

Reasoning:
- The wrapper object is the most explicit and stable abstraction
- A function-based convenience wrapper is easier to implement and reason about than a class decorator
- A class decorator may introduce surprises around type identity, construction, and debugging

## Required Changes

### 1. Define the public integration API

Create a public API for applying the conversation layer to an agent instance, for example:

```python
def with_conversation_layer(
    agent: AgentInterface,
    *,
    conversation_store=None,
    policy_engine=None,
    config=None,
) -> ConversationWrapper:
    ...
```

### 2. Keep wrapper as the implementation source of truth

The convenience API must delegate to `ConversationWrapper`.

It must not duplicate:
- working memory orchestration
- summary generation
- policy decisions
- disclosure planning

### 3. Decide whether to support class decorator form now or later

If class decorator support is added now, define exactly how it behaves:
- Does instantiating the class return a wrapped instance?
- Does it add a helper method?
- Does it preserve access to the original underlying agent?

If class decorator support is deferred, document that clearly.

### 4. Ensure wrapped agents remain inspectable

The wrapped result should make it easy to access:
- the underlying raw agent
- wrapper-owned state/services if needed
- a stable method like `respond()` or `execute()`

### 5. Add host-app examples

In the example/core app, show:
- one raw agent path
- one explicit wrapper path
- one convenience API path if implemented

## Open Questions

1. Should the decorator return a `ConversationWrapper` instance directly, or a proxy object?
2. Should the public convenience API be instance-only at first?
3. What is the canonical public method name for agents: `execute()`, `respond()`, or something else?
4. How much configuration should be exposed in the convenience API versus constructor injection?

## Acceptance Criteria

1. **Wrapper remains primary**: `ConversationWrapper` is still the core implementation and source of truth
2. **Convenience API exists**: There is a supported helper such as `with_conversation_layer(agent, ...)`
3. **No duplicated orchestration logic**: The convenience API delegates to the wrapper instead of reimplementing it
4. **Agent black-box principle preserved**: The convenience API does not assume agent DB/schema/tool internals
5. **Usable across many MAF agents**: Multiple different MAF agents can adopt the conversation layer with minimal integration code
6. **Configuration is explicit**: Storage/policy/configuration can be passed in without hidden global coupling
7. **Debuggable behavior**: It is still easy to identify and access the underlying raw agent when needed
8. **Host app demonstrates usage**: The example app includes a raw path and at least one wrapped integration path using the new API
9. **Decorator behavior is documented**: If class decorator support exists, its lifecycle and return behavior are clearly documented
10. **Tests cover integration paths**: Tests validate explicit wrapper usage and any implemented convenience/decorator API

## Related Issues

- `issues/issue_1.md` - Split conversation layer into an independent stack
- `issues/issue_4.md` - Replace hard-coded conversation orchestration with LLM-powered planning
