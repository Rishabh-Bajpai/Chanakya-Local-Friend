# Issue 6: Support MAF A2A Connector for External Coding Agent Servers

## Problem

The long-term goal is to use the conversation layer with external coding-agent servers that speak A2A, such as servers behind OpenCode, Codex, and Claude Code.

Right now the app is built around a local in-process core agent adapter:

- `AgentFrameworkCoreAgentAdapter`
- local tool wiring
- local history provider assumptions
- local app-owned config/bootstrap

That makes it difficult to plug the conversation layer into remote agent systems accessed through MAF's A2A connector.

If we want the conversation layer to be reusable across many agent backends, it should work with:

1. local/in-process agents
2. remote agents accessed through an A2A-compatible transport

## Goal

Add architectural support for using the conversation layer with MAF's A2A connector so it can wrap remote coding-agent servers that support A2A.

Examples of target backends include:
- OpenCode-compatible coding agent server
- Codex-compatible coding agent server
- Claude Code-compatible coding agent server

The conversation layer should not care whether the underlying agent is local or remote. It should interact through a stable agent interface and let the A2A adapter handle transport/protocol details.

The recent `test` repo validates a few concrete patterns that should inform this work:
- a single chat service can switch between a local MAF agent and a MAF `A2AAgent`
- remote multi-turn continuity can be preserved by storing and reusing the returned remote `context_id`
- when remote continuity fails, the app can fall back to a seeded transcript excerpt instead of failing outright

## Desired Architecture

```python
ConversationWrapper
    -> AgentInterface
        -> AgentFrameworkCoreAgentAdapter   # local/in-process
        -> A2ACoreAgentAdapter              # remote via MAF A2A connector
```

The key design principle is:

- the conversation layer owns orchestration, working memory, summaries, and wrapper behavior
- the agent adapter owns how requests are sent to the underlying agent system
- A2A transport/protocol details stay outside the conversation layer core

## Why This Matters

1. It makes the conversation layer usable with remote coding agents, not just embedded ones.
2. It aligns with the goal of keeping the agent as a black box.
3. It reduces lock-in to the current `AgentFrameworkCoreAgentAdapter`.
4. It allows one conversation layer to work across multiple coding-agent products.
5. It makes future side-by-side comparison possible: local agent vs remote A2A agent.

## Integration Requirements

### 1. Stable Agent Interface

The work in `issue_1.md` becomes important here.

We need a stable wrapper-facing interface that supports at least:
- sending a user turn to the agent
- receiving the agent response in a normalized format
- optionally retrieving debug/introspection data if supported

The conversation layer should not depend directly on:
- MAF connector implementation details
- A2A wire payload shapes
- remote server-specific schemas

### 2. A2A-Specific Adapter

Create an adapter such as `A2ACoreAgentAdapter` that:
- implements the same wrapper-facing interface as local adapters
- uses MAF's A2A connector under the hood
- translates between app `ChatRequest` / `ChatResponse` and remote A2A messages
- handles remote errors, timeouts, and malformed responses safely
- preserves remote conversation continuity identifiers such as `context_id` when available

### 3. Remote Capability Awareness

Different coding-agent servers may expose different capabilities.

The adapter should account for differences such as:
- tool execution visibility
- history retrieval support
- memory retrieval/debug support
- streaming vs non-streaming behavior
- metadata richness

The conversation layer should degrade gracefully when a remote A2A agent exposes only limited introspection.

### 4. Remote Conversation Continuity

When the remote A2A agent returns a conversation identifier such as `context_id`, the adapter should preserve and reuse it for later turns in the same app session.

This should be treated as remote-agent state, not conversation-layer working memory.

At minimum, the architecture should make room for:
- storing remote `context_id` per session and agent pairing
- reusing it on follow-up turns
- distinguishing remote context continuity from local transcript/history fallback

### 5. Conversation/Agent Boundary Clarity

The debug dashboard from `issue_2.md` should clearly show:
- what the conversation layer sent to the A2A adapter
- what the adapter sent over A2A
- what the remote agent returned
- what debug state is available locally vs remotely

## Suggested Scope

### Phase 1: Basic A2A Response Path

Support:
- send user message to remote A2A coding agent
- receive final text response
- return normalized `ChatResponse`
- preserve and reuse remote `context_id` when available

This is the minimum viable integration.

### Phase 2: Debug and Trace Visibility

Support:
- remote request/response trace capture
- remote metadata capture
- dashboard visibility into the wrapper-to-A2A boundary

### Phase 3: Rich Introspection

Where supported by the remote server or connector, expose:
- tool traces
- remote history/context
- remote memory/debug state
- model/runtime metadata

This phase should be capability-driven, not assumed.

## Required Changes

### 1. Create an A2A adapter

Add a new adapter, likely in `app/services/core_agent.py` or a future agent-adapter module, for example:

```python
class A2ACoreAgentAdapter(CoreAgentAdapter):
    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        ...
```

### 2. Normalize remote responses

Ensure remote A2A responses are converted into the app's standard response shape, including:
- response text
- metadata
- optional messages/chunks if supported
- remote continuity identifiers such as `context_id` when exposed by the connector

### 3. Add configuration for remote agent selection

The host app should be able to choose between:
- local agent adapter
- A2A agent adapter

Configuration should include items such as:
- remote server endpoint
- connector settings
- auth/token settings if needed
- timeout/retry behavior
- debug flags

### 4. Add capability/introspection hooks

Define optional hooks for remote adapters so the rest of the app can ask for:
- remote debug state
- remote history/context
- remote capability metadata

These should be optional and not required for basic response handling.

### 5. Add continuity fallback behavior

If remote A2A conversation continuity fails, the adapter should support a safe fallback path.

One validated pattern is to retry using a seeded transcript excerpt from recent local history when remote memory continuity is missing or broken.

This should be treated as fallback behavior, not the primary memory strategy.

### 6. Add example integration in the host app

The app should include an example route or configuration path that uses an A2A-backed coding agent through the conversation layer.

Based on the validated pattern in the `test` repo, backend selection should be configuration-driven rather than requiring code edits to swap between local and A2A agents.

### 7. Update tests

Add tests for:
- successful remote response handling
- timeout/error handling
- malformed remote payload handling
- capability-limited agents
- wrapper compatibility with the A2A adapter
- remote `context_id` reuse across turns
- seeded-history fallback when remote continuity fails

## Open Questions

1. What should be the minimum required A2A capability set for a remote coding-agent backend?
2. Should the first version support only request/response, or also streaming?
3. How should remote tool traces be surfaced when servers expose them differently?
4. Should A2A-specific payloads be persisted for debugging, or only normalized traces?
5. What exact continuity identifier shape does the MAF A2A connector expose in the production target integration, and how stable is it across providers?

## Acceptance Criteria

1. **A2A adapter exists**: A dedicated adapter supports remote coding agents via MAF's A2A connector
2. **Conversation layer stays transport-agnostic**: `ConversationWrapper` does not contain A2A-specific logic
3. **Same wrapper interface works**: The wrapper can be used with either a local adapter or an A2A adapter
4. **Remote response path works**: A user message can be sent through the wrapper to a remote A2A coding agent and produce a normalized `ChatResponse`
5. **Remote continuity is supported**: When the connector exposes a remote conversation identifier such as `context_id`, it is persisted and reused across turns for the same session
6. **Errors are handled safely**: Timeouts, transport failures, and invalid remote payloads are handled without crashing the app
7. **Capabilities are optional**: The system works even when the remote A2A agent exposes limited history/debug/tool visibility
8. **Configurable backend selection**: The host app can switch between local and A2A-backed agents through configuration
9. **Debug support exists**: The wrapper-to-agent boundary for A2A requests/responses is visible in the debug dashboard where available
10. **Reusable across coding-agent servers**: The integration is not hard-coded to one specific remote product and can target multiple A2A-compatible coding-agent servers
11. **Fallback continuity exists**: If remote memory continuity fails, the system can fall back safely rather than losing the turn entirely
12. **Tests cover remote integration**: Automated tests cover normal flow and failure cases for the A2A adapter

## Related Issues

- `issues/issue_1.md` - Split conversation layer into an independent stack
- `issues/issue_2.md` - Build a developer debug dashboard for full conversation and agent trace visibility
- `issues/issue_4.md` - Replace hard-coded conversation orchestration with LLM-powered planning
- `issues/issue_5.md` - Add a decorator-based integration API on top of the conversation wrapper
