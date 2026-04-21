from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from agent_framework import Message

from chanakya.agent.runtime import MAFRuntime
from chanakya.chat_service import ChatService
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import (
    REQUEST_STATUS_COMPLETED,
    REQUEST_STATUS_FAILED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
)
from chanakya.history_provider import SQLAlchemyHistoryProvider
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.services.sandbox_workspace import delete_shared_workspace, resolve_shared_workspace
from chanakya.store import ChanakyaStore


@dataclass
class _Trace:
    tool_id: str
    tool_name: str
    server_name: str
    status: str
    input_payload: str | None = None
    output_text: str | None = None
    error_text: str | None = None


@dataclass
class _RunResult:
    text: str
    response_mode: str
    tool_traces: list[_Trace]


class _RuntimeStub:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.profile = AgentProfileModel(
            id="agent_chanakya",
            name="Chanakya",
            role="assistant",
            system_prompt="test",
            personality="",
            tool_ids_json=[],
            workspace=None,
            heartbeat_enabled=False,
            heartbeat_interval_seconds=300,
            heartbeat_file_path=None,
            is_active=True,
            created_at="2026-03-29T00:00:00+00:00",
            updated_at="2026-03-29T00:00:00+00:00",
        )
        self.should_fail = should_fail
        self.calls: list[dict[str, str]] = []
        self.cleared_session_ids: list[str] = []

    def runtime_metadata(
        self,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> dict[str, str | None]:
        selected_backend = backend or "local"
        return {
            "model": a2a_model_id if selected_backend == "a2a" else "test-model",
            "endpoint": a2a_url if selected_backend == "a2a" else "http://test",
            "runtime": "maf_agent",
            "backend": selected_backend,
            "a2a_remote_agent": a2a_remote_agent if selected_backend == "a2a" else None,
            "a2a_model_provider": a2a_model_provider if selected_backend == "a2a" else None,
            "a2a_model_id": a2a_model_id if selected_backend == "a2a" else None,
        }

    def run(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
    ) -> _RunResult:
        if self.should_fail:
            raise RuntimeError("runtime exploded")
        self.calls.append({"session_id": session_id, "text": text, "request_id": request_id})
        return _RunResult(
            text=f"reply:{text}",
            response_mode="direct_answer",
            tool_traces=[],
        )

    def clear_session_state(self, session_id: str) -> None:
        self.cleared_session_ids.append(session_id)


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def test_chat_persists_request_root_task_and_timeline() -> None:
    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))

    reply = service.chat("session_1", "Implement milestone 3")

    requests = store.list_requests(session_id="session_1")
    assert len(requests) == 1
    assert requests[0]["id"] == reply.request_id
    assert requests[0]["status"] == REQUEST_STATUS_COMPLETED
    assert requests[0]["root_task_id"] == reply.root_task_id

    tasks = store.list_tasks(session_id="session_1", root_only=True)
    assert len(tasks) == 1
    assert tasks[0]["id"] == reply.root_task_id
    assert tasks[0]["status"] == TASK_STATUS_DONE
    assert tasks[0]["result"]["message"] == "reply:Implement milestone 3"

    task_events = store.list_task_events(session_id="session_1")
    event_types = [item["event_type"] for item in task_events]
    assert event_types == [
        "request_received",
        "task_created",
        "task_status_changed",
        "response_persisted",
        "task_status_changed",
    ]

    messages = store.list_messages("session_1")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["root_task_id"] == reply.root_task_id


def test_chat_post_processes_visible_assistant_message() -> None:
    class _PostProcessorStub:
        enabled = True

        def wrap_reply(
            self,
            *,
            session_id: str,
            user_message: str,
            assistant_message: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            conversation_tone_instruction: str | None = None,
            tts_instruction: str | None = None,
            metadata: dict[str, str] | None = None,
        ):
            return type(
                "Wrapped",
                (),
                {
                    "response": f"layered:{assistant_message}",
                    "messages": [{"text": f"layered:{assistant_message}", "delay_ms": 0}],
                    "metadata": {"pending_delivery_count": 0, "source": "conversation_layer"},
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))
    service._conversation_layer = _PostProcessorStub()  # type: ignore[attr-defined]

    reply = service.chat("session_layered", "Explain recursion")

    messages = store.list_messages("session_layered")
    assert messages[1]["content"] == "layered:reply:Explain recursion"
    assert messages[1]["metadata"]["conversation_layer_applied"] is True
    assert reply.message == "layered:reply:Explain recursion"


def test_chat_registers_request_scoped_artifact() -> None:
    class _ArtifactRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            workspace = resolve_shared_workspace(request_id, create=True)
            (workspace / "palindrome.py").write_text(
                (
                    "def is_palindrome_number(value: int) -> bool:\n"
                    "    text = str(value)\n"
                    "    return text == text[::-1]\n"
                ),
                encoding="utf-8",
            )
            return _RunResult(
                text="I saved the exact program as an artifact.",
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _ArtifactRuntimeStub()))

    reply = service.chat("session_artifact", "Write a palindrome detector")

    try:
        assert len(reply.artifacts) == 1
        artifact = reply.artifacts[0]
        assert artifact["name"] == "palindrome.py"
        assert artifact["kind"] == "code"
        assert artifact["download_url"].endswith("/download")
        assert store.list_artifacts_for_request(reply.request_id)[0]["path"] == "palindrome.py"
        messages = store.list_messages("session_artifact")
        assert messages[1]["metadata"]["artifacts"][0]["name"] == "palindrome.py"
    finally:
        delete_shared_workspace(reply.request_id)


def test_chat_keeps_artifacts_when_conversation_layer_wraps() -> None:
    class _ArtifactRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            workspace = resolve_shared_workspace(request_id, create=True)
            (workspace / "report.md").write_text(
                "# Report\n\nDetailed findings.\n",
                encoding="utf-8",
            )
            return _RunResult(
                text="I saved the report as an artifact.",
                response_mode="direct_answer",
                tool_traces=[],
            )

    class _PostProcessorStub:
        enabled = True

        def wrap_reply(self, **kwargs):
            assistant_message = str(kwargs["assistant_message"])
            return type(
                "Wrapped",
                (),
                {
                    "response": f"layered:{assistant_message}",
                    "messages": [{"text": f"layered:{assistant_message}", "delay_ms": 0}],
                    "metadata": {"pending_delivery_count": 0, "source": "conversation_layer"},
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _ArtifactRuntimeStub()))
    service._conversation_layer = _PostProcessorStub()  # type: ignore[attr-defined]

    reply = service.chat("session_layer_artifact", "Write a short research report")

    try:
        assert reply.message == "layered:I saved the report as an artifact."
        assert reply.artifacts[0]["name"] == "report.md"
        messages = store.list_messages("session_layer_artifact")
        assert messages[1]["metadata"]["artifacts"][0]["name"] == "report.md"
    finally:
        delete_shared_workspace(reply.request_id)


def test_chat_materializes_inline_code_block_as_artifact() -> None:
    class _InlineCodeRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            return _RunResult(
                text=(
                    "Here is the program:\n\n"
                    "```python\n"
                    "def is_palindrome(n):\n"
                    "    text = str(n)\n"
                    "    return text == text[::-1]\n"
                    "```\n"
                ),
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _InlineCodeRuntimeStub()))

    reply = service.chat("session_inline_code", "Write a palindrome program")

    try:
        assert len(reply.artifacts) == 1
        artifact = reply.artifacts[0]
        assert artifact["name"].endswith(".py")
        assert artifact["kind"] == "code"
        assert artifact["path"].startswith(f"generated/{reply.request_id}/")
        workspace = resolve_shared_workspace(reply.request_id, create=False)
        artifact_file = workspace / artifact["path"]
        assert artifact_file.read_text(encoding="utf-8").startswith("def is_palindrome")
    finally:
        delete_shared_workspace(reply.request_id)


def test_chat_generates_artifact_via_followup_when_first_answer_is_prose_only() -> None:
    class _TwoStepRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]
            self.calls: list[str] = []

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            self.calls.append(text)
            if len(self.calls) == 1:
                return _RunResult(
                    text=(
                        "Here is a simple Python program to check if a number is prime. "
                        "It defines a helper and then prompts for input."
                    ),
                    response_mode="direct_answer",
                    tool_traces=[],
                )
            if len(self.calls) == 2:
                return _RunResult(
                    text=(
                        '{"title": "Write a Python program", '
                        '"description": "Create the script", '
                        '"message": "Please write the code."}'
                    ),
                    response_mode="direct_answer",
                    tool_traces=[],
                )
            return _RunResult(
                text=(
                    "```python\n"
                    "def is_prime(n):\n"
                    "    if n < 2:\n"
                    "        return False\n"
                    "    for i in range(2, int(n ** 0.5) + 1):\n"
                    "        if n % i == 0:\n"
                    "            return False\n"
                    "    return True\n"
                    "```\n"
                ),
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _TwoStepRuntimeStub()))

    reply = service.chat("session_followup_artifact", "Write a Python program for prime numbers")

    try:
        assert len(reply.artifacts) == 1
        artifact = reply.artifacts[0]
        assert artifact["kind"] == "code"
        assert artifact["name"].endswith(".py")
        assert len(service.runtime.calls) == 3
        workspace = resolve_shared_workspace(reply.request_id, create=False)
        artifact_file = workspace / artifact["path"]
        assert artifact_file.read_text(encoding="utf-8").startswith("def is_prime")
    finally:
        delete_shared_workspace(reply.request_id)


def test_conversation_layer_receives_original_answer_when_artifact_exists() -> None:
    class _InlineCodeRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            return _RunResult(
                text=(
                    "Here is a Python program to check if a number is a palindrome.\n\n"
                    "```python\n"
                    "def is_palindrome(n):\n"
                    "    return str(n) == str(n)[::-1]\n"
                    "```\n\n"
                    "It compares the number with its reverse."
                ),
                response_mode="direct_answer",
                tool_traces=[],
            )

    class _PostProcessorStub:
        enabled = True

        def __init__(self) -> None:
            self.assistant_messages: list[str] = []

        def wrap_reply(self, **kwargs):
            self.assistant_messages.append(str(kwargs["assistant_message"]))
            assistant_message = str(kwargs["assistant_message"])
            return type(
                "Wrapped",
                (),
                {
                    "response": assistant_message,
                    "messages": [{"text": assistant_message, "delay_ms": 0}],
                    "metadata": {"pending_delivery_count": 0, "source": "conversation_layer"},
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _InlineCodeRuntimeStub()))
    layer = _PostProcessorStub()
    service._conversation_layer = layer  # type: ignore[attr-defined]

    reply = service.chat("session_original_conversation", "Write a palindrome program")

    try:
        assert reply.artifacts
        assert layer.assistant_messages
        assert "```python" in layer.assistant_messages[0]
        assert "def is_palindrome" in layer.assistant_messages[0]
        assert "It compares the number with its reverse." in layer.assistant_messages[0]
    finally:
        delete_shared_workspace(reply.request_id)


def test_artifact_followup_uses_isolated_runtime_session() -> None:
    class _TwoStepRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            self.calls.append({"session_id": session_id, "text": text, "request_id": request_id})
            if len(self.calls) == 1:
                return _RunResult(
                    text="Here is a short explanation without code.",
                    response_mode="direct_answer",
                    tool_traces=[],
                )
            return _RunResult(
                text="```python\nprint('hello')\n```\n",
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    runtime = _TwoStepRuntimeStub()
    service = ChatService(store, cast(MAFRuntime, runtime))

    reply = service.chat("session_isolated_followup", "Write a Python program")

    try:
        assert reply.artifacts
        assert len(runtime.calls) >= 2
        assert runtime.calls[0]["session_id"] == "session_isolated_followup"
        assert runtime.calls[1]["session_id"] != "session_isolated_followup"
        assert runtime.calls[1]["session_id"] in runtime.cleared_session_ids
    finally:
        delete_shared_workspace(reply.request_id)


def test_work_scoped_generated_artifacts_remain_immutable_across_requests() -> None:
    class _WorkScopedInlineCodeRuntimeStub(_RuntimeStub):
        def __init__(self) -> None:
            super().__init__()
            self.profile.tool_ids_json = ["mcp_filesystem"]
            self.calls = 0

        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            prompt_addendum: str | None = None,
        ) -> _RunResult:
            self.calls += 1
            return _RunResult(
                text=(
                    "```python\n"
                    f"print('request {self.calls}')\n"
                    "```\n"
                ),
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _WorkScopedInlineCodeRuntimeStub()))
    work_id = "work_shared_artifacts"

    first_reply = service.chat("session_work_shared", "Write script one", work_id=work_id)
    second_reply = service.chat("session_work_shared", "Write script two", work_id=work_id)

    try:
        first_artifact = first_reply.artifacts[0]
        second_artifact = second_reply.artifacts[0]
        assert first_artifact["path"] != second_artifact["path"]
        assert first_artifact["path"].startswith(f"generated/{first_reply.request_id}/")
        assert second_artifact["path"].startswith(f"generated/{second_reply.request_id}/")

        workspace = resolve_shared_workspace(work_id, create=False)
        first_file = workspace / first_artifact["path"]
        second_file = workspace / second_artifact["path"]
        assert first_file.read_text(encoding="utf-8") == "print('request 1')\n"
        assert second_file.read_text(encoding="utf-8") == "print('request 2')\n"

        work_artifacts = store.list_artifacts_for_work(work_id)
        assert [artifact["path"] for artifact in work_artifacts] == [
            first_artifact["path"],
            second_artifact["path"],
        ]
    finally:
        delete_shared_workspace(work_id)


def test_chat_passes_a2a_backend_into_conversation_layer() -> None:
    class _PostProcessorStub:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def wrap_reply(
            self,
            *,
            session_id: str,
            user_message: str,
            assistant_message: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            conversation_tone_instruction: str | None = None,
            tts_instruction: str | None = None,
            metadata: dict[str, str] | None = None,
        ):
            self.calls.append(
                {
                    "session_id": session_id,
                    "backend": backend,
                    "model_id": model_id,
                    "a2a_url": a2a_url,
                    "a2a_remote_agent": a2a_remote_agent,
                    "a2a_model_provider": a2a_model_provider,
                    "a2a_model_id": a2a_model_id,
                    "conversation_tone_instruction": conversation_tone_instruction,
                    "tts_instruction": tts_instruction,
                    "metadata": metadata,
                }
            )
            return type(
                "Wrapped",
                (),
                {
                    "response": f"layered:{assistant_message}",
                    "messages": [{"text": f"layered:{assistant_message}", "delay_ms": 0}],
                    "metadata": {
                        "pending_delivery_count": 0,
                        "source": "conversation_layer",
                        "conversation_layer_backend": backend,
                    },
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))
    layer = _PostProcessorStub()
    service._conversation_layer = layer  # type: ignore[attr-defined]

    reply = service.chat(
        "session_a2a_layer",
        "Explain recursion",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
        a2a_remote_agent="planner",
        a2a_model_provider="lmstudio",
        a2a_model_id="qwen/qwen3.5-9b",
    )

    assert layer.calls[0]["backend"] == "a2a"
    assert reply.metadata["conversation_layer_backend"] == "a2a"


def test_chat_passes_conversation_preferences_into_conversation_layer() -> None:
    class _PostProcessorStub:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def wrap_reply(
            self,
            *,
            session_id: str,
            user_message: str,
            assistant_message: str,
            model_id: str | None = None,
            backend: str | None = None,
            a2a_url: str | None = None,
            a2a_remote_agent: str | None = None,
            a2a_model_provider: str | None = None,
            a2a_model_id: str | None = None,
            conversation_tone_instruction: str | None = None,
            tts_instruction: str | None = None,
            metadata: dict[str, str] | None = None,
        ):
            self.calls.append(
                {
                    "conversation_tone_instruction": conversation_tone_instruction,
                    "tts_instruction": tts_instruction,
                }
            )
            return type(
                "Wrapped",
                (),
                {
                    "response": assistant_message,
                    "messages": [{"text": assistant_message, "delay_ms": 0}],
                    "metadata": {"pending_delivery_count": 0, "source": "conversation_layer"},
                },
            )()

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub()))
    layer = _PostProcessorStub()
    service._conversation_layer = layer  # type: ignore[attr-defined]

    service.chat(
        "session_tone_layer",
        "Explain recursion",
        conversation_tone_instruction="Dry but kind.",
        tts_instruction="Speak clearly with short phrases.",
    )

    assert layer.calls[0]["conversation_tone_instruction"] == "Dry but kind."
    assert layer.calls[0]["tts_instruction"] == "Speak clearly with short phrases."


def test_chat_backend_falls_back_for_legacy_runtime_run_signature() -> None:
    class _LegacyRuntimeStub(_RuntimeStub):
        def run(
            self,
            session_id: str,
            text: str,
            *,
            request_id: str,
            model_id: str | None = None,
        ) -> _RunResult:
            if self.should_fail:
                raise RuntimeError("runtime exploded")
            return _RunResult(
                text=f"legacy:{text}",
                response_mode="direct_answer",
                tool_traces=[],
            )

    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _LegacyRuntimeStub()))

    reply = service.chat(
        "session_legacy_backend",
        "Hi",
        backend="a2a",
        a2a_url="http://127.0.0.1:18770",
        a2a_remote_agent="planner",
        a2a_model_provider="lmstudio",
        a2a_model_id="qwen/qwen3.5-9b",
    )

    assert reply.message == "legacy:Hi"
    assert reply.request_status == REQUEST_STATUS_COMPLETED


def test_chat_failure_marks_request_and_task_failed() -> None:
    store = _build_store()
    service = ChatService(store, cast(MAFRuntime, _RuntimeStub(should_fail=True)))

    try:
        service.chat("session_2", "This should fail")
    except RuntimeError as exc:
        assert str(exc) == "runtime exploded"
    else:
        raise AssertionError("expected runtime error")

    requests = store.list_requests(session_id="session_2")
    assert len(requests) == 1
    assert requests[0]["status"] == REQUEST_STATUS_FAILED

    tasks = store.list_tasks(session_id="session_2", root_only=True)
    assert len(tasks) == 1
    assert tasks[0]["status"] == TASK_STATUS_FAILED
    assert tasks[0]["error"] == "runtime exploded"

    task_events = store.list_task_events(session_id="session_2")
    assert task_events[-1]["event_type"] == "task_status_changed"
    assert task_events[-1]["payload"]["to_status"] == TASK_STATUS_FAILED


def test_update_task_preserves_error_until_non_failed_transition() -> None:
    store = _build_store()
    store.create_request(
        request_id="req_1",
        session_id="session_3",
        user_message="Investigate failure",
        status="created",
        root_task_id="task_1",
    )
    store.create_task(
        task_id="task_1",
        request_id="req_1",
        parent_task_id=None,
        title="Investigate failure",
        summary=None,
        status="created",
        owner_agent_id="agent_chanakya",
        task_type="chat_request",
    )

    store.update_task("task_1", status=TASK_STATUS_FAILED, error_text="boom")
    store.update_task("task_1", status=TASK_STATUS_FAILED)
    assert store.list_tasks(session_id="session_3", root_only=True)[0]["error"] == "boom"

    store.update_task("task_1", status=TASK_STATUS_IN_PROGRESS)
    assert store.list_tasks(session_id="session_3", root_only=True)[0]["error"] is None


def test_history_provider_filters_control_json_messages() -> None:
    row = type(
        "Row",
        (),
        {
            "role": "assistant",
            "content": '{"should_create_subagents": false, "reason": "not needed"}',
            "metadata_json": {},
        },
    )()
    assert SQLAlchemyHistoryProvider._is_control_history_row(row) is True

    normal_row = type(
        "Row",
        (),
        {
            "role": "assistant",
            "content": "Here is the final report.",
            "metadata_json": {},
        },
    )()
    assert SQLAlchemyHistoryProvider._is_control_history_row(normal_row) is False


def test_history_provider_compresses_history_with_relevance_and_recency() -> None:
    rows = [
        type(
            "Row", (), {"content": "old unrelated note", "role": "assistant", "metadata_json": {}}
        )(),
        type(
            "Row",
            (),
            {
                "content": "billing retry policy and timeout handling",
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
        type(
            "Row",
            (),
            {"content": "another unrelated item", "role": "assistant", "metadata_json": {}},
        )(),
        type(
            "Row", (), {"content": "latest user follow-up", "role": "user", "metadata_json": {}}
        )(),
        type(
            "Row",
            (),
            {"content": "latest assistant reply", "role": "assistant", "metadata_json": {}},
        )(),
    ]

    selected = SQLAlchemyHistoryProvider._compress_history_rows(
        rows,
        query_text="help with billing retry",
        recent_window=2,
        max_messages=3,
        max_chars=2000,
        max_message_chars=500,
    )

    texts = [content for _, content in selected]
    assert any("billing retry policy" in text for text in texts)
    assert any("latest user follow-up" in text for text in texts)
    assert any("latest assistant reply" in text for text in texts)


def test_history_provider_enforces_character_budgets() -> None:
    rows = [
        type(
            "Row",
            (),
            {
                "content": "A" * 500,
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
        type(
            "Row",
            (),
            {
                "content": "B" * 500,
                "role": "assistant",
                "metadata_json": {},
            },
        )(),
    ]

    selected = SQLAlchemyHistoryProvider._compress_history_rows(
        rows,
        query_text="",
        recent_window=2,
        max_messages=10,
        max_chars=320,
        max_message_chars=180,
    )

    assert selected
    combined = "".join(content for _, content in selected)
    assert len(combined) <= 323
    assert all(len(content) <= 183 for _, content in selected)


def test_history_context_stats_are_persisted_in_message_metadata() -> None:
    store = _build_store()
    provider = SQLAlchemyHistoryProvider(store.Session)

    run_in_maf_loop(
        provider.save_messages(
            "session_hist_stats",
            [Message(role="assistant", text="Final answer")],
            state={
                "request_id": "req_hist_stats",
                "history_context_stats": {
                    "available_messages": 12,
                    "selected_messages": 5,
                    "selected_chars": 980,
                    "relevance_hits": 2,
                    "backfill_hits": 1,
                    "truncated_messages": 0,
                    "query_text": "implement billing retry",
                },
            },
        )
    )

    messages = store.list_messages("session_hist_stats")
    assert len(messages) == 1
    metadata = messages[0]["metadata"]
    assert "history_context" in metadata
    assert metadata["history_context"]["selected_messages"] == 5
    assert metadata["history_context"]["relevance_hits"] == 2
