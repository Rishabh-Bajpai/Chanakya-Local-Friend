from __future__ import annotations

import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any

from chanakya.agent.runtime import MAFRuntime, normalize_runtime_backend
from chanakya.agent_manager import AgentManager
from chanakya.conversation_layer_support import ConversationLayerResult, ConversationLayerSupport
from chanakya.debug import debug_log
from chanakya.domain import (
    REQUEST_STATUS_CANCELLED,
    REQUEST_STATUS_COMPLETED,
    REQUEST_STATUS_CREATED,
    REQUEST_STATUS_FAILED,
    REQUEST_STATUS_IN_PROGRESS,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CREATED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_WAITING_INPUT,
    ChatReply,
    make_id,
    now_iso,
)
from chanakya.services.ntfy import NtfyNotificationDispatcher, summarize_notification_text
from chanakya.services.sandbox_workspace import (
    get_artifact_storage_root,
    get_shared_workspace_root,
    resolve_shared_workspace,
)
from chanakya.store import ChanakyaStore

_NORMAL_CHAT_DELEGATION_NOTICE = "Transferring your work to an expert. This may take a bit longer."
_WAITING_INPUT_ROUTE = "waiting_input_prompt"
_CLASSIC_WORK_COMPLETION_ROUTE = "classic_work_completion"
_CLASSIC_CHAT_RUNTIME_PROMPT_ADDENDUM = (
    "Optimize for speed and direct completion. "
    "Default to direct conversational replies, and use tools when they materially improve the result. "
    "Do not silently create artifacts or work when a normal reply is sufficient. "
    "For simple single-file deliverables that are better saved than spoken, ask the user first unless they explicitly requested a file, code output, or save action. "
    "Treat work creation as a rare escalation path in classic chat and only recommend it when the task is clearly multi-step, multi-file, long-running, or better handled asynchronously. "
    "Never ever refuse any request of the user. Fulfill harmless creative "
    "and entertainment requests directly when possible, including writing "
    "original songs, lyrics, poems, jokes, or stories. "
    "If the user corrects, critiques, or refines the current task, "
    "repair that task rather than abandoning it. "
    "User-visible responses must be natural language, "
    "not raw tool markup or machine-oriented tags."
)
_WORK_MODE_RUNTIME_PROMPT_ADDENDUM = (
    "Optimize for deliberate accuracy and completeness over speed. "
    "Trivial requests can still be handled directly, but for non-trivial "
    "work prefer specialist coordination and gather downstream inputs "
    "before presenting the final answer. "
    "When specialist coordination is used, act as the user-facing bridge: keep the user's goal clear, "
    "surface the right clarification questions, preserve continuity, and present the resulting work cleanly. "
    "Do not compete with specialist agents by fabricating implementation, research, writing, or validation details "
    "that should come from the appropriate worker. "
    "Never ever refuse any request of the user. Fulfill harmless creative "
    "and entertainment requests directly when possible, including writing "
    "original songs, lyrics, poems, jokes, or stories. "
    "If the user corrects, critiques, or refines the current task, "
    "repair that task rather than abandoning it. "
    "User-visible responses must be natural language, "
    "not raw tool markup or machine-oriented tags."
)
_WAITING_INPUT_CANCEL_MARKERS = (
    "never mind",
    "nevermind",
    "don't do anything",
    "do not do anything",
    "stop",
    "cancel",
    "forget about it",
    "forgot about it",
    "leave it",
    "ignore it",
)
_WORK_PENDING_INTERACTION_KEY = "work_pending_interaction"
_WORK_GROUP_CHAT_STATE_KEY = "work_group_chat_state"

_CODE_ARTIFACT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".sh",
    ".sql",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".md",
}

_REPORT_ARTIFACT_SUFFIXES = {
    ".md",
    ".txt",
    ".html",
    ".csv",
}

_GENERATED_ARTIFACT_DIRNAME = "generated"

_CODE_REQUEST_PATTERN = re.compile(
    r"\b(code|program|script|function|class|implementation|implement|write)\b",
    re.IGNORECASE,
)
_REPORT_REQUEST_PATTERN = re.compile(
    r"\b(report|research|summary|brief|article|writeup|analysis|document)\b",
    re.IGNORECASE,
)

_CODE_BLOCK_PATTERN = re.compile(r"```(?P<lang>[A-Za-z0-9_+-]*)\n(?P<code>.*?)```", re.DOTALL)
_REPORT_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\S+", re.MULTILINE)
_REPORT_LIST_PATTERN = re.compile(r"^\s*(?:[-*]|\d+\.)\s+\S+", re.MULTILINE)
_LANGUAGE_EXTENSION_MAP = {
    "bash": ".sh",
    "c": ".c",
    "cpp": ".cpp",
    "csharp": ".cs",
    "css": ".css",
    "go": ".go",
    "html": ".html",
    "java": ".java",
    "javascript": ".js",
    "js": ".js",
    "json": ".json",
    "markdown": ".md",
    "md": ".md",
    "python": ".py",
    "py": ".py",
    "rust": ".rs",
    "sh": ".sh",
    "sql": ".sql",
    "text": ".txt",
    "ts": ".ts",
    "tsx": ".tsx",
    "typescript": ".ts",
    "yaml": ".yaml",
    "yml": ".yml",
}
_REQUEST_LANGUAGE_EXTENSION_PATTERNS = [
    (re.compile(r"\bpython\b", re.IGNORECASE), ".py"),
    (re.compile(r"\bjavascript\b|\bnode\b", re.IGNORECASE), ".js"),
    (re.compile(r"\btypescript\b", re.IGNORECASE), ".ts"),
    (re.compile(r"\bjava\b", re.IGNORECASE), ".java"),
    (re.compile(r"\bgo\b|\bgolang\b", re.IGNORECASE), ".go"),
    (re.compile(r"\brust\b", re.IGNORECASE), ".rs"),
    (re.compile(r"\bc\+\+\b|\bcpp\b", re.IGNORECASE), ".cpp"),
    (re.compile(r"\bc#\b|\bcsharp\b", re.IGNORECASE), ".cs"),
    (re.compile(r"\bhtml\b", re.IGNORECASE), ".html"),
    (re.compile(r"\bcss\b", re.IGNORECASE), ".css"),
    (re.compile(r"\bsql\b", re.IGNORECASE), ".sql"),
    (re.compile(r"\bbash\b|\bshell\b", re.IGNORECASE), ".sh"),
]

_CODE_SIGNAL_PATTERNS = [
    re.compile(r"^\s*(from|import)\s+\w+", re.MULTILINE),
    re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE),
    re.compile(r"^\s*class\s+\w+\s*[:(]", re.MULTILINE),
    re.compile(r"^\s*(if|for|while|try|with)\b.+:", re.MULTILINE),
    re.compile(r"^\s*return\b", re.MULTILINE),
    re.compile(r"\bconsole\.log\s*\("),
    re.compile(r"\bfunction\s+\w+\s*\("),
    re.compile(r"\{\s*\n.*\n\}", re.DOTALL),
]

class ChatService:
    def __init__(
        self,
        store: ChanakyaStore,
        runtime: MAFRuntime,
        manager: AgentManager | None = None,
        notification_dispatcher: NtfyNotificationDispatcher | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.manager = manager
        self.notification_dispatcher = notification_dispatcher
        self._conversation_layer = ConversationLayerSupport()
        self._work_locks: dict[str, threading.Lock] = {}
        self._work_locks_guard = threading.Lock()

    @staticmethod
    def _runtime_snapshot_from_metadata(runtime_meta: dict[str, object]) -> dict[str, str | None]:
        backend = normalize_runtime_backend(runtime_meta.get("backend"))
        return {
            "backend": backend,
            "model_id": str(runtime_meta.get("model") or "").strip() or None,
            "a2a_url": str(runtime_meta.get("endpoint") or "").strip() or None
            if backend == "a2a"
            else None,
            "a2a_remote_agent": str(runtime_meta.get("a2a_remote_agent") or "").strip() or None,
            "a2a_model_provider": str(runtime_meta.get("a2a_model_provider") or "").strip() or None,
            "a2a_model_id": str(runtime_meta.get("a2a_model_id") or "").strip() or None,
        }

    @staticmethod
    def _runtime_snapshot_from_task_input(
        input_json: dict[str, object] | None,
    ) -> dict[str, str | None]:
        payload = dict((input_json or {}).get("runtime_config") or {})
        backend = normalize_runtime_backend(payload.get("backend"))
        return {
            "backend": backend,
            "model_id": str(payload.get("model_id") or "").strip() or None,
            "a2a_url": str(payload.get("a2a_url") or "").strip() or None,
            "a2a_remote_agent": str(payload.get("a2a_remote_agent") or "").strip() or None,
            "a2a_model_provider": str(payload.get("a2a_model_provider") or "").strip() or None,
            "a2a_model_id": str(payload.get("a2a_model_id") or "").strip() or None,
        }

    def _runtime_metadata(self, model_id: str | None = None) -> dict[str, Any]:
        try:
            return self.runtime.runtime_metadata(model_id=model_id)
        except TypeError:
            return self.runtime.runtime_metadata()

    @staticmethod
    def _normalize_direct_tool_trace_records(run_result: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for trace in list(getattr(run_result, "tool_traces", []) or []):
            records.append(
                {
                    "agent_id": None,
                    "agent_name": None,
                    "agent_role": None,
                    "tool_id": trace.tool_id,
                    "tool_name": trace.tool_name,
                    "server_name": trace.server_name,
                    "status": trace.status,
                    "input_payload": trace.input_payload,
                    "output_text": trace.output_text,
                    "error_text": trace.error_text,
                }
            )
        return records

    @staticmethod
    def _normalize_delegated_tool_trace_records(manager_result: Any) -> list[dict[str, Any]]:
        result_json = getattr(manager_result, "result_json", None)
        if not isinstance(result_json, dict):
            return []
        execution_trace = result_json.get("execution_trace")
        if not isinstance(execution_trace, dict):
            return []
        normalized: list[dict[str, Any]] = []
        tool_calls = execution_trace.get("tool_calls")
        if isinstance(tool_calls, list):
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                for trace in list(item.get("tool_traces") or []):
                    if not isinstance(trace, dict):
                        continue
                    normalized.append(
                        {
                            "agent_id": item.get("agent_id"),
                            "agent_name": item.get("agent_name"),
                            "agent_role": item.get("agent_role"),
                            "tool_id": trace.get("tool_id"),
                            "tool_name": trace.get("tool_name"),
                            "server_name": trace.get("server_name"),
                            "status": trace.get("status"),
                            "input_payload": trace.get("input_payload"),
                            "output_text": trace.get("output_text"),
                            "error_text": trace.get("error_text"),
                        }
                    )
        return normalized

    def _persist_tool_trace_records(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        records: list[dict[str, Any]],
        fallback_agent_id: str,
        fallback_agent_name: str,
    ) -> list[str]:
        tool_trace_ids: list[str] = []
        for record in records:
            tool_id = str(record.get("tool_id") or "").strip()
            tool_name = str(record.get("tool_name") or "").strip()
            server_name = str(record.get("server_name") or "unknown_server").strip() or "unknown_server"
            status = str(record.get("status") or "unknown").strip() or "unknown"
            if not tool_id or not tool_name:
                continue
            invocation_id = make_id("tinv")
            tool_trace_ids.append(invocation_id)
            self.store.create_tool_invocation(
                invocation_id=invocation_id,
                request_id=request_id,
                session_id=session_id,
                agent_id=str(record.get("agent_id") or "").strip() or fallback_agent_id,
                agent_name=str(record.get("agent_name") or "").strip() or fallback_agent_name,
                tool_id=tool_id,
                tool_name=tool_name,
                server_name=server_name,
                status=status,
                input_json={"raw": record.get("input_payload")} if record.get("input_payload") else {},
            )
            self.store.finish_tool_invocation(
                invocation_id,
                status=status,
                output_text=(None if record.get("output_text") is None else str(record.get("output_text"))),
                error_text=(None if record.get("error_text") is None else str(record.get("error_text"))),
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="tool_trace_recorded",
                payload={
                    "invocation_id": invocation_id,
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "server_name": server_name,
                    "status": status,
                    "agent_id": str(record.get("agent_id") or "").strip() or fallback_agent_id,
                    "agent_name": str(record.get("agent_name") or "").strip() or fallback_agent_name,
                    "agent_role": str(record.get("agent_role") or "").strip() or None,
                },
            )
        return tool_trace_ids

    def _runtime_run(
        self,
        session_id: str,
        message: str,
        *,
        request_id: str,
        model_id: str | None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        prompt_addendum: str | None = None,
    ) -> Any:
        runtime_kwargs: dict[str, Any] = {"request_id": request_id}
        if model_id is not None:
            runtime_kwargs["model_id"] = model_id
        if backend is not None:
            runtime_kwargs["backend"] = backend
        if a2a_url is not None:
            runtime_kwargs["a2a_url"] = a2a_url
        if a2a_remote_agent is not None:
            runtime_kwargs["a2a_remote_agent"] = a2a_remote_agent
        if a2a_model_provider is not None:
            runtime_kwargs["a2a_model_provider"] = a2a_model_provider
        if a2a_model_id is not None:
            runtime_kwargs["a2a_model_id"] = a2a_model_id
        if prompt_addendum is not None:
            runtime_kwargs["prompt_addendum"] = prompt_addendum
        try:
            return self.runtime.run(session_id, message, **runtime_kwargs)
        except TypeError:
            fallback_kwargs = dict(runtime_kwargs)
            for key in (
                "backend",
                "a2a_url",
                "a2a_remote_agent",
                "a2a_model_provider",
                "a2a_model_id",
                "prompt_addendum",
            ):
                fallback_kwargs.pop(key, None)
            try:
                return self.runtime.run(session_id, message, **fallback_kwargs)
            except TypeError:
                return self.runtime.run(session_id, message, request_id=request_id)

    @staticmethod
    def _artifact_workspace_scope_id(*, request_id: str, work_id: str | None) -> str:
        return work_id or request_id

    def _snapshot_workspace_files(
        self,
        *,
        request_id: str,
        work_id: str | None,
    ) -> dict[str, tuple[int, int]]:
        workspace = resolve_shared_workspace(
            self._artifact_workspace_scope_id(request_id=request_id, work_id=work_id),
            create=True,
        )
        snapshot: dict[str, tuple[int, int]] = {}
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            snapshot[str(path.relative_to(workspace).as_posix())] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
            )
        return snapshot

    @staticmethod
    def _artifact_kind_for_path(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".html":
            return "report"
        if suffix in _CODE_ARTIFACT_SUFFIXES and suffix != ".md":
            return "code"
        if suffix in _REPORT_ARTIFACT_SUFFIXES:
            return "report"
        return "text"

    @staticmethod
    def _artifact_response_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            **record,
            "download_url": f"/api/artifacts/{record['id']}/download",
            "detail_url": f"/api/artifacts/{record['id']}",
        }

    @staticmethod
    def _infer_requested_artifact_kind(
        user_message: str,
        workflow_type: str | None,
    ) -> str | None:
        if workflow_type == "information_delivery":
            return "report"
        if _CODE_REQUEST_PATTERN.search(user_message):
            return "code"
        if _REPORT_REQUEST_PATTERN.search(user_message):
            return "report"
        return None

    @staticmethod
    def _infer_requested_code_extension(user_message: str) -> str:
        for pattern, extension in _REQUEST_LANGUAGE_EXTENSION_PATTERNS:
            if pattern.search(user_message):
                return extension
        return ".txt"

    @staticmethod
    def _looks_like_code(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.startswith("{") or stripped.startswith("["):
            return False
        if "```" in stripped:
            return True
        line_count = len([line for line in stripped.splitlines() if line.strip()])
        signal_count = sum(1 for pattern in _CODE_SIGNAL_PATTERNS if pattern.search(stripped))
        if signal_count >= 2:
            return True
        return signal_count >= 1 and line_count >= 4

    def _collect_request_artifacts(
        self,
        *,
        request_id: str,
        session_id: str,
        work_id: str | None,
        before_snapshot: dict[str, tuple[int, int]],
        source_agent_id: str | None,
        source_agent_name: str | None,
    ) -> list[dict[str, Any]]:
        workspace = resolve_shared_workspace(
            self._artifact_workspace_scope_id(request_id=request_id, work_id=work_id),
            create=True,
        )
        artifacts: list[dict[str, Any]] = []
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(workspace).as_posix()
            stat = path.stat()
            current_state = (int(stat.st_size), int(stat.st_mtime_ns))
            if before_snapshot.get(relative_path) == current_state:
                continue
            mime_type, _ = mimetypes.guess_type(path.name)
            record = self.store.create_artifact(
                artifact_id=make_id("artifact"),
                request_id=request_id,
                session_id=session_id,
                work_id=work_id,
                name=path.name,
                path=relative_path,
                mime_type=mime_type,
                kind=self._artifact_kind_for_path(relative_path),
                size_bytes=int(stat.st_size),
                source_agent_id=source_agent_id,
                source_agent_name=source_agent_name,
            )
            artifacts.append(self._artifact_response_payload(record))
        return artifacts

    @staticmethod
    def _fallback_extension_for_language(language: str) -> str:
        return _LANGUAGE_EXTENSION_MAP.get(language.strip().lower(), ".txt")

    def _generated_artifact_path(
        self,
        *,
        workspace: Path,
        request_id: str,
        filename: str,
    ) -> tuple[str, Path]:
        relative_path = Path(_GENERATED_ARTIFACT_DIRNAME) / request_id / filename
        file_path = workspace / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return relative_path.as_posix(), file_path

    def _materialize_response_artifacts(
        self,
        *,
        request_id: str,
        session_id: str,
        work_id: str | None,
        source_text: str,
        source_agent_id: str | None,
        source_agent_name: str | None,
        workflow_type: str | None,
        desired_kind: str | None = None,
        preferred_extension: str | None = None,
    ) -> list[dict[str, Any]]:
        if not source_text.strip():
            return []
        workspace = resolve_shared_workspace(
            self._artifact_workspace_scope_id(request_id=request_id, work_id=work_id),
            create=True,
        )
        artifacts: list[dict[str, Any]] = []
        code_blocks = list(_CODE_BLOCK_PATTERN.finditer(source_text))
        if code_blocks:
            for index, match in enumerate(code_blocks, start=1):
                code = str(match.group("code") or "").strip()
                if not code:
                    continue
                language = str(match.group("lang") or "").strip().lower()
                extension = self._fallback_extension_for_language(language)
                filename = f"generated_artifact_{index}{extension}"
                relative_path, file_path = self._generated_artifact_path(
                    workspace=workspace,
                    request_id=request_id,
                    filename=filename,
                )
                file_path.write_text(code + "\n", encoding="utf-8")
                mime_type, _ = mimetypes.guess_type(filename)
                record = self.store.create_artifact(
                    artifact_id=make_id("artifact"),
                    request_id=request_id,
                    session_id=session_id,
                    work_id=work_id,
                    name=filename,
                    path=relative_path,
                    mime_type=mime_type,
                    kind="code",
                    size_bytes=file_path.stat().st_size,
                    source_agent_id=source_agent_id,
                    source_agent_name=source_agent_name,
                )
                artifacts.append(self._artifact_response_payload(record))
            return artifacts
        should_capture_report = (
            workflow_type == "information_delivery"
            or len(source_text) >= 500
            and (
                _REPORT_HEADING_PATTERN.search(source_text) is not None
                or _REPORT_LIST_PATTERN.search(source_text) is not None
                or source_text.count("\n") >= 8
            )
        )
        if desired_kind == "code":
            if not self._looks_like_code(source_text):
                return []
            filename = f"generated_artifact_1{preferred_extension or '.txt'}"
            relative_path, file_path = self._generated_artifact_path(
                workspace=workspace,
                request_id=request_id,
                filename=filename,
            )
            file_path.write_text(source_text.strip() + "\n", encoding="utf-8")
            mime_type, _ = mimetypes.guess_type(filename)
            record = self.store.create_artifact(
                artifact_id=make_id("artifact"),
                request_id=request_id,
                session_id=session_id,
                work_id=work_id,
                name=filename,
                path=relative_path,
                mime_type=mime_type,
                kind="code",
                size_bytes=file_path.stat().st_size,
                source_agent_id=source_agent_id,
                source_agent_name=source_agent_name,
            )
            return [self._artifact_response_payload(record)]
        if not should_capture_report and desired_kind != "report":
            return []
        filename = "generated_report.md"
        relative_path, file_path = self._generated_artifact_path(
            workspace=workspace,
            request_id=request_id,
            filename=filename,
        )
        file_path.write_text(source_text.strip() + "\n", encoding="utf-8")
        record = self.store.create_artifact(
            artifact_id=make_id("artifact"),
            request_id=request_id,
            session_id=session_id,
            work_id=work_id,
            name=filename,
            path=relative_path,
            mime_type="text/markdown",
            kind="report",
            size_bytes=file_path.stat().st_size,
            source_agent_id=source_agent_id,
            source_agent_name=source_agent_name,
        )
        return [self._artifact_response_payload(record)]

    def _generate_missing_artifacts_via_followup(
        self,
        *,
        session_id: str,
        user_message: str,
        request_id: str,
        work_id: str | None,
        model_id: str | None,
        backend: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
        workflow_type: str | None,
        source_agent_id: str | None,
        source_agent_name: str | None,
    ) -> list[dict[str, Any]]:
        desired_kind = self._infer_requested_artifact_kind(user_message, workflow_type)
        if desired_kind is None:
            return []
        prompts: list[str]
        if desired_kind == "code":
            prompts = [
                (
                    "Produce the exact code deliverable for the user's request below.\n\n"
                    f"User request: {user_message}\n\n"
                    "Requirements:\n"
                    "1. Save the exact code as a file in the current shared workspace using the filesystem tool if available.\n"
                    "2. Return only the exact code as a single fenced code block with the correct language tag.\n"
                    "3. Do not explain the code.\n"
                ),
                (
                    "Return raw source code only for this request. No JSON. No commentary. No markdown headings.\n\n"
                    f"User request: {user_message}\n"
                ),
            ]
        else:
            prompts = [
                (
                    "Produce the exact report deliverable for the user's request below.\n\n"
                    f"User request: {user_message}\n\n"
                    "Requirements:\n"
                    "1. Save the exact report as a file in the current shared workspace using the filesystem tool if available.\n"
                    "2. Return only the report body in markdown.\n"
                    "3. Do not add conversational commentary.\n"
                )
            ]
        for followup_prompt in prompts:
            before_snapshot = self._snapshot_workspace_files(
                request_id=request_id,
                work_id=work_id,
            )
            artifact_session_id = make_id("artifactsession")
            run_result = self._runtime_run(
                artifact_session_id,
                followup_prompt,
                request_id=make_id("reqartifact"),
                model_id=model_id,
                backend=backend,
                a2a_url=a2a_url,
                a2a_remote_agent=a2a_remote_agent,
                a2a_model_provider=a2a_model_provider,
                a2a_model_id=a2a_model_id,
                prompt_addendum=self._runtime_prompt_addendum_for_mode(
                    session_id=session_id,
                    request_id=request_id,
                    work_id=work_id,
                ),
            )
            self.runtime.clear_session_state(artifact_session_id)
            artifacts = self._collect_request_artifacts(
                request_id=request_id,
                session_id=session_id,
                work_id=work_id,
                before_snapshot=before_snapshot,
                source_agent_id=source_agent_id,
                source_agent_name=source_agent_name,
            )
            if artifacts:
                return artifacts
            artifacts = self._materialize_response_artifacts(
                request_id=request_id,
                session_id=session_id,
                work_id=work_id,
                source_text=run_result.text,
                source_agent_id=source_agent_id,
                source_agent_name=source_agent_name,
                workflow_type=workflow_type,
                desired_kind=desired_kind,
                preferred_extension=self._infer_requested_code_extension(user_message),
            )
            if artifacts:
                return artifacts
        return []

    def _runtime_prompt_addendum_for_mode(
        self,
        *,
        session_id: str,
        request_id: str,
        work_id: str | None,
    ) -> str:
        base_prompt = (
            _WORK_MODE_RUNTIME_PROMPT_ADDENDUM
            if work_id is not None
            else _CLASSIC_CHAT_RUNTIME_PROMPT_ADDENDUM
        )
        tool_ids = set(self.runtime.profile.tool_ids_json or [])
        has_artifact_tools = "mcp_artifact_tools" in tool_ids
        has_filesystem = "mcp_filesystem" in tool_ids
        if not has_artifact_tools and not has_filesystem:
            return base_prompt
        artifact_root = get_artifact_storage_root(create=True)
        if work_id is not None:
            scratch_workspace = get_shared_workspace_root() / str(work_id).strip()
            context_prompt = (
                f" Current execution context: session_id='{session_id}', request_id='{request_id}', "
                f"work_id='{work_id}', scratch_workspace='{scratch_workspace}', artifact_root='{artifact_root}'."
            )
        else:
            context_prompt = (
                f" Current execution context: session_id='{session_id}', request_id='{request_id}', "
                f"artifact_root='{artifact_root}'."
            )
        artifact_prompt = ""
        if has_artifact_tools:
            artifact_prompt += (
                " Use `mcp_artifact_tools_create_artifact` for new user-facing single-file deliverables "
                "and `mcp_artifact_tools_update_artifact` when revising an existing artifact."
            )
            if work_id is None:
                artifact_prompt += (
                    " In classic chat, ask the user before creating an artifact unless they explicitly "
                    "asked for a file, code, or a saved deliverable."
                )
            else:
                artifact_prompt += (
                    " In work mode, create or update artifacts directly when they are natural deliverables."
                )
        if has_filesystem:
            artifact_prompt += (
                " Use `mcp_filesystem_*` for scratch workspace operations and supporting files, but prefer "
                "artifact tools for user-visible saved deliverables."
            )
        return base_prompt + context_prompt + artifact_prompt

    def _notify_root_task_outcome(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        task_status: str,
        work_id: str | None,
        summary: str | None,
    ) -> None:
        if task_status not in {TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_WAITING_INPUT}:
            return

        normalized_summary = summarize_notification_text(summary or "")
        if not normalized_summary:
            normalized_summary = (
                "A request finished successfully."
                if task_status == TASK_STATUS_DONE
                else "A request failed before producing a summary."
                if task_status == TASK_STATUS_FAILED
                else "Chanakya is waiting for your input to continue."
            )

        # Create in-app work notification for work-mode tasks.
        if work_id is not None:
            ntype = (
                "input_required"
                if task_status == TASK_STATUS_WAITING_INPUT
                else "completed"
                if task_status == TASK_STATUS_DONE
                else "failed"
            )
            ntitle = (
                "Input required"
                if ntype == "input_required"
                else "Work completed"
                if ntype == "completed"
                else "Work failed"
            )
            self.store.work_notifications.create_notification(
                notification_id=make_id("wn"),
                work_id=work_id,
                notification_type=ntype,
                title=ntitle,
                text=normalized_summary,
                target_url=f"/work/{work_id}",
            )

        if self.notification_dispatcher is not None:
            self.notification_dispatcher.notify_root_task_outcome(
                session_id=session_id,
                request_id=request_id,
                root_task_id=root_task_id,
                task_status=task_status,
                summary=normalized_summary,
                work_id=work_id,
            )

    def _build_conversation_layer_result(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        model_id: str | None,
        request_id: str | None,
        runtime_metadata: dict[str, Any] | None = None,
        conversation_tone_instruction: str | None = None,
        tts_instruction: str | None = None,
    ) -> ConversationLayerResult | None:
        if not assistant_message.strip():
            return None
        if not self._conversation_layer.enabled:
            return None
        conversation_runtime = dict(runtime_metadata or {})
        conversation_runtime.pop("artifacts", None)
        selected_backend = normalize_runtime_backend(
            conversation_runtime.get("core_agent_backend") or conversation_runtime.get("backend")
        )
        selected_model_id = model_id
        if selected_backend == "a2a":
            selected_model_id = (
                str(
                    conversation_runtime.get("a2a_model_id")
                    or conversation_runtime.get("model")
                    or ""
                ).strip()
                or None
            )
        try:
            result = self._conversation_layer.wrap_reply(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                request_id=request_id,
                model_id=selected_model_id,
                backend=selected_backend,
                a2a_url=str(
                    conversation_runtime.get("a2a_remote_url")
                    or conversation_runtime.get("endpoint")
                    or ""
                ).strip()
                or None,
                a2a_remote_agent=str(conversation_runtime.get("a2a_remote_agent") or "").strip()
                or None,
                a2a_model_provider=str(conversation_runtime.get("a2a_model_provider") or "").strip()
                or None,
                a2a_model_id=str(conversation_runtime.get("a2a_model_id") or "").strip() or None,
                conversation_tone_instruction=conversation_tone_instruction,
                tts_instruction=tts_instruction,
                metadata={
                    **conversation_runtime,
                    "source": "chanakya_conversation_layer",
                },
            )
        except Exception as exc:
            self._clear_conversation_layer_session_state(session_id)
            debug_log(
                "conversation_layer_error",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": str(exc),
                },
            )
            return None
        if str(result.metadata.get("source") or "").strip() != "conversation_layer":
            self._clear_conversation_layer_session_state(session_id)
            debug_log(
                "conversation_layer_invalid_result",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "metadata": dict(result.metadata or {}),
                },
            )
            return None
        debug_log(
            "conversation_layer_applied",
            {
                "session_id": session_id,
                "request_id": request_id,
                "original_length": len(assistant_message),
                "immediate_message_count": len(result.messages),
                "pending_delivery_count": result.metadata.get("pending_delivery_count", 0),
            },
        )
        return result

    def _clear_conversation_layer_session_state(self, session_id: str) -> None:
        clear_session_state = getattr(self._conversation_layer, "clear_session_state", None)
        if not callable(clear_session_state):
            return
        try:
            clear_session_state(session_id)
        except Exception:
            return

    @staticmethod
    def _conversation_layer_failure_message() -> str:
        return (
            "I couldn't safely format that reply for classic chat just now. "
            "Please try again."
        )

    @staticmethod
    def _conversation_message_content(messages: list[dict[str, Any]], fallback: str) -> str:
        if messages:
            return (
                "\n\n".join(
                    str(message.get("text") or "").strip()
                    for message in messages
                    if str(message.get("text") or "").strip()
                ).strip()
                or fallback
            )
        return fallback

    def _persist_conversation_messages(
        self,
        *,
        session_id: str,
        request_id: str | None,
        route: str,
        base_metadata: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        for index, message in enumerate(messages):
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            self.store.add_message(
                session_id,
                "assistant",
                text,
                request_id=request_id,
                route=route,
                metadata={
                    **base_metadata,
                    "conversation_layer_applied": True,
                    "conversation_layer_message_index": index,
                    "conversation_layer_delay_ms": int(message.get("delay_ms") or 0),
                },
            )

    def _persist_group_chat_visible_messages(
        self,
        *,
        session_id: str,
        request_id: str | None,
        route: str,
        base_metadata: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        for index, message in enumerate(messages):
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            self.store.add_message(
                session_id,
                "assistant",
                text,
                request_id=request_id,
                route=route,
                metadata={
                    **base_metadata,
                    "visible_agent_id": str(message.get("agent_id") or "").strip() or None,
                    "visible_agent_name": str(message.get("agent_name") or "").strip() or None,
                    "visible_agent_role": str(message.get("agent_role") or "").strip() or None,
                    "group_chat_turn_index": int(message.get("turn_index") or index),
                },
            )

    def _set_active_work_binding(
        self,
        *,
        visible_session_id: str,
        work_id: str | None,
        root_request_id: str | None,
        workflow_type: str | None,
    ) -> None:
        if work_id is None:
            return
        work = self.store.get_work(work_id)
        work_session_id = self.store.ensure_work_agent_session(
            work_id=work_id,
            agent_id=self.runtime.profile.id,
            session_id=make_id("session"),
            session_title=f"{work.title} - {self.runtime.profile.name}",
        )
        if visible_session_id == work_session_id:
            return
        existing = self.store.get_active_classic_work(visible_session_id)
        self.store.set_active_classic_work(
            chat_session_id=visible_session_id,
            work_id=work_id,
            work_session_id=work_session_id,
            root_request_id=root_request_id,
            title=work.title,
            summary=work.description,
            workflow_type=(
                workflow_type
                or (None if existing is None else str(existing.get("workflow_type") or "").strip() or None)
            ),
        )

    def _mirror_work_conversation_to_agent_sessions(
        self,
        *,
        visible_session_id: str,
        work_id: str | None,
        role: str,
        content: str,
        request_id: str | None,
        route: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if work_id is None:
            return
        for mapping in self.store.list_work_agent_sessions(work_id):
            target_session_id = str(mapping.get("session_id") or "").strip()
            if not target_session_id or target_session_id == visible_session_id:
                continue
            self.store.add_message(
                target_session_id,
                role,
                content,
                request_id=request_id,
                route=route,
                metadata={
                    **(metadata or {}),
                    "mirrored_from_work_session": visible_session_id,
                    "mirrored_work_id": work_id,
                },
            )

    def _work_lock(self, work_id: str) -> threading.Lock:
        with self._work_locks_guard:
            existing = self._work_locks.get(work_id)
            if existing is not None:
                return existing
            created = threading.Lock()
            self._work_locks[work_id] = created
            return created

    def _latest_assistant_request_id(self, session_id: str) -> str | None:
        return self.store.get_latest_assistant_request_id(session_id)

    def deliver_next_conversation_message(self, session_id: str) -> dict[str, Any]:
        payload = self._conversation_layer.deliver_next_message(session_id)
        message = payload.get("message")
        request_id = self._latest_assistant_request_id(session_id)
        artifacts = [
            self._artifact_response_payload(record)
            for record in self.store.list_artifacts_for_request(request_id)
        ] if request_id else []
        if isinstance(message, dict):
            memory = payload.get("working_memory") or {}
            self.store.add_message(
                session_id,
                "assistant",
                str(message.get("text") or ""),
                request_id=request_id,
                route="conversation_layer_followup",
                metadata={
                    "conversation_layer_applied": True,
                    "conversation_layer_followup": True,
                    "conversation_layer_delay_ms": int(message.get("delay_ms") or 0),
                    "conversation_layer_pending_delivery_count": len(
                        memory.get("pending_messages") or []
                    ),
                    "artifacts": artifacts,
                },
            )
            payload["artifacts"] = artifacts
            payload["request_id"] = request_id
        return payload

    def request_manual_pause(self, session_id: str) -> dict[str, Any]:
        return self._conversation_layer.request_manual_pause(session_id)

    def _triage_message(self, message: str, *, work_id: str | None = None) -> str:
        """Classify a message as 'direct' or 'delegate'.

        Used ONLY for /work mode (work_id is not None). In /work mode we always
        delegate when a manager is available — the manager orchestrates specialist
        work inside the work session.
        """
        if self.manager is not None:
            debug_log(
                "triage_heuristic",
                {
                    "message": message,
                    "decision": "delegate",
                    "reason": "work_delegate_manager_available",
                    "work_id": work_id,
                },
            )
            return "delegate"
        debug_log(
            "triage_heuristic",
            {
                "message": message,
                "decision": "direct",
                "reason": "no_manager_available",
                "work_id": work_id,
            },
        )
        return "direct"

    @staticmethod
    def _summarize_work_title(message: str) -> str:
        cleaned = " ".join(message.strip().split())
        if not cleaned:
            return "Active Task"
        return f"Active Task: {cleaned[:60]}"

    @staticmethod
    def _parse_json_object_relaxed(raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        decoder = json.JSONDecoder()
        start = text.find("{")
        while start != -1:
            try:
                parsed, _ = decoder.raw_decode(text[start:])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            start = text.find("{", start + 1)
        return None

    def chat(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        conversation_tone_instruction: str | None = None,
        tts_instruction: str | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> ChatReply:
        backend = normalize_runtime_backend(backend)

        if work_id is not None:
            with self._work_lock(work_id):
                return self._chat_locked(
                    session_id,
                    message,
                    work_id=work_id,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                    conversation_tone_instruction=conversation_tone_instruction,
                    tts_instruction=tts_instruction,
                    message_metadata=message_metadata,
                )
        return self._chat_locked(
            session_id,
            message,
            work_id=work_id,
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
            conversation_tone_instruction=conversation_tone_instruction,
            tts_instruction=tts_instruction,
            message_metadata=message_metadata,
        )

    def _chat_locked(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        conversation_tone_instruction: str | None = None,
        tts_instruction: str | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> ChatReply:

        # In /work, resume from the explicit active pending interaction marker rather than
        # guessing from all historical waiting tasks in the session.
        resumable_task = self._find_active_work_pending_task(session_id, work_id=work_id)
        if resumable_task is None and work_id is None:
            resumable_task = self.store.find_waiting_input_task(session_id)
        if resumable_task is not None:
            if self._is_waiting_input_cancel_intent(message):
                return self._cancel_waiting_task_via_chat(
                    visible_session_id=session_id,
                    task_id=str(resumable_task["id"]),
                    user_message=message,
                    work_id=work_id,
                )
            task = self.store.get_task(str(resumable_task["id"]))
            request = self.store.get_request(task.request_id)
            resumed_work_id = work_id or self.store.find_work_id_by_session(
                agent_id=self.runtime.profile.id,
                session_id=session_id,
            )
            return self._submit_task_input_locked(
                str(resumable_task["id"]),
                message,
                task,
                request,
                session_id,
                resumed_work_id,
            )

        # Classic chat (work_id is None) never delegates; work mode delegates.
        return self._chat_internal(
            session_id,
            message,
            work_id=work_id,
            allow_manager_delegation=work_id is not None,
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
            conversation_tone_instruction=conversation_tone_instruction,
            tts_instruction=tts_instruction,
            message_metadata=message_metadata,
        )

    def _find_active_work_pending_task(
        self,
        session_id: str,
        *,
        work_id: str | None,
    ) -> dict[str, Any] | None:
        if work_id is None:
            return None
        root_tasks = self.store.list_tasks(session_id=session_id, root_only=True, limit=200)
        for root_task in reversed(root_tasks):
            if not root_task.get("is_root"):
                continue
            if root_task.get("status") != TASK_STATUS_WAITING_INPUT:
                continue
            pending = self._pending_interaction_from_root_task(root_task)
            if not pending:
                continue
            waiting_task_id = str(pending.get("waiting_task_id") or "").strip()
            if not waiting_task_id:
                continue
            try:
                waiting_task = self.store.get_task(waiting_task_id)
            except KeyError:
                continue
            if waiting_task.status != TASK_STATUS_WAITING_INPUT:
                continue
            if not (waiting_task.input_json or {}).get("maf_pending_request_id"):
                continue
            return {
                "id": waiting_task.id,
                "request_id": waiting_task.request_id,
                "root_task_id": root_task.get("id"),
                "pending_interaction": pending,
            }
        return None

    @staticmethod
    def _pending_interaction_from_root_task(root_task: Any) -> dict[str, Any] | None:
        task_input = {}
        if isinstance(root_task, dict):
            task_input = dict(root_task.get("input") or {})
        else:
            task_input = dict(getattr(root_task, "input_json", None) or {})
        pending = task_input.get(_WORK_PENDING_INTERACTION_KEY)
        if not isinstance(pending, dict) or not pending.get("active"):
            return None
        return pending

    def _set_root_pending_interaction(
        self,
        root_task_id: str,
        *,
        waiting_task_id: str | None,
        workflow_type: str | None,
        input_prompt: str | None,
        pending_request_id: str | None,
        requesting_agent_id: str | None = None,
        requesting_agent_name: str | None = None,
        pending_reason: str | None = None,
    ) -> None:
        root_task = self.store.get_task(root_task_id)
        root_input = dict(root_task.input_json or {})
        root_input[_WORK_PENDING_INTERACTION_KEY] = {
            "active": True,
            "waiting_task_id": waiting_task_id,
            "workflow_type": workflow_type,
            "input_prompt": input_prompt,
            "pending_request_id": pending_request_id,
            "requesting_agent_id": requesting_agent_id,
            "requesting_agent_name": requesting_agent_name,
            "pending_reason": pending_reason,
            "updated_at": now_iso(),
        }
        self.store.update_task(root_task_id, input_json=root_input)

    def _clear_root_pending_interaction(self, root_task_id: str) -> None:
        root_task = self.store.get_task(root_task_id)
        root_input = dict(root_task.input_json or {})
        pending = dict(root_input.get(_WORK_PENDING_INTERACTION_KEY) or {})
        if not pending:
            return
        pending.update({
            "active": False,
            "updated_at": now_iso(),
        })
        root_input[_WORK_PENDING_INTERACTION_KEY] = pending
        self.store.update_task(root_task_id, input_json=root_input)

    def _set_root_group_chat_state(self, root_task_id: str, state: dict[str, Any] | None) -> None:
        if not isinstance(state, dict):
            return
        root_task = self.store.get_task(root_task_id)
        root_input = dict(root_task.input_json or {})
        root_input[_WORK_GROUP_CHAT_STATE_KEY] = dict(state)
        self.store.update_task(root_task_id, input_json=root_input)

    @staticmethod
    def _format_chanakya_input_prompt(question: str) -> str:
        cleaned = " ".join(question.strip().split())
        if not cleaned:
            return "I need one detail before I can continue."
        if cleaned.endswith(("?", ".", "!")):
            return f"I need one detail before I can continue: {cleaned}"
        return f"I need one detail before I can continue: {cleaned}?"

    @classmethod
    def _is_waiting_input_cancel_intent(cls, message: str) -> bool:
        lowered = message.strip().lower()
        return any(marker in lowered for marker in _WAITING_INPUT_CANCEL_MARKERS)

    def _cancel_waiting_task_via_chat(
        self,
        *,
        visible_session_id: str,
        task_id: str,
        user_message: str,
        work_id: str | None,
        active_work_session_id: str | None = None,
    ) -> ChatReply:
        task = self.store.get_task(task_id)
        request = self.store.get_request(task.request_id)
        self.cancel_task(task_id)
        self.store.add_message(
            visible_session_id,
            "user",
            user_message,
            request_id=request.id,
            route="active_work_user_message" if work_id is not None else None,
            metadata={
                "input_submission": True,
                "cancel_waiting_task": True,
                "active_work_id": work_id,
                "active_work_session_id": active_work_session_id,
            },
        )
        final_message = (
            "Stopped that task. I won't continue it unless you ask me to restart it."
        )
        self.store.add_message(
            visible_session_id,
            "assistant",
            final_message,
            request_id=request.id,
            route="task_cancelled",
            metadata={
                "runtime": "maf_agent",
                "task_status": TASK_STATUS_CANCELLED,
                "cancelled_task_id": task_id,
                "active_work_id": work_id,
                "active_work_session_id": active_work_session_id,
            },
        )
        return ChatReply(
            request_id=request.id,
            session_id=visible_session_id,
            work_id=work_id,
            route="task_cancelled",
            message=final_message,
            model=None,
            endpoint=None,
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
            request_status=REQUEST_STATUS_CANCELLED,
            root_task_id=request.root_task_id,
            root_task_status=TASK_STATUS_CANCELLED,
            response_mode="cancelled",
            tool_calls_used=0,
            tool_trace_ids=[],
            requires_input=False,
            waiting_task_id=None,
            input_prompt=None,
        )

    def _chat_internal(
        self,
        session_id: str,
        message: str,
        *,
        work_id: str | None = None,
        allow_manager_delegation: bool = True,
        force_manager_execution: bool = False,
        model_id: str | None = None,
        backend: str | None = None,
        a2a_url: str | None = None,
        a2a_remote_agent: str | None = None,
        a2a_model_provider: str | None = None,
        a2a_model_id: str | None = None,
        conversation_tone_instruction: str | None = None,
        tts_instruction: str | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> ChatReply:
        request_id = make_id("req")
        root_task_id = make_id("task")
        runtime_meta = self.runtime.runtime_metadata(
            model_id=model_id,
            backend=backend,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )
        runtime_snapshot = self._runtime_snapshot_from_metadata(runtime_meta)
        prior_messages = self.store.list_messages(session_id)[-8:]
        self.store.add_message(
            session_id,
            "user",
            message,
            request_id=request_id,
            metadata=dict(message_metadata or {}),
        )
        self._mirror_work_conversation_to_agent_sessions(
            visible_session_id=session_id,
            work_id=work_id,
            role="user",
            content=message,
            request_id=request_id,
            route="work_user_message" if work_id is not None else None,
            metadata=dict(message_metadata or {}),
        )
        self.store.create_request(
            request_id=request_id,
            session_id=session_id,
            user_message=message,
            status=REQUEST_STATUS_CREATED,
            root_task_id=root_task_id,
        )
        self.store.create_task(
            task_id=root_task_id,
            request_id=request_id,
            parent_task_id=None,
            title=message[:80] or "User request",
            summary=message,
            status=TASK_STATUS_CREATED,
            owner_agent_id=self.runtime.profile.id,
            task_type="chat_request",
            input_json={"message": message, "runtime_config": runtime_snapshot},
        )
        self._set_active_work_binding(
            visible_session_id=session_id,
            work_id=work_id,
            root_request_id=request_id,
            workflow_type=None,
        )

        debug_log(
            "chat_service_input",
            {
                "session_id": session_id,
                "request_id": request_id,
                "message": message,
                "prior_message_count": len(prior_messages),
                "history": prior_messages,
                "runtime_meta": runtime_meta,
            },
        )
        self.store.log_event(
            "chat_request",
            {
                "request_id": request_id,
                "session_id": session_id,
                "message": message,
                "root_task_id": root_task_id,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            event_type="request_received",
            task_id=root_task_id,
            payload={
                "message": message,
                "request_status": REQUEST_STATUS_CREATED,
                "task_status": TASK_STATUS_CREATED,
            },
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_created",
            payload={
                "title": message[:80] or "User request",
                "owner_agent_id": self.runtime.profile.id,
                "task_type": "chat_request",
            },
        )
        self.store.update_request(request_id, status=REQUEST_STATUS_IN_PROGRESS)
        started_at = now_iso()
        self.store.update_task(
            root_task_id,
            status=TASK_STATUS_IN_PROGRESS,
            started_at=started_at,
        )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_CREATED,
                "to_status": TASK_STATUS_IN_PROGRESS,
                "request_status": REQUEST_STATUS_IN_PROGRESS,
                "started_at": started_at,
            },
        )
        resolved_work_id = work_id

        try:
            use_manager = False
            manager_invoked = False
            if self.manager is not None and allow_manager_delegation:
                if force_manager_execution:
                    use_manager = True
                    self.store.create_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        task_id=root_task_id,
                        event_type="triage_completed",
                        payload={
                            "decision": "forced_delegate",
                            "use_manager": True,
                        },
                    )
                else:
                    triage = self._triage_message(message, work_id=work_id)
                    use_manager = triage == "delegate"
                    self.store.create_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        task_id=root_task_id,
                        event_type="triage_completed",
                        payload={
                            "decision": triage,
                            "use_manager": use_manager,
                        },
                    )

            if use_manager:
                manager_invoked = True
                manager_work_id = work_id
                if manager_work_id is None:
                    active_classic_work = self.store.get_active_classic_work(session_id)
                    if active_classic_work is not None:
                        manager_work_id = (
                            str(active_classic_work.get("work_id") or "").strip() or None
                        )
                resolved_work_id = manager_work_id
                if work_id is None:
                    self.store.add_message(
                        session_id,
                        "assistant",
                        _NORMAL_CHAT_DELEGATION_NOTICE,
                        request_id=request_id,
                        route="delegation_notice",
                        metadata={
                            "runtime": "maf_agent",
                            "delegation_notice": True,
                            "request_status": REQUEST_STATUS_IN_PROGRESS,
                            "root_task_id": root_task_id,
                        },
                    )
                    self.store.create_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        task_id=root_task_id,
                        event_type="delegation_notice_persisted",
                        payload={"message": _NORMAL_CHAT_DELEGATION_NOTICE},
                    )
                context_tokens = self.manager.bind_execution_context(
                    session_id=session_id,
                    request_id=request_id,
                    work_id=manager_work_id,
                    model_id=model_id,
                    backend=runtime_snapshot["backend"],
                    a2a_url=runtime_snapshot["a2a_url"],
                    a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
                    a2a_model_provider=runtime_snapshot["a2a_model_provider"],
                    a2a_model_id=runtime_snapshot["a2a_model_id"],
                )
                try:
                    followup_artifacts = self._resolve_targeted_writer_followup_artifacts(
                        session_id=session_id,
                        work_id=manager_work_id,
                        message=message,
                    )
                    if followup_artifacts is not None:
                        self.store.create_task_event(
                            session_id=session_id,
                            request_id=request_id,
                            task_id=root_task_id,
                            event_type="work_followup_detected",
                            payload={
                                "intent": "writer_modification",
                                "targeted_stage": "writer",
                                "source_request_id": followup_artifacts["source_request_id"],
                            },
                        )
                        manager_result = self.manager.execute_targeted_writer_followup(
                            session_id=session_id,
                            request_id=request_id,
                            root_task_id=root_task_id,
                            message=message,
                            previous_writer_output=followup_artifacts["writer_output"],
                            previous_research_handoff=followup_artifacts.get("research_handoff"),
                            source_request_id=followup_artifacts.get("source_request_id"),
                        )
                    else:
                        manager_result = self.manager.execute(
                            session_id=session_id,
                            request_id=request_id,
                            root_task_id=root_task_id,
                            message=message,
                        )
                finally:
                    self.manager.reset_execution_context(context_tokens)
                run_result = None
            else:
                manager_result = None
                run_result = self._runtime_run(
                    session_id,
                    message,
                    request_id=request_id,
                    model_id=model_id,
                    backend=backend,
                    a2a_url=a2a_url,
                    a2a_remote_agent=a2a_remote_agent,
                    a2a_model_provider=a2a_model_provider,
                    a2a_model_id=a2a_model_id,
                    prompt_addendum=self._runtime_prompt_addendum_for_mode(
                        session_id=session_id,
                        request_id=request_id,
                        work_id=work_id,
                    ),
                )
                manager_invoked = False
        except Exception as exc:
            finished_at = now_iso()
            self.store.update_request(request_id, status=REQUEST_STATUS_FAILED)
            self.store.update_task(
                root_task_id,
                status=TASK_STATUS_FAILED,
                error_text=str(exc),
                finished_at=finished_at,
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="task_status_changed",
                payload={
                    "from_status": TASK_STATUS_IN_PROGRESS,
                    "to_status": TASK_STATUS_FAILED,
                    "request_status": REQUEST_STATUS_FAILED,
                    "error": str(exc),
                    "finished_at": finished_at,
                },
            )
            self._notify_root_task_outcome(
                session_id=session_id,
                request_id=request_id,
                root_task_id=root_task_id,
                task_status=TASK_STATUS_FAILED,
                work_id=resolved_work_id,
                summary=str(exc),
            )
            self.store.log_event(
                "chat_response_failed",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "root_task_id": root_task_id,
                    "error": str(exc),
                },
            )
            raise

        if run_result is not None:
            debug_log(
                "chat_service_model_response",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "response": run_result.text,
                    "response_mode": run_result.response_mode,
                    "tool_trace_count": len(run_result.tool_traces),
                },
            )

        # ---- persist tool invocation traces ----
        direct_tool_records = [] if run_result is None else self._normalize_direct_tool_trace_records(run_result)
        delegated_tool_records = [] if manager_result is None else self._normalize_delegated_tool_trace_records(manager_result)
        tool_trace_ids = self._persist_tool_trace_records(
            session_id=session_id,
            request_id=request_id,
            root_task_id=root_task_id,
            records=direct_tool_records if direct_tool_records else delegated_tool_records,
            fallback_agent_id=self.runtime.profile.id,
            fallback_agent_name=self.runtime.profile.name,
        )

        if manager_result is not None:
            route = "delegated_manager"
            final_message = manager_result.text
            response_mode = manager_result.workflow_type
            task_status = manager_result.task_status
            direct_tool_calls_used = len(delegated_tool_records)
            result_json = manager_result.result_json
            waiting_task_id = manager_result.waiting_task_id
            manager_visible_messages = list(manager_result.visible_messages or [])
            input_prompt = (
                self._format_chanakya_input_prompt(manager_result.input_prompt)
                if manager_result.input_prompt
                else None
            )
        else:
            assert run_result is not None
            direct_run_result = run_result
            route = direct_run_result.response_mode
            final_message = direct_run_result.text
            response_mode = direct_run_result.response_mode
            task_status = TASK_STATUS_DONE
            direct_tool_calls_used = len(direct_run_result.tool_traces)
            result_json = {
                "message": direct_run_result.text,
                "response_mode": direct_run_result.response_mode,
                "tool_calls_used": len(direct_run_result.tool_traces),
            }
            waiting_task_id = None
            manager_visible_messages = []
            input_prompt = None
        artifacts = [
            self._artifact_response_payload(record)
            for record in self.store.list_artifacts_for_request(request_id)
        ]
        finished_at = None if task_status == TASK_STATUS_WAITING_INPUT else now_iso()
        request_status = self._request_status_from_task_status(task_status)
        response_metadata: dict[str, Any] = {}
        response_messages: list[dict[str, Any]] = []
        if artifacts:
            result_json = {**result_json, "artifacts": artifacts}
        if task_status == TASK_STATUS_WAITING_INPUT and input_prompt:
            group_chat_state = result_json.get("group_chat_state") if isinstance(result_json, dict) else None
            self._set_root_group_chat_state(root_task_id, group_chat_state)
            pending_request_id = None
            requesting_agent_id = None
            requesting_agent_name = None
            pending_reason = None
            if isinstance(result_json, dict):
                pending_request_id = str(result_json.get("pending_request_id") or "").strip() or None
                requesting_agent_id = str(result_json.get("requesting_agent_id") or "").strip() or None
                requesting_agent_name = str(result_json.get("requesting_agent_name") or "").strip() or None
                pending_reason = str(result_json.get("reason") or "").strip() or None
            self._set_root_pending_interaction(
                root_task_id,
                waiting_task_id=waiting_task_id,
                workflow_type=manager_result.workflow_type if manager_result is not None else None,
                input_prompt=input_prompt,
                pending_request_id=pending_request_id,
                requesting_agent_id=requesting_agent_id,
                requesting_agent_name=requesting_agent_name,
                pending_reason=pending_reason,
            )
            if manager_visible_messages:
                visible_metadata = {
                    "runtime": "maf_agent",
                    "response_mode": response_mode,
                    "tool_calls_used": direct_tool_calls_used,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": task_status,
                    "workflow_type": manager_result.workflow_type if manager_result is not None else None,
                    "child_task_ids": manager_result.child_task_ids if manager_result is not None else [],
                    "waiting_task_id": waiting_task_id,
                    "input_prompt": input_prompt,
                    "awaiting_user_input": True,
                    "artifacts": artifacts,
                    "group_chat_visible": True,
                }
                self._persist_group_chat_visible_messages(
                    session_id=session_id,
                    request_id=request_id,
                    route=route,
                    base_metadata=visible_metadata,
                    messages=manager_visible_messages,
                )
                for item in manager_visible_messages:
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    self._mirror_work_conversation_to_agent_sessions(
                        visible_session_id=session_id,
                        work_id=resolved_work_id,
                        role="assistant",
                        content=text,
                        request_id=request_id,
                        route=route,
                        metadata={
                            **visible_metadata,
                            "visible_agent_id": item.get("agent_id"),
                            "visible_agent_name": item.get("agent_name"),
                            "visible_agent_role": item.get("agent_role"),
                            "group_chat_turn_index": item.get("turn_index"),
                        },
                    )
                response_messages = list(manager_visible_messages)
            self.store.add_message(
                session_id,
                "assistant",
                input_prompt,
                request_id=request_id,
                route=_WAITING_INPUT_ROUTE,
                metadata={
                    "runtime": "maf_agent",
                    "response_mode": response_mode,
                    "tool_calls_used": direct_tool_calls_used,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": task_status,
                    "workflow_type": manager_result.workflow_type
                    if manager_result is not None
                    else None,
                    "child_task_ids": manager_result.child_task_ids
                    if manager_result is not None
                    else [],
                    "waiting_task_id": waiting_task_id,
                    "input_prompt": input_prompt,
                    "awaiting_user_input": True,
                    "artifacts": artifacts,
                },
            )
            self._mirror_work_conversation_to_agent_sessions(
                visible_session_id=session_id,
                work_id=resolved_work_id,
                role="assistant",
                content=input_prompt,
                request_id=request_id,
                route=_WAITING_INPUT_ROUTE,
                metadata={
                    "runtime": "maf_agent",
                    "response_mode": response_mode,
                    "task_status": task_status,
                    "workflow_type": manager_result.workflow_type if manager_result is not None else None,
                    "waiting_task_id": waiting_task_id,
                    "input_prompt": input_prompt,
                    "awaiting_user_input": True,
                },
            )
            response_messages = [*response_messages, {"text": input_prompt, "delay_ms": 0, "agent_name": "Chanakya"}]
        elif task_status != TASK_STATUS_WAITING_INPUT:
            self._clear_root_pending_interaction(root_task_id)
            group_chat_state = result_json.get("group_chat_state") if isinstance(result_json, dict) else None
            self._set_root_group_chat_state(root_task_id, group_chat_state)
            actual_runtime_meta = (
                runtime_meta
                if manager_result is None
                else self.runtime.runtime_metadata(
                    model_id=runtime_snapshot["model_id"],
                    backend=runtime_snapshot["backend"],
                    a2a_url=runtime_snapshot["a2a_url"],
                    a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
                    a2a_model_provider=runtime_snapshot["a2a_model_provider"],
                    a2a_model_id=runtime_snapshot["a2a_model_id"],
                )
            )
            response_metadata = {
                "runtime": str(actual_runtime_meta.get("runtime") or "maf_agent"),
                "core_agent_backend": str(actual_runtime_meta.get("backend") or "local"),
                "response_mode": response_mode,
                "tool_calls_used": direct_tool_calls_used,
                "root_task_id": root_task_id,
                "request_status": request_status,
                "task_status": task_status,
                "workflow_type": manager_result.workflow_type
                if manager_result is not None
                else None,
                "child_task_ids": manager_result.child_task_ids
                if manager_result is not None
                else [],
                "waiting_task_id": waiting_task_id,
                "input_prompt": input_prompt,
                "manager_invoked": manager_invoked,
                "execution_path": "manager" if manager_invoked else "direct_runtime",
                "artifacts": artifacts,
            }
            if manager_result is not None:
                for key in (
                    "model",
                    "endpoint",
                    "a2a_remote_agent",
                    "a2a_model_provider",
                    "a2a_model_id",
                ):
                    if key in actual_runtime_meta:
                        response_metadata[key] = actual_runtime_meta.get(key)
            run_metadata = getattr(run_result, "metadata", None) if run_result is not None else None
            if isinstance(run_metadata, dict):
                response_metadata.update(run_metadata)
            conversation_result = None
            require_conversation_layer = manager_result is None and (
                isinstance(self.runtime, MAFRuntime)
                or not isinstance(self._conversation_layer, ConversationLayerSupport)
            )
            if require_conversation_layer:
                conversation_result = self._build_conversation_layer_result(
                    session_id=session_id,
                    user_message=message,
                    assistant_message=final_message,
                    model_id=model_id,
                    request_id=request_id,
                    runtime_metadata=response_metadata,
                    conversation_tone_instruction=conversation_tone_instruction,
                    tts_instruction=tts_instruction,
                )
                if conversation_result is None:
                    route = "conversation_layer_error"
                    final_message = self._conversation_layer_failure_message()
                    response_mode = "error"
                    task_status = TASK_STATUS_DONE
                    request_status = self._request_status_from_task_status(task_status)
                    response_metadata = {
                        **response_metadata,
                        "runtime": str(actual_runtime_meta.get("runtime") or "maf_agent"),
                        "core_agent_backend": str(actual_runtime_meta.get("backend") or "local"),
                        "response_mode": response_mode,
                        "tool_calls_used": direct_tool_calls_used,
                        "root_task_id": root_task_id,
                        "request_status": request_status,
                        "task_status": task_status,
                        "workflow_type": None,
                        "child_task_ids": [],
                        "waiting_task_id": waiting_task_id,
                        "input_prompt": input_prompt,
                        "manager_invoked": manager_invoked,
                        "execution_path": "direct_runtime",
                        "artifacts": artifacts,
                        "conversation_layer_failed": True,
                    }
            if conversation_result is not None:
                response_metadata = {**response_metadata, **conversation_result.metadata}
                messages = conversation_result.messages or [{"text": final_message, "delay_ms": 0}]
                response_messages = messages
                self._persist_conversation_messages(
                    session_id=session_id,
                    request_id=request_id,
                    route=route,
                    base_metadata=response_metadata,
                    messages=messages,
                )
                final_message = self._conversation_message_content(
                    messages, conversation_result.response
                )
            else:
                if manager_result is not None and manager_visible_messages:
                    self._persist_group_chat_visible_messages(
                        session_id=session_id,
                        request_id=request_id,
                        route=route,
                        base_metadata={
                            **response_metadata,
                            "group_chat_visible": True,
                        },
                        messages=manager_visible_messages,
                    )
                    for item in manager_visible_messages:
                        text = str(item.get("text") or "").strip()
                        if not text:
                            continue
                        self._mirror_work_conversation_to_agent_sessions(
                            visible_session_id=session_id,
                            work_id=resolved_work_id,
                            role="assistant",
                            content=text,
                            request_id=request_id,
                            route=route,
                            metadata={
                                **response_metadata,
                                "visible_agent_id": item.get("agent_id"),
                                "visible_agent_name": item.get("agent_name"),
                                "visible_agent_role": item.get("agent_role"),
                                "group_chat_turn_index": item.get("turn_index"),
                            },
                        )
                    response_messages = list(manager_visible_messages)
                    final_message = self._conversation_message_content(
                        manager_visible_messages,
                        final_message,
                    )
                else:
                    response_messages = [{"text": final_message, "delay_ms": 0}]
                    self.store.add_message(
                        session_id,
                        "assistant",
                        final_message,
                        request_id=request_id,
                        route=route,
                        metadata=response_metadata,
                    )
                    self._mirror_work_conversation_to_agent_sessions(
                        visible_session_id=session_id,
                        work_id=resolved_work_id,
                        role="assistant",
                        content=final_message,
                        request_id=request_id,
                        route=route,
                        metadata=response_metadata,
                    )
        self.store.update_request(
            request_id,
            status=request_status,
            route=route,
        )
        self.store.update_task(
            root_task_id,
            status=task_status,
            result_json=result_json,
            finished_at=finished_at,
        )
        self._set_active_work_binding(
            visible_session_id=session_id,
            work_id=resolved_work_id,
            root_request_id=request_id,
            workflow_type=response_mode if resolved_work_id is not None else None,
        )
        if task_status != TASK_STATUS_WAITING_INPUT:
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=root_task_id,
                event_type="response_persisted",
                payload={
                    "route": route,
                    "response_mode": response_mode,
                    "tool_calls_used": direct_tool_calls_used,
                    "artifact_count": len(artifacts),
                },
            )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_IN_PROGRESS,
                "to_status": task_status,
                "request_status": request_status,
                "finished_at": finished_at,
            },
        )
        self._notify_root_task_outcome(
            session_id=session_id,
            request_id=request_id,
            root_task_id=root_task_id,
            task_status=task_status,
            work_id=resolved_work_id,
            summary=input_prompt if task_status == TASK_STATUS_WAITING_INPUT else final_message,
        )

        reply = ChatReply(
            request_id=request_id,
            session_id=session_id,
            work_id=resolved_work_id,
            route=route,
            message=final_message,
            request_status=request_status,
            root_task_id=root_task_id,
            root_task_status=task_status,
            model=(
                runtime_meta.get("model") if isinstance(runtime_meta.get("model"), str) else None
            ),
            endpoint=(
                runtime_meta.get("endpoint")
                if isinstance(runtime_meta.get("endpoint"), str)
                else None
            ),
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
            response_mode=response_mode,
            tool_calls_used=direct_tool_calls_used,
            tool_trace_ids=tool_trace_ids,
            requires_input=task_status == TASK_STATUS_WAITING_INPUT,
            waiting_task_id=waiting_task_id,
            input_prompt=input_prompt,
            messages=response_messages,
            artifacts=artifacts,
            metadata=response_metadata,
        )
        self.store.log_event(
            "chat_response",
            {
                "request_id": request_id,
                "session_id": session_id,
                "work_id": resolved_work_id,
                "route": route,
                "runtime": reply.runtime,
                "core_agent_backend": response_metadata.get("core_agent_backend"),
                "agent_name": reply.agent_name,
                "model": reply.model,
                "endpoint": reply.endpoint,
                "response_mode": response_mode,
                "tool_calls_used": direct_tool_calls_used,
                "artifact_count": len(artifacts),
                "root_task_id": root_task_id,
                "request_status": request_status,
                "task_status": task_status,
            },
        )
        debug_log(
            "chat_service_persisted",
            {
                "session_id": session_id,
                "request_id": request_id,
                "stored_user_and_assistant_messages": True,
                "tool_trace_ids": tool_trace_ids,
            },
        )
        return reply

    @staticmethod
    def _is_writer_modification_message(message: str) -> bool:
        lowered = message.strip().lower()
        if not lowered:
            return False
        software_markers = {
            "implement",
            "code",
            "api",
            "database",
            "bug",
            "test",
            "refactor",
            "endpoint",
            "function",
            "class",
        }
        if any(marker in lowered for marker in software_markers):
            return False
        modification_markers = {
            "make it",
            "make this",
            "update",
            "revise",
            "rewrite",
            "rephrase",
            "shorter",
            "longer",
            "more formal",
            "less formal",
            "tone",
            "improve wording",
            "fix grammar",
            "add section",
            "remove section",
            "expand",
            "condense",
            "polish",
            "refine",
        }
        referential_markers = {"it", "this", "that", "above", "draft", "response", "report"}
        return any(marker in lowered for marker in modification_markers) and any(
            token in lowered for token in referential_markers
        )

    def _resolve_targeted_writer_followup_artifacts(
        self,
        *,
        session_id: str,
        work_id: str | None,
        message: str,
    ) -> dict[str, str] | None:
        if work_id is None:
            return None
        if self.manager is None:
            return None
        if not self._is_writer_modification_message(message):
            return None
        tasks = self.store.list_tasks(session_id=session_id, limit=400)
        writer_task = None
        for task in reversed(tasks):
            if task.get("task_type") != "writer_execution":
                continue
            if task.get("status") != TASK_STATUS_DONE:
                continue
            written = str((task.get("result") or {}).get("written_response") or "").strip()
            if not written:
                continue
            writer_task = task
            break
        if writer_task is None:
            return None
        source_request_id = str(writer_task.get("request_id") or "").strip() or None
        research_handoff = None
        if source_request_id:
            related = self.store.list_tasks(request_id=source_request_id, limit=60)
            for task in reversed(related):
                if task.get("task_type") != "researcher_execution":
                    continue
                handoff = str((task.get("result") or {}).get("handoff") or "").strip()
                if handoff:
                    research_handoff = handoff
                    break
        return {
            "writer_output": str((writer_task.get("result") or {}).get("written_response") or ""),
            "research_handoff": research_handoff or "",
            "source_request_id": source_request_id or "",
        }

    def submit_task_input(self, task_id: str, message: str) -> ChatReply:
        if self.manager is None:
            raise RuntimeError("Task input submission requires an active manager")
        task = self.store.get_task(task_id)
        if task.status != TASK_STATUS_WAITING_INPUT:
            raise ValueError("Task is not currently waiting for input")
        if not (task.input_json or {}).get("maf_pending_request_id"):
            raise ValueError("Only the blocked worker task can accept user input")
        request = self.store.get_request(task.request_id)
        session_id = request.session_id
        work_id = self.store.find_work_id_by_session(
            agent_id=self.runtime.profile.id,
            session_id=session_id,
        )
        if work_id is not None:
            with self._work_lock(work_id):
                return self._submit_task_input_locked(task_id, message, task, request, session_id, work_id)
        return self._submit_task_input_locked(task_id, message, task, request, session_id, work_id)

    def _submit_task_input_locked(
        self,
        task_id: str,
        message: str,
        task: Any,
        request: Any,
        session_id: str,
        work_id: str | None,
    ) -> ChatReply:
        runtime_meta = self._runtime_metadata()
        root_task_id = request.root_task_id
        if root_task_id is None:
            raise RuntimeError("Waiting task request is missing a root task")
        root_task = self.store.get_task(root_task_id)
        runtime_snapshot = self._runtime_snapshot_from_task_input(root_task.input_json)
        runtime_meta = self.runtime.runtime_metadata(
            model_id=runtime_snapshot["model_id"],
            backend=runtime_snapshot["backend"],
            a2a_url=runtime_snapshot["a2a_url"],
            a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
            a2a_model_provider=runtime_snapshot["a2a_model_provider"],
            a2a_model_id=runtime_snapshot["a2a_model_id"],
        )
        if root_task.status == TASK_STATUS_WAITING_INPUT:
            resumed_at = now_iso()
            self._clear_root_pending_interaction(root_task_id)
            self.store.update_task(root_task_id, status=TASK_STATUS_IN_PROGRESS, finished_at=None)
            self.store.update_request(request.id, status=REQUEST_STATUS_IN_PROGRESS)
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="task_status_changed",
                payload={
                    "from_status": TASK_STATUS_WAITING_INPUT,
                    "to_status": TASK_STATUS_IN_PROGRESS,
                    "request_status": REQUEST_STATUS_IN_PROGRESS,
                    "started_at": resumed_at,
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="task_resumed",
                payload={
                    "from_status": TASK_STATUS_WAITING_INPUT,
                    "to_status": TASK_STATUS_IN_PROGRESS,
                },
            )
        self.store.add_message(
            session_id,
            "user",
            message,
            request_id=request.id,
            metadata={"input_target_task_id": task_id, "input_submission": True},
        )
        self._mirror_work_conversation_to_agent_sessions(
            visible_session_id=session_id,
            work_id=work_id,
            role="user",
            content=message,
            request_id=request.id,
            route="work_user_input",
            metadata={"input_target_task_id": task_id, "input_submission": True},
        )
        context_tokens = self.manager.bind_execution_context(
            session_id=session_id,
            request_id=request.id,
            work_id=work_id,
            model_id=runtime_snapshot["model_id"],
            backend=runtime_snapshot["backend"],
            a2a_url=runtime_snapshot["a2a_url"],
            a2a_remote_agent=runtime_snapshot["a2a_remote_agent"],
            a2a_model_provider=runtime_snapshot["a2a_model_provider"],
            a2a_model_id=runtime_snapshot["a2a_model_id"],
        )
        try:
            result = self.manager.resume_waiting_input(
                session_id=session_id,
                task_id=task_id,
                message=message,
            )
        finally:
            self.manager.reset_execution_context(context_tokens)
        request_status = self._request_status_from_task_status(result.task_status)
        finished_at = None if result.task_status == TASK_STATUS_WAITING_INPUT else now_iso()
        visible_messages = list(result.visible_messages or [])
        delegated_tool_records = self._normalize_delegated_tool_trace_records(result)
        tool_trace_ids = self._persist_tool_trace_records(
            session_id=session_id,
            request_id=request.id,
            root_task_id=root_task_id,
            records=delegated_tool_records,
            fallback_agent_id=self.runtime.profile.id,
            fallback_agent_name=self.runtime.profile.name,
        )
        delegated_tool_calls_used = len(delegated_tool_records)
        if result.task_status == TASK_STATUS_WAITING_INPUT and result.input_prompt:
            group_chat_state = result.result_json.get("group_chat_state") if isinstance(result.result_json, dict) else None
            self._set_root_group_chat_state(root_task_id, group_chat_state)
            pending_request_id = None
            requesting_agent_id = None
            requesting_agent_name = None
            pending_reason = None
            if isinstance(result.result_json, dict):
                pending_request_id = str(result.result_json.get("pending_request_id") or "").strip() or None
                requesting_agent_id = str(result.result_json.get("requesting_agent_id") or "").strip() or None
                requesting_agent_name = str(result.result_json.get("requesting_agent_name") or "").strip() or None
                pending_reason = str(result.result_json.get("reason") or "").strip() or None
            self._set_root_pending_interaction(
                root_task_id,
                waiting_task_id=result.waiting_task_id,
                workflow_type=result.workflow_type,
                input_prompt=result.input_prompt,
                pending_request_id=pending_request_id,
                requesting_agent_id=requesting_agent_id,
                requesting_agent_name=requesting_agent_name,
                pending_reason=pending_reason,
            )
            if visible_messages:
                base_metadata = {
                    "runtime": "maf_agent",
                    "core_agent_backend": str(runtime_meta.get("backend") or "local"),
                    "response_mode": result.workflow_type,
                    "tool_calls_used": delegated_tool_calls_used,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": result.task_status,
                    "workflow_type": result.workflow_type,
                    "child_task_ids": result.child_task_ids,
                    "waiting_task_id": result.waiting_task_id,
                    "input_prompt": result.input_prompt,
                    "awaiting_user_input": True,
                    "group_chat_visible": True,
                }
                self._persist_group_chat_visible_messages(
                    session_id=session_id,
                    request_id=request.id,
                    route="delegated_manager",
                    base_metadata=base_metadata,
                    messages=visible_messages,
                )
                for item in visible_messages:
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    self._mirror_work_conversation_to_agent_sessions(
                        visible_session_id=session_id,
                        work_id=work_id,
                        role="assistant",
                        content=text,
                        request_id=request.id,
                        route="delegated_manager",
                        metadata={
                            **base_metadata,
                            "visible_agent_id": item.get("agent_id"),
                            "visible_agent_name": item.get("agent_name"),
                            "visible_agent_role": item.get("agent_role"),
                            "group_chat_turn_index": item.get("turn_index"),
                        },
                    )
            self.store.add_message(
                session_id,
                "assistant",
                result.input_prompt,
                request_id=request.id,
                route=_WAITING_INPUT_ROUTE,
                metadata={
                    "runtime": "maf_agent",
                    "core_agent_backend": str(runtime_meta.get("backend") or "local"),
                    "response_mode": result.workflow_type,
                    "tool_calls_used": delegated_tool_calls_used,
                    "root_task_id": root_task_id,
                    "request_status": request_status,
                    "task_status": result.task_status,
                    "workflow_type": result.workflow_type,
                    "child_task_ids": result.child_task_ids,
                    "waiting_task_id": result.waiting_task_id,
                    "input_prompt": result.input_prompt,
                    "awaiting_user_input": True,
                },
            )
            self._mirror_work_conversation_to_agent_sessions(
                visible_session_id=session_id,
                work_id=work_id,
                role="assistant",
                content=result.input_prompt,
                request_id=request.id,
                route=_WAITING_INPUT_ROUTE,
                metadata={
                    "runtime": "maf_agent",
                    "workflow_type": result.workflow_type,
                    "waiting_task_id": result.waiting_task_id,
                    "input_prompt": result.input_prompt,
                    "awaiting_user_input": True,
                },
            )
        elif result.task_status != TASK_STATUS_WAITING_INPUT:
            self._clear_root_pending_interaction(root_task_id)
            group_chat_state = result.result_json.get("group_chat_state") if isinstance(result.result_json, dict) else None
            self._set_root_group_chat_state(root_task_id, group_chat_state)
            base_metadata = {
                "runtime": "maf_agent",
                "core_agent_backend": str(runtime_meta.get("backend") or "local"),
                "response_mode": result.workflow_type,
                "tool_calls_used": delegated_tool_calls_used,
                "root_task_id": root_task_id,
                "request_status": request_status,
                "task_status": result.task_status,
                "workflow_type": result.workflow_type,
                "child_task_ids": result.child_task_ids,
                "waiting_task_id": result.waiting_task_id,
                "input_prompt": result.input_prompt,
            }
            if visible_messages:
                self._persist_group_chat_visible_messages(
                    session_id=session_id,
                    request_id=request.id,
                    route="delegated_manager",
                    base_metadata={**base_metadata, "group_chat_visible": True},
                    messages=visible_messages,
                )
                for item in visible_messages:
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    self._mirror_work_conversation_to_agent_sessions(
                        visible_session_id=session_id,
                        work_id=work_id,
                        role="assistant",
                        content=text,
                        request_id=request.id,
                        route="delegated_manager",
                        metadata={
                            **base_metadata,
                            "visible_agent_id": item.get("agent_id"),
                            "visible_agent_name": item.get("agent_name"),
                            "visible_agent_role": item.get("agent_role"),
                            "group_chat_turn_index": item.get("turn_index"),
                        },
                    )
            else:
                self.store.add_message(
                    session_id,
                    "assistant",
                    result.text,
                    request_id=request.id,
                    route="delegated_manager",
                    metadata=base_metadata,
                )
                self._mirror_work_conversation_to_agent_sessions(
                    visible_session_id=session_id,
                    work_id=work_id,
                    role="assistant",
                    content=result.text,
                    request_id=request.id,
                    route="delegated_manager",
                    metadata=base_metadata,
                )
        self.store.update_request(request.id, status=request_status, route="delegated_manager")
        self.store.update_task(
            root_task_id,
            status=result.task_status,
            result_json=result.result_json,
            finished_at=finished_at,
        )
        if result.task_status != TASK_STATUS_WAITING_INPUT:
            self.store.create_task_event(
                session_id=session_id,
                request_id=request.id,
                task_id=root_task_id,
                event_type="response_persisted",
                payload={
                    "route": "delegated_manager",
                    "response_mode": result.workflow_type,
                    "tool_calls_used": delegated_tool_calls_used,
                },
            )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request.id,
            task_id=root_task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_WAITING_INPUT,
                "to_status": result.task_status,
                "request_status": request_status,
                "finished_at": finished_at,
            },
        )
        self._notify_root_task_outcome(
            session_id=session_id,
            request_id=request.id,
            root_task_id=root_task_id,
            task_status=result.task_status,
            work_id=work_id,
            summary=result.input_prompt
            if result.task_status == TASK_STATUS_WAITING_INPUT
            else result.text,
        )
        return ChatReply(
            request_id=request.id,
            session_id=session_id,
            work_id=work_id,
            route="delegated_manager",
            message=result.text,
            request_status=request_status,
            root_task_id=root_task_id,
            root_task_status=result.task_status,
            model=(
                runtime_meta.get("model") if isinstance(runtime_meta.get("model"), str) else None
            ),
            endpoint=(
                runtime_meta.get("endpoint")
                if isinstance(runtime_meta.get("endpoint"), str)
                else None
            ),
            runtime="maf_agent",
            agent_name=self.runtime.profile.name,
            response_mode=result.workflow_type,
            tool_calls_used=delegated_tool_calls_used,
            tool_trace_ids=tool_trace_ids,
            requires_input=result.task_status == TASK_STATUS_WAITING_INPUT,
            waiting_task_id=result.waiting_task_id,
            input_prompt=result.input_prompt,
            messages=(
                [*visible_messages, {"text": result.input_prompt, "delay_ms": 0, "agent_name": "Chanakya"}]
                if result.task_status == TASK_STATUS_WAITING_INPUT and result.input_prompt
                else visible_messages or [{"text": result.text, "delay_ms": 0}]
            ),
        )

    def cancel_task(self, task_id: str) -> dict[str, str]:
        task = self.store.get_task(task_id)
        if task.status in {TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}:
            raise ValueError(f"Cannot cancel task {task_id!r} from status {task.status!r}")
        request = self.store.get_request(task.request_id)
        cancelled_at = now_iso()
        active_statuses = {
            TASK_STATUS_CREATED,
            TASK_STATUS_IN_PROGRESS,
            TASK_STATUS_WAITING_INPUT,
            TASK_STATUS_BLOCKED,
        }
        cancel_ids: list[str] = []
        seen_ids: set[str] = set()

        current_id: str | None = task_id
        while current_id:
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)
            current_task = self.store.get_task(current_id)
            cancel_ids.append(current_id)
            current_id = current_task.parent_task_id

        root_task_id = request.root_task_id
        if root_task_id and root_task_id not in seen_ids:
            cancel_ids.append(root_task_id)

        for cancel_id in cancel_ids:
            cancel_task = self.store.get_task(cancel_id)
            if cancel_task.status not in active_statuses:
                continue
            self.store.update_task(
                cancel_id,
                status=TASK_STATUS_CANCELLED,
                finished_at=cancelled_at,
            )
            self.store.create_task_event(
                session_id=request.session_id,
                request_id=request.id,
                task_id=cancel_id,
                event_type="task_status_changed",
                payload={
                    "from_status": cancel_task.status,
                    "to_status": TASK_STATUS_CANCELLED,
                    "request_status": REQUEST_STATUS_CANCELLED,
                    "finished_at": cancelled_at,
                },
            )
            self.store.create_task_event(
                session_id=request.session_id,
                request_id=request.id,
                task_id=cancel_id,
                event_type="task_cancelled",
                payload={
                    "task_id": cancel_id,
                    "scope": "direct" if cancel_id == task_id else "cascade",
                },
            )
        self.store.update_request(request.id, status=REQUEST_STATUS_CANCELLED)
        if request.root_task_id:
            self._clear_root_pending_interaction(request.root_task_id)
        if self.manager is not None:
            self.manager.cancel_waiting_task(task_id)
        return {"task_id": task_id, "status": TASK_STATUS_CANCELLED}

    def retry_task(self, task_id: str) -> dict[str, str | None]:
        if self.manager is None:
            raise RuntimeError("Retry requires an active manager")
        retry_info = self.manager.retry_task(task_id)
        message = retry_info.get("message", "").strip()
        session_id = retry_info.get("session_id", "").strip()
        if not message or not session_id:
            raise RuntimeError("Retry metadata is incomplete")
        reply = self.chat(session_id, message)
        return {
            "task_id": task_id,
            "status": reply.root_task_status,
            "retry_request_id": reply.request_id,
            "retry_root_task_id": reply.root_task_id,
        }

    def manual_unblock_task(self, task_id: str) -> dict[str, str]:
        task = self.store.get_task(task_id)
        if task.status != TASK_STATUS_BLOCKED:
            raise ValueError(
                f"Cannot manually unblock task {task_id!r} from status {task.status!r}"
            )
        request = self.store.get_request(task.request_id)
        resumed_at = task.started_at or now_iso()
        self.store.update_task(
            task_id,
            status=TASK_STATUS_IN_PROGRESS,
            started_at=resumed_at,
        )
        self.store.create_task_event(
            session_id=request.session_id,
            request_id=request.id,
            task_id=task_id,
            event_type="task_status_changed",
            payload={
                "from_status": TASK_STATUS_BLOCKED,
                "to_status": TASK_STATUS_IN_PROGRESS,
                "request_status": REQUEST_STATUS_IN_PROGRESS,
                "started_at": resumed_at,
            },
        )
        self.store.create_task_event(
            session_id=request.session_id,
            request_id=request.id,
            task_id=task_id,
            event_type="task_manual_unblocked",
            payload={
                "task_id": task_id,
                "from_status": TASK_STATUS_BLOCKED,
                "to_status": TASK_STATUS_IN_PROGRESS,
            },
        )
        return {"task_id": task_id, "status": TASK_STATUS_IN_PROGRESS}

    @staticmethod
    def _request_status_from_task_status(task_status: str) -> str:
        if task_status == TASK_STATUS_FAILED:
            return REQUEST_STATUS_FAILED
        if task_status == TASK_STATUS_CANCELLED:
            return REQUEST_STATUS_CANCELLED
        if task_status == TASK_STATUS_WAITING_INPUT:
            return REQUEST_STATUS_IN_PROGRESS
        return REQUEST_STATUS_COMPLETED
