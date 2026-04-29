from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import Message

from chanakya.agent.runtime import MAFRuntime, build_profile_agent, create_openai_chat_client
from chanakya.config import get_long_term_memory_default_owner_id
from chanakya.debug import debug_log
from chanakya.domain import make_id, now_iso
from chanakya.model import AgentProfileModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import ChanakyaStore

_MEMORY_MANAGER_SYSTEM_PROMPT = (
    "You are the long-term memory manager. Your only job is to maintain accurate, minimal, "
    "durable memory about the user, the project, and stable working preferences. Do not store "
    "full conversations, temporary chatter, or uncertain guesses. You may return multiple memory "
    "operations in a single response. When information is ambiguous, do not guess. Return JSON only.\n\n"
    "Allowed operations: add, update, delete, noop.\n"
    "Return a JSON object with keys: status, summary, needs_clarification, clarification_question, "
    "retryable, error_code, error_detail, operations.\n"
    "Allowed status values: ok, needs_clarification, failed.\n"
    "Each operation must be an object with keys: op, memory_id, scope, type, subject, content, importance, confidence.\n"
    "Use memory_id only for update/delete. For add, memory_id should be null.\n"
    "Use delete for memories that should no longer be active.\n"
    "If nothing durable should be changed, return operations as an empty list and status='ok'.\n"
    "If the request is ambiguous, return status='needs_clarification' with a short clarification_question.\n"
    "If the request cannot be processed, return status='failed' with exact error_detail and retryable true or false."
)


@dataclass(slots=True)
class MemoryManagerResult:
    status: str
    summary: str
    needs_clarification: bool
    clarification_question: str | None
    retryable: bool
    error_code: str | None
    error_detail: str | None
    operations: list[dict[str, Any]]


class MemoryManagerService:
    def __init__(self, store: ChanakyaStore, *, owner_id: str | None = None) -> None:
        self.store = store
        configured_owner = str(owner_id or get_long_term_memory_default_owner_id()).strip()
        self.owner_id = configured_owner or "default_user"
        self._repo_root = Path(__file__).resolve().parents[2]

    def process_request_turn(self, *, session_id: str, request_id: str) -> MemoryManagerResult:
        messages = self.store.list_messages_for_request(request_id)
        active_memories = self.store.list_memories(
            owner_id=self.owner_id,
            status="active",
            session_id=session_id,
            limit=200,
        )
        prompt = self._build_background_prompt(
            session_id=session_id,
            request_id=request_id,
            messages=messages,
            active_memories=active_memories,
        )
        result = self._run_memory_manager(prompt_text=prompt, session_id=session_id)
        self._apply_result(
            result,
            session_id=session_id,
            request_id=request_id,
            source_messages=messages,
        )
        return result

    def handle_memory_request(
        self,
        *,
        memory_request: str,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = self._parse_memory_request_envelope(memory_request)
        effective_session_id = str(parsed.get("session_id") or session_id or "").strip() or None
        effective_request_id = str(parsed.get("request_id") or request_id or "").strip() or None
        user_request = str(parsed.get("request") or memory_request).strip()
        active_memories = self.store.list_memories(
            owner_id=self.owner_id,
            status="active",
            session_id=effective_session_id,
            limit=200,
        )
        recent_messages: list[dict[str, Any]] = []
        if effective_request_id:
            recent_messages = self.store.list_messages_for_request(effective_request_id)[-8:]
        elif effective_session_id:
            recent_messages = self.store.list_messages(effective_session_id)[-8:]
        prompt = self._build_user_request_prompt(
            user_request=user_request,
            session_id=effective_session_id,
            request_id=effective_request_id,
            active_memories=active_memories,
            request_envelope=parsed,
            recent_messages=recent_messages,
        )
        result = self._run_memory_manager(
            prompt_text=prompt,
            session_id=effective_session_id or make_id("memorysession"),
        )
        self._apply_result(
            result,
            session_id=effective_session_id,
            request_id=effective_request_id,
            source_messages=[],
        )
        return {
            "status": result.status,
            "summary": result.summary,
            "needs_clarification": result.needs_clarification,
            "clarification_question": result.clarification_question,
            "retryable": result.retryable,
            "error_code": result.error_code,
            "error_detail": result.error_detail,
            "operations": result.operations,
        }

    def _build_background_prompt(
        self,
        *,
        session_id: str,
        request_id: str,
        messages: list[dict[str, Any]],
        active_memories: list[dict[str, Any]],
    ) -> str:
        recent_messages = [
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "content": item.get("content"),
                "created_at": item.get("created_at"),
            }
            for item in messages[-8:]
        ]
        payload = {
            "mode": "background_turn_update",
            "owner_id": self.owner_id,
            "session_id": session_id,
            "request_id": request_id,
            "active_memories": active_memories,
            "recent_messages": recent_messages,
            "instruction": (
                "Inspect the recent conversation slice and decide whether durable memory should be added, "
                "updated, deleted, or left unchanged."
            ),
        }
        return json.dumps(payload, indent=2, ensure_ascii=True)

    def _build_user_request_prompt(
        self,
        *,
        user_request: str,
        session_id: str | None,
        request_id: str | None,
        active_memories: list[dict[str, Any]],
        request_envelope: dict[str, Any],
        recent_messages: list[dict[str, Any]],
    ) -> str:
        payload = {
            "mode": "explicit_memory_request",
            "owner_id": self.owner_id,
            "session_id": session_id,
            "request_id": request_id,
            "active_memories": active_memories,
            "memory_request": user_request,
            "request_envelope": request_envelope,
            "recent_messages": recent_messages,
            "instruction": (
                "Handle this memory-related request using the current memory state. "
                "If the request is ambiguous, return needs_clarification=true with a short question. "
                "Use recent_messages to resolve references like 'it', 'that', or 'my old name'."
            ),
        }
        return json.dumps(payload, indent=2, ensure_ascii=True)

    def _run_memory_manager(self, *, prompt_text: str, session_id: str) -> MemoryManagerResult:
        raw = self._run_memory_manager_text(prompt_text=prompt_text, session_id=session_id)
        return self._parse_manager_result(raw)

    def _run_memory_manager_text(self, *, prompt_text: str, session_id: str) -> str:
        profile = AgentProfileModel(
            id="agent_memory_manager",
            name="Memory Manager",
            role="memory_manager",
            system_prompt=_MEMORY_MANAGER_SYSTEM_PROMPT,
            personality="precise, durable, conservative",
            tool_ids_json=[],
            workspace=None,
            heartbeat_enabled=False,
            heartbeat_interval_seconds=300,
            heartbeat_file_path=None,
            is_active=True,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        client = create_openai_chat_client()
        agent, _ = build_profile_agent(
            profile,
            self.store.Session,
            client=client,
            include_history=False,
            store_inputs=False,
            store_outputs=False,
            usage_text=prompt_text,
            repo_root=self._repo_root,
        )

        async def _run() -> str:
            session = agent.create_session(session_id=session_id)
            response = await agent.run(
                Message(role="user", text=prompt_text),
                session=session,
                options={"store": False},
            )
            return MAFRuntime._extract_local_response_text(response)

        return str(run_in_maf_loop(_run()) or "").strip()

    def _parse_manager_result(self, raw: str) -> MemoryManagerResult:
        payload = self._extract_json_object(raw)
        operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
        normalized_ops: list[dict[str, Any]] = []
        for item in operations:
            if not isinstance(item, dict):
                continue
            normalized_ops.append(
                {
                    "op": str(item.get("op") or "noop").strip().lower() or "noop",
                    "memory_id": str(item.get("memory_id") or "").strip() or None,
                    "scope": str(item.get("scope") or "shared").strip() or "shared",
                    "type": str(item.get("type") or "fact").strip() or "fact",
                    "subject": str(item.get("subject") or "").strip(),
                    "content": str(item.get("content") or "").strip(),
                    "importance": self._bounded_importance(item.get("importance")),
                    "confidence": self._bounded_confidence(item.get("confidence")),
                }
            )
        return MemoryManagerResult(
            status=str(payload.get("status") or "ok").strip() or "ok",
            summary=str(payload.get("summary") or "").strip(),
            needs_clarification=bool(payload.get("needs_clarification", False)),
            clarification_question=(
                str(payload.get("clarification_question") or "").strip() or None
            ),
            retryable=bool(payload.get("retryable", False)),
            error_code=str(payload.get("error_code") or "").strip() or None,
            error_detail=str(payload.get("error_detail") or "").strip() or None,
            operations=normalized_ops,
        )

    def _apply_result(
        self,
        result: MemoryManagerResult,
        *,
        session_id: str | None,
        request_id: str | None,
        source_messages: list[dict[str, Any]],
    ) -> None:
        source_message_ids = [str(item.get("id") or "") for item in source_messages if item.get("id")]
        source_request_ids = [request_id] if request_id else []
        self.store.create_memory_event(
            owner_id=self.owner_id,
            session_id=session_id,
            request_id=request_id,
            event_type="memory_operations_proposed",
            payload={
                "status": result.status,
                "summary": result.summary,
                "needs_clarification": result.needs_clarification,
                "clarification_question": result.clarification_question,
                "retryable": result.retryable,
                "error_code": result.error_code,
                "error_detail": result.error_detail,
                "operations": result.operations,
                "source_message_ids": source_message_ids,
                "source_request_ids": source_request_ids,
            },
        )
        if result.status == "failed":
            self.store.create_memory_event(
                owner_id=self.owner_id,
                session_id=session_id,
                request_id=request_id,
                event_type="memory_extraction_failed",
                payload={
                    "summary": result.summary,
                    "retryable": result.retryable,
                    "error_code": result.error_code,
                    "error_detail": result.error_detail,
                    "source_message_ids": source_message_ids,
                    "source_request_ids": source_request_ids,
                },
            )
            return
        changed_ids: list[str] = []
        applied_operations: list[dict[str, Any]] = []
        for item in result.operations:
            op = str(item.get("op") or "noop")
            if op == "add":
                if not item.get("subject") or not item.get("content"):
                    continue
                record, event_type = self._apply_add_operation(
                    item=item,
                    session_id=session_id,
                    request_id=request_id,
                    source_message_ids=source_message_ids,
                    source_request_ids=source_request_ids,
                )
                if record is None:
                    continue
                changed_ids.append(str(record.get("id") or ""))
                applied_operations.append(
                    {
                        "op": "add",
                        "resolved_as": event_type,
                        "memory_id": str(record.get("id") or ""),
                        "subject": record.get("subject"),
                        "type": record.get("type"),
                    }
                )
            elif op == "update":
                memory_id = self._resolve_existing_memory_id(
                    memory_id=str(item.get("memory_id") or "").strip() or None,
                    session_id=session_id,
                    subject=str(item.get("subject") or "").strip(),
                    memory_type=str(item.get("type") or "fact").strip() or "fact",
                    content=str(item.get("content") or "").strip(),
                )
                if not memory_id:
                    continue
                try:
                    updated = self.store.update_memory(
                        memory_id,
                        scope=str(item.get("scope") or "shared"),
                        type=str(item.get("type") or "fact"),
                        subject=str(item.get("subject") or "").strip(),
                        content=str(item.get("content") or "").strip(),
                        importance=self._bounded_importance(item.get("importance")),
                        confidence=self._bounded_confidence(item.get("confidence")),
                        source_message_ids=source_message_ids,
                        source_request_ids=source_request_ids,
                    )
                except KeyError:
                    continue
                changed_ids.append(str(updated.get("id") or ""))
                self.store.create_memory_event(
                    owner_id=self.owner_id,
                    session_id=session_id,
                        request_id=request_id,
                        memory_id=memory_id,
                        event_type="memory_updated",
                    payload={
                        "subject": updated.get("subject"),
                        "type": updated.get("type"),
                        "source_message_ids": source_message_ids,
                        "source_request_ids": source_request_ids,
                    },
                )
                applied_operations.append(
                    {
                        "op": "update",
                        "memory_id": memory_id,
                        "subject": updated.get("subject"),
                        "type": updated.get("type"),
                    }
                )
            elif op == "delete":
                memory_id = self._resolve_existing_memory_id(
                    memory_id=str(item.get("memory_id") or "").strip() or None,
                    session_id=session_id,
                    subject=str(item.get("subject") or "").strip(),
                    memory_type=str(item.get("type") or "fact").strip() or "fact",
                    content=str(item.get("content") or "").strip(),
                )
                if not memory_id:
                    continue
                try:
                    self.store.update_memory(memory_id, status="deleted")
                except KeyError:
                    continue
                changed_ids.append(memory_id)
                self.store.create_memory_event(
                    owner_id=self.owner_id,
                    session_id=session_id,
                    request_id=request_id,
                    memory_id=memory_id,
                    event_type="memory_deleted",
                    payload={
                        "reason": result.summary or "deleted by memory manager",
                        "source_message_ids": source_message_ids,
                        "source_request_ids": source_request_ids,
                    },
                )
                applied_operations.append({"op": "delete", "memory_id": memory_id})
        if applied_operations:
            self.store.create_memory_event(
                owner_id=self.owner_id,
                session_id=session_id,
                request_id=request_id,
                event_type="memory_operations_applied",
                payload={
                    "status": result.status,
                    "summary": result.summary,
                    "operations_applied": applied_operations,
                    "memory_ids": changed_ids,
                    "source_message_ids": source_message_ids,
                    "source_request_ids": source_request_ids,
                },
            )
        if not changed_ids:
            self.store.create_memory_event(
                owner_id=self.owner_id,
                session_id=session_id,
                request_id=request_id,
                event_type="memory_extraction_skipped",
                payload={
                    "reason": "no_operations_applied",
                    "summary": result.summary,
                    "needs_clarification": result.needs_clarification,
                    "clarification_question": result.clarification_question,
                    "retryable": result.retryable,
                    "error_code": result.error_code,
                    "error_detail": result.error_detail,
                    "source_message_ids": source_message_ids,
                    "source_request_ids": source_request_ids,
                },
            )

    def _apply_add_operation(
        self,
        *,
        item: dict[str, Any],
        session_id: str | None,
        request_id: str | None,
        source_message_ids: list[str],
        source_request_ids: list[str],
    ) -> tuple[dict[str, Any] | None, str]:
        scope = str(item.get("scope") or "shared")
        memory_type = str(item.get("type") or "fact").strip() or "fact"
        subject = str(item.get("subject") or "").strip()
        content = str(item.get("content") or "").strip()
        active = self.store.list_memories(
            owner_id=self.owner_id,
            status="active",
            session_id=session_id,
            limit=200,
        )
        exact_match = self._find_exact_active_match(
            active,
            subject=subject,
            memory_type=memory_type,
            content=content,
        )
        if exact_match is not None:
            updated = self.store.update_memory(
                str(exact_match.get("id") or ""),
                importance=max(
                    int(exact_match.get("importance") or 0),
                    self._bounded_importance(item.get("importance")),
                ),
                confidence=max(
                    float(exact_match.get("confidence") or 0),
                    self._bounded_confidence(item.get("confidence")),
                ),
                source_message_ids=self._merge_unique_strings(
                    list(exact_match.get("source_message_ids") or []),
                    source_message_ids,
                ),
                source_request_ids=self._merge_unique_strings(
                    list(exact_match.get("source_request_ids") or []),
                    source_request_ids,
                ),
            )
            self.store.create_memory_event(
                owner_id=self.owner_id,
                session_id=session_id,
                request_id=request_id,
                memory_id=str(updated.get("id") or ""),
                event_type="memory_updated",
                payload={
                    "subject": updated.get("subject"),
                    "type": updated.get("type"),
                    "reason": "merged_duplicate_add",
                    "source_message_ids": source_message_ids,
                    "source_request_ids": source_request_ids,
                },
            )
            return updated, "merged_duplicate_add"

        prior = self._find_subject_type_active_match(active, subject=subject, memory_type=memory_type)
        if prior is not None:
            self.store.update_memory(str(prior.get("id") or ""), status="superseded")
            record = self.store.create_memory(
                memory_id=make_id("memory"),
                owner_id=self.owner_id,
                session_id=session_id,
                scope=scope,
                type=memory_type,
                subject=subject,
                content=content,
                importance=self._bounded_importance(item.get("importance")),
                confidence=self._bounded_confidence(item.get("confidence")),
                source_message_ids=source_message_ids,
                source_request_ids=source_request_ids,
                supersedes_memory_id=str(prior.get("id") or ""),
            )
            self.store.create_memory_event(
                owner_id=self.owner_id,
                session_id=session_id,
                request_id=request_id,
                memory_id=str(record.get("id") or ""),
                event_type="memory_superseded",
                payload={
                    "subject": record.get("subject"),
                    "type": record.get("type"),
                    "superseded_memory_id": str(prior.get("id") or ""),
                    "source_message_ids": source_message_ids,
                    "source_request_ids": source_request_ids,
                },
            )
            return record, "memory_superseded"

        record = self.store.create_memory(
            memory_id=make_id("memory"),
            owner_id=self.owner_id,
            session_id=session_id,
            scope=scope,
            type=memory_type,
            subject=subject,
            content=content,
            importance=self._bounded_importance(item.get("importance")),
            confidence=self._bounded_confidence(item.get("confidence")),
            source_message_ids=source_message_ids,
            source_request_ids=source_request_ids,
        )
        self.store.create_memory_event(
            owner_id=self.owner_id,
            session_id=session_id,
            request_id=request_id,
            memory_id=str(record.get("id") or ""),
            event_type="memory_added",
            payload={
                "subject": record.get("subject"),
                "type": record.get("type"),
                "source_message_ids": source_message_ids,
                "source_request_ids": source_request_ids,
            },
        )
        return record, "memory_added"

    def _resolve_existing_memory_id(
        self,
        *,
        memory_id: str | None,
        session_id: str | None,
        subject: str,
        memory_type: str,
        content: str,
    ) -> str | None:
        if memory_id:
            return memory_id
        active = self.store.list_memories(
            owner_id=self.owner_id,
            status="active",
            session_id=session_id,
            limit=200,
        )
        exact = self._find_exact_active_match(active, subject=subject, memory_type=memory_type, content=content)
        if exact is not None:
            return str(exact.get("id") or "") or None
        by_subject = self._find_subject_type_active_match(active, subject=subject, memory_type=memory_type)
        if by_subject is not None:
            return str(by_subject.get("id") or "") or None
        return None

    @staticmethod
    def _find_exact_active_match(
        memories: list[dict[str, Any]],
        *,
        subject: str,
        memory_type: str,
        content: str,
    ) -> dict[str, Any] | None:
        normalized_subject = MemoryManagerService._normalize_text(subject)
        normalized_type = MemoryManagerService._normalize_text(memory_type)
        normalized_content = MemoryManagerService._normalize_text(content)
        for item in memories:
            if str(item.get("status") or "") != "active":
                continue
            if MemoryManagerService._normalize_text(str(item.get("type") or "")) != normalized_type:
                continue
            if MemoryManagerService._normalize_text(str(item.get("subject") or "")) != normalized_subject:
                continue
            if MemoryManagerService._normalize_text(str(item.get("content") or "")) != normalized_content:
                continue
            return item
        return None

    @staticmethod
    def _find_subject_type_active_match(
        memories: list[dict[str, Any]],
        *,
        subject: str,
        memory_type: str,
    ) -> dict[str, Any] | None:
        normalized_subject = MemoryManagerService._normalize_text(subject)
        normalized_type = MemoryManagerService._normalize_text(memory_type)
        for item in memories:
            if str(item.get("status") or "") != "active":
                continue
            if MemoryManagerService._normalize_text(str(item.get("type") or "")) != normalized_type:
                continue
            if MemoryManagerService._normalize_text(str(item.get("subject") or "")) != normalized_subject:
                continue
            return item
        return None

    @staticmethod
    def _merge_unique_strings(existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        for value in [*existing, *incoming]:
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in merged:
                merged.append(cleaned)
        return merged

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(text[start : end + 1])
                    return payload if isinstance(payload, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def _parse_memory_request_envelope(memory_request: str) -> dict[str, Any]:
        try:
            payload = json.loads(str(memory_request or ""))
        except json.JSONDecodeError:
            return {"request": memory_request}
        if not isinstance(payload, dict):
            return {"request": memory_request}
        request_text = str(payload.get("request") or "").strip()
        if request_text:
            return payload
        text_value = str(payload.get("text") or "").strip()
        if text_value:
            payload["request"] = text_value
            return payload
        data = payload.get("data")
        if isinstance(data, dict) and data:
            parts: list[str] = []
            for key, value in data.items():
                cleaned = str(value or "").strip()
                if not cleaned:
                    continue
                parts.append(f"{key}: {cleaned}")
            if parts:
                payload["request"] = "Remember this information: " + "; ".join(parts)
                return payload
        payload["request"] = memory_request
        return payload

    @staticmethod
    def _bounded_importance(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 3
        return max(1, min(parsed, 5))

    @staticmethod
    def _bounded_confidence(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.85
        return max(0.0, min(parsed, 1.0))

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(str(text or "").replace("\x00", " ").strip().lower().split())


def run_memory_manager_update_job(store: ChanakyaStore, *, session_id: str, request_id: str) -> None:
    service = MemoryManagerService(store)
    try:
        service.process_request_turn(session_id=session_id, request_id=request_id)
    except Exception as exc:
        debug_log(
            "memory_manager_update_failed",
            {"session_id": session_id, "request_id": request_id, "error": str(exc)},
        )
        store.create_memory_event(
            owner_id=service.owner_id,
            session_id=session_id,
            request_id=request_id,
            event_type="memory_extraction_failed",
            payload={"error": str(exc)},
        )
