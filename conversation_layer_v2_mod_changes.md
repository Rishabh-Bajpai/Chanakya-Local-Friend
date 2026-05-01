# Conversation Layer V2_mod Changes

This file records the compatibility changes applied to `chanakya_conversation_layer_new` to produce `V2_mod`.

Use this as the replay guide for applying the same changes later in the original V2 repo.

## Scope

Applied to:

- `chanakya_conversation_layer_new/core_agent_app/config.py`
- `chanakya_conversation_layer_new/conversation_layer/services/orchestration_agent.py`
- `chanakya_conversation_layer_new/a2a_example_app/opencode_a2a_bridge.py`
- `chanakya_conversation_layer_new/tests/test_orchestration_agent.py`
- `chanakya_conversation_layer_new/tests/test_opencode_bridge.py`

## Why These Changes Exist

These are the V1_mod compatibility deltas that were still missing from V2 and were intentionally re-applied:

1. Support repo-root exported `ENV_FILE_PATH`
2. Restore A2A-capable orchestration planning support
3. Restore robust nested response text extraction in the OpenCode A2A bridge
4. Add regression tests for both behaviors

## 1. `core_agent_app/config.py`

Purpose:

- Let the conversation-layer app honor the environment file path injected by the parent stack scripts.

Applied change:

```diff
@@
-    env_file_path: str = str(PROJECT_ROOT / ".env")
+    env_file_path: str = os.getenv("ENV_FILE_PATH", str(PROJECT_ROOT / ".env"))
```

Result:

- V2_mod now reads the repo-root exported `ENV_FILE_PATH` when present.

## 2. `conversation_layer/services/orchestration_agent.py`

Purpose:

- Restore the planner's ability to run through an A2A backend instead of only the OpenAI-compatible local path.
- Preserve remote planner target/model routing through `[[opencode-options:...]]` headers.

Applied structural additions:

```diff
@@
 class MAFOrchestrationAgent:
     model: str
     base_url: str
     api_key: str
     env_file_path: str
+    backend: str = "openai_compatible"
     debug: bool = False
     runner: Callable[[str], Any] | None = None
+    remote_agent_url: str = ""
+    default_remote_agent: str | None = None
+    default_model_provider: str | None = None
+    default_model_id: str | None = None
+    a2a_agent_factory: Any | None = None
     _agent: Agent | None = field(init=False, default=None, repr=False)
     _agent_by_model: dict[str, Agent] = field(
         init=False, default_factory=dict, repr=False
     )
+    _a2a_agent: Any | None = field(init=False, default=None, repr=False)
+    _a2a_sessions: dict[str, Any] = field(
+        init=False, default_factory=dict, repr=False
+    )
```

Applied `__post_init__` behavior:

```diff
@@
     def __post_init__(self) -> None:
         if self.runner is None:
-            client = OpenAIChatClient(
-                model_id=self.model,
-                api_key=self.api_key,
-                base_url=self.base_url,
-                env_file_path=self.env_file_path,
-            )
-            self._agent = Agent(
-                client=client,
-                name="ConversationLayerPlanner",
-                description="Structured orchestration planner for the conversation layer.",
-                instructions=(
-                    "You are a planning agent for a conversation orchestration layer. "
-                    "Return only valid JSON that matches the requested schema. "
-                    "Do not include markdown fences or prose outside the JSON object."
-                ),
-            )
+            if self.backend == "a2a":
+                if self.a2a_agent_factory is None:
+                    from agent_framework_a2a import A2AAgent
+
+                    self.a2a_agent_factory = A2AAgent
+                self._a2a_agent = self.a2a_agent_factory(
+                    name="ConversationLayerPlanner",
+                    description="Structured orchestration planner for the conversation layer.",
+                    url=self.remote_agent_url,
+                )
+            else:
+                client = OpenAIChatClient(
+                    model_id=self.model,
+                    api_key=self.api_key,
+                    base_url=self.base_url,
+                    env_file_path=self.env_file_path,
+                )
+                self._agent = Agent(
+                    client=client,
+                    name="ConversationLayerPlanner",
+                    description="Structured orchestration planner for the conversation layer.",
+                    instructions=(
+                        "You are a planning agent for a conversation orchestration layer. "
+                        "Return only valid JSON that matches the requested schema. "
+                        "Do not include markdown fences or prose outside the JSON object."
+                    ),
+                )
```

Applied sync execution change:

```diff
@@
     def _run(self, prompt: str, *, model_override: str | None = None) -> str:
         if self.runner is not None:
             result = self.runner(prompt)
+        elif self.backend == "a2a":
+            result = asyncio.run(self._run_a2a(prompt, model_override=model_override))
         else:
             agent = self._agent_for_model(model_override)
             result = asyncio.run(agent.run(prompt))
```

Applied async execution change:

```diff
@@
     async def _run_async(self, prompt: str) -> str:
         if self.runner is not None:
             result = self.runner(prompt)
+        elif self.backend == "a2a":
+            result = await self._run_a2a(prompt)
         else:
             result = await self._agent.run(prompt)
```

Added A2A run helper:

```diff
@@
+    async def _run_a2a(self, prompt: str, *, model_override: str | None = None) -> Any:
+        if self._a2a_agent is None:
+            raise OrchestrationAgentError("A2A orchestration agent is not initialized")
+        session = self._a2a_session_for_model(model_override)
+        message = self._build_a2a_prompt(prompt, model_override=model_override)
+        from agent_framework import Message
+
+        return await self._a2a_agent.run(
+            [Message(role="user", text=message)],
+            session=session,
+        )
```

Added A2A session/model helpers:

```diff
@@
+    def _a2a_session_for_model(self, model_override: str | None):
+        model_id = str(model_override or "").strip() or str(
+            self.default_model_id or ""
+        ).strip()
+        session_key = model_id or "default"
+        session = self._a2a_sessions.get(session_key)
+        if session is not None:
+            return session
+        if self._a2a_agent is None:
+            raise OrchestrationAgentError("A2A orchestration agent is not initialized")
+        created = self._a2a_agent.create_session(
+            session_id=f"conversation-layer-planner:{session_key}"
+        )
+        self._a2a_sessions[session_key] = created
+        return created
+
+    def _build_a2a_prompt(self, prompt: str, *, model_override: str | None) -> str:
+        header_parts: list[str] = []
+        remote_agent = str(self.default_remote_agent or "").strip()
+        model_provider = str(self.default_model_provider or "").strip()
+        model_id = str(model_override or self.default_model_id or "").strip()
+        if remote_agent:
+            header_parts.append(f"agent={remote_agent}")
+        if model_provider and model_id:
+            header_parts.append(f"model_provider={model_provider}")
+            header_parts.append(f"model_id={model_id}")
+        if not header_parts:
+            return prompt
+        return f"[[opencode-options:{';'.join(header_parts)}]]\n{prompt}"
```

Important detail:

- The A2A message call uses `Message(role="user", text=message)`.
- This is intentional and came from the last V1_mod fix.

## 3. `a2a_example_app/opencode_a2a_bridge.py`

Purpose:

- Make bridge reply extraction resilient to nested payload shapes returned by the OpenCode server.

Applied `_collect_text(...)` rewrite:

```diff
@@
-def _collect_text(parts: list[dict[str, Any]]) -> str:
+def _collect_text(payload: Any) -> str:
     texts: list[str] = []
-    for part in parts:
-        if isinstance(part, dict) and part.get("type") == "text":
-            text = part.get("text")
-            if isinstance(text, str) and text.strip():
-                texts.append(text)
-    return "\n".join(texts).strip()
+
+    def collect(value: Any) -> None:
+        if value is None:
+            return
+        if isinstance(value, str):
+            stripped = value.strip()
+            if stripped:
+                texts.append(stripped)
+            return
+        if isinstance(value, dict):
+            part_type = str(value.get("type") or "").strip().lower()
+            if part_type == "text":
+                collect(value.get("text"))
+                return
+            if "text" in value and len(value) == 1:
+                collect(value.get("text"))
+                return
+            for key in ("parts", "artifacts", "root", "content", "message", "messages"):
+                if key in value:
+                    collect(value.get(key))
+            return
+        if isinstance(value, (list, tuple)):
+            for item in value:
+                collect(item)
+            return
+        for attr in ("parts", "artifacts", "root", "content", "message", "messages", "text"):
+            nested = getattr(value, attr, None)
+            if nested is not None and nested is not value:
+                collect(nested)
+
+    collect(payload)
+    return "\n".join(dict.fromkeys(texts)).strip()
```

Applied call site change:

```diff
@@
-            reply = (
-                _collect_text(message.get("parts", []))
-                or "OpenCode returned no text parts."
-            )
+            reply = _collect_text(message) or "OpenCode returned no text parts."
```

Result:

- V2_mod can extract text from nested `parts`, `artifacts`, `root`, `content`, `message`, and `messages` payloads.
- Duplicate collected strings are de-duplicated while preserving order.

## 4. Added Test: `tests/test_orchestration_agent.py`

Purpose:

- Verify the A2A planner path works.
- Verify the generated prompt carries the `[[opencode-options:...]]` header.
- Verify session IDs are keyed by model override.

Added file contents:

```python
from __future__ import annotations

from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent


class _FakeA2AResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeA2ASession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _FakeA2AAgent:
    def __init__(self, **kwargs) -> None:
        self.calls: list[dict[str, object]] = []

    def create_session(self, *, session_id: str | None = None):
        return _FakeA2ASession(session_id or "default")

    async def run(self, messages, session=None):
        message = messages[0]
        text = getattr(message, "text", None)
        if text is None:
            text = getattr(message, "content", None)
        self.calls.append(
            {
                "text": text if isinstance(text, str) else str(message),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return _FakeA2AResponse('{"messages":[{"text":"planned","delay_ms":0}]}')


def test_orchestration_agent_can_plan_over_a2a_with_opencode_options() -> None:
    planner = MAFOrchestrationAgent(
        model="qwen-default",
        base_url="",
        api_key="",
        env_file_path=".env",
        backend="a2a",
        remote_agent_url="http://127.0.0.1:18770",
        default_remote_agent="planner",
        default_model_provider="lmstudio",
        default_model_id="qwen-default",
        a2a_agent_factory=_FakeA2AAgent,
    )

    result = planner.plan_with_model(
        task="Conversation delivery planning",
        instructions="Return JSON",
        payload={"message": "hello"},
        model_id="qwen-override",
    )

    assert result["messages"][0]["text"] == "planned"
    assert planner._a2a_agent is not None
    assert (
        planner._a2a_agent.calls[0]["session_id"]
        == "conversation-layer-planner:qwen-override"
    )
    assert (
        "[[opencode-options:agent=planner;model_provider=lmstudio;model_id=qwen-override]]"
        in str(planner._a2a_agent.calls[0]["text"])
    )
```

## 5. Added Test: `tests/test_opencode_bridge.py`

Purpose:

- Verify `_collect_text(...)` handles nested artifact payloads and simple message parts.

Added file contents:

```python
from __future__ import annotations

from a2a_example_app.opencode_a2a_bridge import _collect_text


def test_collect_text_reads_nested_artifact_parts() -> None:
    payload = {
        "message": {
            "artifacts": [
                {
                    "parts": [
                        {"type": "text", "text": "First reply"},
                        {"root": {"text": "Second reply"}},
                    ]
                }
            ]
        }
    }

    assert _collect_text(payload) == "First reply\nSecond reply"


def test_collect_text_reads_message_parts_payload() -> None:
    payload = {
        "parts": [
            {"type": "text", "text": "Hello from parts"},
        ]
    }

    assert _collect_text(payload) == "Hello from parts"
```

## Validation Run In This Workspace

Commands run:

- `pytest tests/test_orchestration_agent.py -q`
- `pytest tests/test_opencode_bridge.py -q`
- `pytest tests -q`

Result:

- `62 passed`

## Minimal Replay Checklist For Original V2 Repo

1. Update `core_agent_app/config.py` to honor `ENV_FILE_PATH`
2. Update `conversation_layer/services/orchestration_agent.py` with the A2A planner support shown above
3. Update `a2a_example_app/opencode_a2a_bridge.py` with the recursive `_collect_text(...)` logic
4. Add `tests/test_orchestration_agent.py`
5. Add `tests/test_opencode_bridge.py`
6. Run `pytest tests -q`
