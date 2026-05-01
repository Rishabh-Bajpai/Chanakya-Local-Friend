# Conversation Layer V1_mod Porting Reference

This file records the local `V1_mod` changes made on top of the original `chanakya_conversation_layer` copy introduced in commit `c118235b6729c5fb276f7eacf9e75960569189b5`.

It also compares those changes against `chanakya_conversation_layer_new` (`V2`) so we can decide what to port.

## Commit Range

Base V1 copy:

- `c118235b6729c5fb276f7eacf9e75960569189b5` `feat: implement AI-Router-AIR server infrastructure and chanakya-conversation-layer core services`

V1_mod commits after that base:

- `66b63d2e01da3b2677691fed8dd8c3415aa56723` `fix startup/runtime issues across AIR and conversation layer`
- `fad0692b4080dea612b4b7192b4b2940b80515d2` `fix: correct timezone handling in datetime parsing`
- `363be262b18dbbec117b254c1ed317b484c15c8e` `chore: update .gitignore to exclude log and pid files; remove obsolete log and pid files`
- `84d4140b19c87efaf774ee9f8a1d7908140b2cde` `Update chanakya_conversation_layer/start_stack.sh`
- `910e049f69a5fc8fa8f13be452831d6523e3a88d` `chore: delete stale Playwright logs and ignore .playwright-mcp directories`
- `c82224a520e6ac1e6b64006e9352133212226258` `fix: correct typo in project directory path within documentation and configuration files`
- `3f4a64ae705bfff7d153b1d0fc4f9eab9a4adc4b` `refactor: centralize runtime configuration management and implement persistent storage for A2A agent settings`
- `72589d68842c8cb431a1df6694ea9e4bccedb536` `feat: add fallback mechanism to retry developer prompts without tools when output is blank`
- `04fc04c687847a85120e196e87f8f12251f0e7fd` `Update chanakya_conversation_layer/conversation_layer/services/orchestration_agent.py`

## Porting Summary

Port to V2:

- `core_agent_app/config.py`: support `ENV_FILE_PATH`
- `conversation_layer/services/orchestration_agent.py`: V1_mod A2A planner support
- `a2a_example_app/opencode_a2a_bridge.py`: robust nested text extraction
- `tests/test_orchestration_agent.py`: carry or recreate coverage
- `tests/test_opencode_bridge.py`: carry or recreate coverage if the bridge still exists in V2

Manual review before porting:

- `start_stack.sh`: only needed if this script is still used directly from the conversation-layer package
- timezone compatibility changes in `conversation_wrapper.py`, `working_memory.py`, `agent_session_context.py`, and `a2a_example_app/chatflash/store.py`

Confirmed in this workspace:

- `scripts/start_chanakya_air.sh` currently launches the stack with `/home/diogenes/miniconda3/envs/chanakya-maf/bin/python`, which is Python 3.10.
- Under that runtime, `datetime.UTC` crashes both the conversation layer and the main Chanakya app during import.
- The local fix was to replace `datetime.UTC` usage with `timezone.utc` in the four files listed above.

Do not port as product logic:

- deleted log and pid files
- deleted `.playwright-mcp` artifacts
- doc-only path typo fixes unless the same typo still exists in V2 docs

## Comparison Against V2

### 1. `core_agent_app/config.py`

Status: missing in V2, should port.

Why:

- V1_mod changed the conversation-layer app to honor the repo-root exported `ENV_FILE_PATH`.
- `scripts/start_chanakya_air.sh` in this repo already exports `ENV_FILE_PATH`.
- `chanakya_conversation_layer_new/core_agent_app/config.py` reverted to a hardcoded local `.env` path.

Exact V1 -> V1_mod hunk:

```diff
@@
-    env_file_path: str = str(PROJECT_ROOT / ".env")
+    env_file_path: str = os.getenv("ENV_FILE_PATH", str(PROJECT_ROOT / ".env"))
```

Old vs V2 result today:

```diff
diff --git a/chanakya_conversation_layer/core_agent_app/config.py b/chanakya_conversation_layer_new/core_agent_app/config.py
@@
-    env_file_path: str = os.getenv("ENV_FILE_PATH", str(PROJECT_ROOT / ".env"))
+    env_file_path: str = str(PROJECT_ROOT / ".env")
```

Recommendation:

- Port this exact line change into `chanakya_conversation_layer_new/core_agent_app/config.py`.

### 2. `conversation_layer/services/orchestration_agent.py`

Status: missing in V2, should port manually.

Why:

- V1_mod added A2A-aware orchestration support to `MAFOrchestrationAgent`.
- V2 currently has the original OpenAI-compatible-only planner implementation.
- This is the biggest compatibility delta and likely the highest-risk item to lose.

Behavior added in V1_mod:

- `backend` field with `openai_compatible` default
- A2A agent construction path in `__post_init__`
- `remote_agent_url`, `default_remote_agent`, `default_model_provider`, `default_model_id`
- optional `a2a_agent_factory`
- `_a2a_agent` and `_a2a_sessions` caches
- `_run_a2a(...)`
- `_a2a_session_for_model(...)`
- `_build_a2a_prompt(...)`
- A2A execution in both sync and async planner paths
- final message format fix: `Message(role="user", text=message)`

Focused V1 -> V1_mod hunks:

```diff
@@
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
+    _a2a_agent: Any | None = field(init=False, default=None, repr=False)
+    _a2a_sessions: dict[str, Any] = field(init=False, default_factory=dict, repr=False)
```

```diff
@@
-        if self.runner is not None:
-            result = self.runner(prompt)
-        else:
-            agent = self._agent_for_model(model_override)
-            result = asyncio.run(agent.run(prompt))
+        if self.runner is not None:
+            result = self.runner(prompt)
+        elif self.backend == "a2a":
+            result = asyncio.run(self._run_a2a(prompt, model_override=model_override))
+        else:
+            agent = self._agent_for_model(model_override)
+            result = asyncio.run(agent.run(prompt))
```

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

```diff
@@
+    def _a2a_session_for_model(self, model_override: str | None):
+        model_id = str(model_override or "").strip() or str(self.default_model_id or "").strip()
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

Old vs V2 result today:

```diff
diff --git a/chanakya_conversation_layer/conversation_layer/services/orchestration_agent.py b/chanakya_conversation_layer_new/conversation_layer/services/orchestration_agent.py
@@
-    backend: str = "openai_compatible"
-    remote_agent_url: str = ""
-    default_remote_agent: str | None = None
-    default_model_provider: str | None = None
-    default_model_id: str | None = None
-    a2a_agent_factory: Any | None = None
-    _a2a_agent: Any | None = field(init=False, default=None, repr=False)
-    _a2a_sessions: dict[str, Any] = field(init=False, default_factory=dict, repr=False)
+    # V2 currently lacks the V1_mod A2A planner fields
@@
-        elif self.backend == "a2a":
-            result = asyncio.run(self._run_a2a(prompt, model_override=model_override))
+        # V2 currently falls straight through to the local planner path
@@
-    async def _run_a2a(...)
-    def _a2a_session_for_model(...)
-    def _build_a2a_prompt(...)
+    # V2 currently lacks these helper methods entirely
```

Recommendation:

- Port manually into V2 rather than copying the full file wholesale, because `V2` may have future upstream changes in this file.

### 3. `a2a_example_app/opencode_a2a_bridge.py`

Status: missing in V2, should port.

Why:

- V1_mod made bridge response extraction robust against nested payload shapes.
- V2 reverted to reading only `message.get("parts", [])`.
- This is likely needed if the remote bridge response schema is inconsistent or nested.

Focused V1 -> V1_mod hunks:

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

```diff
@@
-            reply = (
-                _collect_text(message.get("parts", []))
-                or "OpenCode returned no text parts."
-            )
+            reply = _collect_text(message) or "OpenCode returned no text parts."
```

Old vs V2 result today:

```diff
diff --git a/chanakya_conversation_layer/a2a_example_app/opencode_a2a_bridge.py b/chanakya_conversation_layer_new/a2a_example_app/opencode_a2a_bridge.py
@@
-def _collect_text(payload: Any) -> str:
+def _collect_text(parts: list[dict[str, Any]]) -> str:
@@
-            reply = _collect_text(message) or "OpenCode returned no text parts."
+            reply = (
+                _collect_text(message.get("parts", []))
+                or "OpenCode returned no text parts."
+            )
```

Recommendation:

- Port this logic into V2.

### 4. `start_stack.sh`

Status: review manually.

Why:

- V1_mod changed the app startup to explicitly `cd` into `ROOT_DIR` before running Flask.
- This can matter if the script is invoked from outside the conversation-layer directory.
- It is not necessarily needed if the host app uses `scripts/start_chanakya_air.sh` instead.

Exact V1 -> V1_mod hunk:

```diff
@@
-    env FLASK_APP=app flask run --host "$APP_HOST" --port "$APP_PORT"
+    bash -lc "cd \"$ROOT_DIR\" && exec env FLASK_APP=app flask run --host \"$APP_HOST\" --port \"$APP_PORT\""
```

Old vs V2 result today:

```diff
diff --git a/chanakya_conversation_layer/start_stack.sh b/chanakya_conversation_layer_new/start_stack.sh
@@
-    bash -lc "cd \"$ROOT_DIR\" && exec env FLASK_APP=app flask run --host \"$APP_HOST\" --port \"$APP_PORT\""
+    env FLASK_APP=app flask run --host "$APP_HOST" --port "$APP_PORT"
```

Recommendation:

- Only port if you still use `chanakya_conversation_layer_new/start_stack.sh` directly.

### 5. Python timezone compatibility changes

Status: review manually.

Why:

- V1_mod replaced `datetime.UTC` with `timezone.utc` in several files for Python 3.10 compatibility.
- V2 reverted to `UTC` in multiple places.
- The package `pyproject.toml` still says `requires-python = ">=3.11"`, so this is only needed if your actual runtime still uses Python 3.10.

Files affected in V1_mod:

- `conversation_layer/services/conversation_wrapper.py`
- `conversation_layer/services/working_memory.py`
- `core_agent_app/services/agent_session_context.py`
- `a2a_example_app/chatflash/store.py`

Representative hunks:

```diff
@@
-from datetime import UTC, datetime, timedelta
+from datetime import datetime, timedelta, timezone
@@
-    return datetime.now(UTC)
+    return datetime.now(timezone.utc)
@@
-                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
+                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
```

```diff
@@
-from datetime import UTC, datetime
+from datetime import datetime, timezone
@@
-    return datetime.now(UTC).isoformat()
+    return datetime.now(timezone.utc).isoformat()
```

```diff
@@
-from datetime import UTC, datetime
+from datetime import datetime, timezone
@@
-                record.updated_at = datetime.now(UTC)
+                record.updated_at = datetime.now(timezone.utc)
```

Recommendation:

- Port if V2 will be started through the same shared stack/runtime pattern used here, because the current startup path uses Python 3.10 in practice.
- Keep the V2 `UTC` style only if you have also moved the real runtime to Python 3.11+ and verified that the startup scripts use that interpreter.

Observed failure in this workspace:

- `chanakya_conversation_layer.log` showed `ImportError: cannot import name 'UTC' from 'datetime'`.
- `chanakya.log` showed the same import failure because the main app imports the conversation-layer package.
- After switching these call sites to `timezone.utc`, both `http://127.0.0.1:5514/` and `http://127.0.0.1:5513/` returned `200 OK`.

### 6. Tests added in V1_mod

Status: missing in V2, should be recreated if the corresponding code is ported.

Files added in V1_mod:

- `chanakya_conversation_layer/tests/test_orchestration_agent.py`
- `chanakya_conversation_layer/tests/test_opencode_bridge.py`

Intent:

- validate A2A orchestration planner session/model-header behavior
- validate nested bridge text extraction behavior

Recommendation:

- If we port the A2A planner and bridge extraction changes into V2, carry these tests too.

## Recommended Step 3 Port Order

1. Port `core_agent_app/config.py` `ENV_FILE_PATH` support.
2. Port `conversation_layer/services/orchestration_agent.py` A2A planner support.
3. Port `a2a_example_app/opencode_a2a_bridge.py` nested payload text extraction.
4. Recreate or port the two tests.
5. Decide separately whether timezone compatibility and `start_stack.sh` changes are still needed.

## Quick Decision Table

`core_agent_app/config.py`

- V1_mod status: changed
- V2 status: missing
- action: port

`conversation_layer/services/orchestration_agent.py`

- V1_mod status: changed heavily
- V2 status: missing
- action: port manually

`a2a_example_app/opencode_a2a_bridge.py`

- V1_mod status: changed
- V2 status: missing
- action: port

`start_stack.sh`

- V1_mod status: changed
- V2 status: missing
- action: optional manual review

`conversation_wrapper.py`

- V1_mod status: timezone compatibility only
- V2 status: not present, plus many upstream changes
- action: port if V2 will run under the current Python 3.10-based startup flow; otherwise decide based on the actual runtime Python version

`working_memory.py`

- V1_mod status: timezone compatibility only
- V2 status: not present
- action: port if V2 will run under the current Python 3.10-based startup flow; otherwise optional based on actual runtime Python version

`agent_session_context.py`

- V1_mod status: timezone compatibility only
- V2 status: not present
- action: port if V2 will run under the current Python 3.10-based startup flow; otherwise optional based on actual runtime Python version

`a2a_example_app/chatflash/store.py`

- V1_mod status: timezone compatibility only
- V2 status: not present
- action: port if V2 will run under the current Python 3.10-based startup flow; otherwise optional based on actual runtime Python version
