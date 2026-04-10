from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from conversation_layer.schemas import ChatRequest, ChatResponse, DeliveryMessage
from conversation_layer.services.agent_interface import AgentInterface
from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent
from conversation_layer.services.working_memory import (
    InMemoryResponseStateStore,
    ResponseScopedWorkingMemory,
    ResponseStateStore,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ConversationWrapper:
    _MAX_DELIVERY_MESSAGE_CHARS = 320

    agent: AgentInterface
    history_provider: Any | None = None
    orchestration_agent: MAFOrchestrationAgent | None = None
    state_store: ResponseStateStore = field(default_factory=InMemoryResponseStateStore)

    def handle(self, chat_request: ChatRequest) -> ChatResponse:
        chat_request.validate()

        prior_memory = self.state_store.get(chat_request.session_id)
        prior_pending_messages = list(prior_memory.pending_messages)
        queue_was_active = bool(prior_memory.pending_messages)
        manual_pause_requested = bool(prior_memory.manual_pause_requested)
        preferences = self._conversation_preferences(chat_request)
        wm_decision = self._run_wm_manager(
            chat_request=chat_request,
            prior_memory=prior_memory,
        )
        wm_decision = self._normalize_wm_decision(
            wm_decision=wm_decision,
            chat_request=chat_request,
            prior_memory=prior_memory,
        )
        interrupt_type = str(wm_decision.get("interrupt_type") or "")
        same_topic = bool(wm_decision.get("same_topic", False))
        clear_working_memory = bool(wm_decision.get("clear_working_memory", False))
        preserve_delivered = bool(wm_decision.get("preserve_delivered_messages", same_topic))
        preserve_pending = bool(wm_decision.get("preserve_pending_messages", False))
        cancelled_pending_count = 0 if preserve_pending else len(prior_memory.pending_messages)

        if clear_working_memory:
            prior_memory = ResponseScopedWorkingMemory(session_id=chat_request.session_id)
            queue_was_active = False
            same_topic = False

        core_response = None
        core_response_text = prior_memory.latest_core_response
        core_agent_called = False
        if wm_decision.get("use_core_agent", True) or not core_response_text.strip():
            core_request = ChatRequest(
                session_id=chat_request.session_id,
                message=chat_request.message,
                metadata=dict(chat_request.metadata or {}),
            )
            core_response = self.agent.respond(core_request)
            core_response_text = core_response.response
            core_agent_called = True

        planner_result: dict[str, Any]
        if (
            interrupt_type == "ack_continue"
            and same_topic
            and preserve_pending
            and not core_agent_called
        ):
            planner_result = {
                "reasoning": "Preserved the remaining pending delivery after an acknowledgment.",
                "messages": [],
            }
            planned_messages = self._coerce_planned_messages(
                [item["text"] for item in prior_pending_messages],
                fallback_text="",
                delay_between_messages_ms=int(preferences.get("delay_between_messages_ms") or 5000),
            )
            immediate_messages = []
            queued_messages = list(prior_pending_messages)
        else:
            planner_result = self._run_conversation_planner(
                chat_request=chat_request,
                prior_memory=prior_memory,
                preferences=preferences,
                wm_decision=wm_decision,
                core_response_text=core_response_text,
                core_agent_called=core_agent_called,
            )
            planned_messages = self._coerce_planned_messages(
                planner_result.get("messages") or [],
                fallback_text=core_response_text,
                delay_between_messages_ms=int(preferences.get("delay_between_messages_ms") or 5000),
            )
            planned_messages = self._restore_full_core_response_plan(
                core_response_text,
                planned_messages,
                user_message=chat_request.message,
                core_agent_called=core_agent_called,
            )
            planned_messages = self._enforce_requested_more_count(
                user_message=chat_request.message,
                planned_messages=planned_messages,
                core_response_text=core_response_text,
                core_agent_called=core_agent_called,
            )
            planned_messages = self._enforce_detailed_request_coverage(
                user_message=chat_request.message,
                planned_messages=planned_messages,
                core_response_text=core_response_text,
                core_agent_called=core_agent_called,
            )
            planned_messages = self._refine_planned_messages(
                planned_messages,
                delay_between_messages_ms=int(preferences.get("delay_between_messages_ms") or 5000),
            )
            planned_messages = self._stitch_dangling_numeric_markers(planned_messages)
            immediate_messages, queued_messages = self._split_delivery_plan(planned_messages)

        delivered_base = list(prior_memory.delivered_messages) if preserve_delivered else []
        delivered_messages = [*delivered_base, *immediate_messages]
        current_turn_messages = list(immediate_messages)
        memory = ResponseScopedWorkingMemory(
            session_id=chat_request.session_id,
            topic_state="active" if same_topic or core_response_text.strip() else "idle",
            topic_label=self._topic_label(chat_request.message, core_response_text),
            current_user_message=chat_request.message,
            latest_user_message=chat_request.message,
            latest_user_intent=interrupt_type,
            topic_continuity_confidence=float(
                wm_decision.get("topic_continuity_confidence") or (1.0 if same_topic else 0.0)
            ),
            latest_core_response=core_response_text,
            planned_messages=planned_messages,
            delivered_messages=delivered_messages,
            pending_messages=queued_messages,
            delivered_summary=self._summarize_messages(delivered_messages),
            remaining_summary=self._summarize_pending_messages(queued_messages),
            core_agent_called=core_agent_called,
            interrupted=queue_was_active,
            manual_pause_requested=False,
            queue_cleared_reason=self._queue_cleared_reason(
                queue_was_active=queue_was_active and not preserve_pending,
                manual_pause_requested=manual_pause_requested,
            ),
            cancelled_pending_count=cancelled_pending_count,
        )
        if interrupt_type == "ack_continue" and same_topic and preserve_pending:
            memory.queue_cleared_reason = None
            memory.cancelled_pending_count = 0
        self.state_store.save(chat_request.session_id, memory)
        if core_agent_called:
            self._rewrite_history(chat_request.session_id, current_turn_messages)
        else:
            self._append_non_core_turn(
                session_id=chat_request.session_id,
                user_message=chat_request.message,
                assistant_messages=immediate_messages,
            )

        response_text = "\n\n".join(
            message.text for message in immediate_messages if message.text.strip()
        )
        selected_orchestration_model = str(
            (chat_request.metadata or {}).get("conversation_orchestration_model_id") or ""
        ).strip()
        return ChatResponse(
            session_id=chat_request.session_id,
            response=response_text,
            messages=immediate_messages,
            metadata={
                **(core_response.metadata if core_response is not None else {}),
                "source": "conversation_layer",
                "core_agent_response": core_response_text,
                "core_agent_called": core_agent_called,
                "interrupted": queue_was_active,
                "manual_pause_requested": manual_pause_requested,
                "queue_cleared_reason": memory.queue_cleared_reason,
                "cancelled_pending_count": cancelled_pending_count,
                "pending_delivery_count": len(queued_messages),
                "interrupt_type": interrupt_type,
                "same_topic": same_topic,
                "wm_manager": wm_decision,
                "conversation_planner": planner_result,
                "conversation_preferences": preferences,
                "conversation_orchestration_model_id": (selected_orchestration_model or None),
            },
        )

    def list_debug_view(self, session_id: str) -> dict[str, Any]:
        return self.state_store.list_debug_view(session_id)

    def request_manual_pause(self, session_id: str) -> dict[str, Any]:
        return self.state_store.mark_manual_pause(session_id).to_dict()

    def deliver_next_message(self, session_id: str) -> dict[str, Any]:
        memory = self.state_store.get(session_id)
        if memory.manual_pause_requested:
            return {
                "status": "paused",
                "working_memory": memory.to_dict(),
            }
        if not memory.pending_messages:
            return {
                "status": "idle",
                "working_memory": memory.to_dict(),
            }

        next_item = memory.pending_messages[0]
        available_at = self._parse_available_at(next_item.get("available_at"))
        now = _utc_now()
        if available_at > now:
            return {
                "status": "waiting",
                "retry_after_ms": max(
                    int((available_at - now).total_seconds() * 1000),
                    0,
                ),
                "working_memory": memory.to_dict(),
            }

        delivered = DeliveryMessage(
            text=str(next_item.get("text") or ""),
            delay_ms=int(next_item.get("delay_ms") or 0),
        )
        memory.delivered_messages.append(delivered)
        memory.pending_messages.pop(0)
        self.state_store.save(session_id, memory)
        self._rewrite_history(session_id, memory.delivered_messages)
        return {
            "status": "delivered",
            "message": delivered.to_dict(),
            "working_memory": memory.to_dict(),
        }

    def get_agent_debug_state(self, session_id: str) -> dict[str, Any]:
        return {
            "adapter_name": type(self).__name__,
            "session_id": session_id,
            "working_memory": self.list_debug_view(session_id),
        }

    def _plan_delivery(self, response_text: str) -> list[DeliveryMessage]:
        chunks = self._split_into_chunks(response_text)
        planned: list[DeliveryMessage] = []
        for index, chunk in enumerate(chunks):
            planned.append(
                DeliveryMessage(
                    text=chunk,
                    delay_ms=0 if index == 0 else 5000,
                )
            )
        return planned or [DeliveryMessage(text=response_text.strip(), delay_ms=0)]

    def _split_into_chunks(self, response_text: str) -> list[str]:
        text = (response_text or "").strip()
        if not text:
            return [""]
        if len(text) <= 140:
            return [text]

        sentence_chunks = [
            sentence.strip() for sentence in text.replace("\n", " ").split(". ") if sentence.strip()
        ]
        if len(sentence_chunks) <= 1:
            return [text]

        normalized = [
            chunk if chunk.endswith((".", "!", "?")) else f"{chunk}." for chunk in sentence_chunks
        ]
        if len(normalized) <= 3:
            return normalized

        grouped: list[str] = []
        chunk_size = max((len(normalized) + 2) // 3, 1)
        for start in range(0, len(normalized), chunk_size):
            grouped.append(" ".join(normalized[start : start + chunk_size]).strip())
        return grouped

    def _refine_planned_messages(
        self,
        planned_messages: list[DeliveryMessage],
        *,
        delay_between_messages_ms: int,
    ) -> list[DeliveryMessage]:
        refined: list[DeliveryMessage] = []
        fallback_delay_ms = max(delay_between_messages_ms, 0)

        for index, message in enumerate(planned_messages):
            text = (message.text or "").strip()
            if not text:
                continue
            if len(text) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                refined.append(message)
                continue

            chunks = self._split_planned_message_text(text)
            if len(chunks) <= 1:
                refined.append(message)
                continue

            for chunk_index, chunk in enumerate(chunks):
                refined.append(
                    DeliveryMessage(
                        text=chunk,
                        delay_ms=(
                            int(message.delay_ms)
                            if chunk_index == 0
                            else (
                                fallback_delay_ms
                                if index == 0 or int(message.delay_ms) <= 0
                                else int(message.delay_ms)
                            )
                        ),
                    )
                )
        return refined or planned_messages

    def _split_planned_message_text(self, text: str) -> list[str]:
        raw_text = (text or "").strip()
        if len(raw_text) <= self._MAX_DELIVERY_MESSAGE_CHARS:
            return [raw_text]
        if self._should_preserve_layout(raw_text):
            return self._split_preserving_layout(raw_text)

        normalized = re.sub(r"\s+", " ", raw_text)
        if len(normalized) <= self._MAX_DELIVERY_MESSAGE_CHARS:
            return [normalized]

        segmented = re.sub(r"\s*---\s*", " || ", normalized)
        segmented = re.sub(r"\s+(?=#+\s*)", " || ", segmented)
        segmented = re.sub(r"\s+(?=\d+\.\s)", " || ", segmented)
        segmented = re.sub(r"\s+(?=[-*]\s)", " || ", segmented)
        segments = [
            self._normalize_segment_text(segment)
            for segment in re.split(r"\s*\|\|\s*", segmented)
            if self._normalize_segment_text(segment)
        ]

        sentences: list[str] = []
        for segment in segments or [normalized]:
            if re.match(r"^\d+\.\s+\S+", segment):
                sentences.append(segment.strip())
                continue
            parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", segment) if part.strip()]
            if parts:
                sentences.extend(parts)
            else:
                sentences.append(segment)

        return self._group_conversational_segments(sentences)

    def _should_preserve_layout(self, text: str) -> bool:
        if "\n" not in text:
            return False
        if re.search(r"(^|\n)\s*[-*_]{3,}\s*(\n|$)", text):
            return True
        non_empty_lines = [line for line in text.splitlines() if line.strip()]
        return len(non_empty_lines) >= 4

    def _split_preserving_layout(self, text: str) -> list[str]:
        blocks = [block.strip("\n") for block in re.split(r"\n{2,}", text) if block.strip()]
        if not blocks:
            return [text]

        chunks: list[str] = []
        current = ""
        for block in blocks:
            candidate = f"{current}\n\n{block}".strip() if current else block
            if len(candidate) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(block) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                current = block
                continue
            chunks.extend(self._split_oversized_structured_block(block))
        if current:
            chunks.append(current)
        return chunks or [text]

    def _split_oversized_structured_block(self, block: str) -> list[str]:
        lines = block.splitlines()
        chunks: list[str] = []
        current = ""
        for line in lines:
            line_text = line.rstrip()
            if not line_text:
                candidate = f"{current}\n" if current else ""
                if len(candidate) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                    current = candidate
                continue
            candidate = f"{current}\n{line_text}".strip("\n") if current else line_text
            if len(candidate) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(line_text) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                current = line_text
                continue
            line_parts = self._split_oversized_segment(line_text)
            if line_parts:
                chunks.extend(line_parts[:-1])
                current = line_parts[-1]
        if current:
            chunks.append(current)
        return chunks

    def _group_conversational_segments(self, segments: list[str]) -> list[str]:
        groups: list[str] = []
        current = ""
        for segment in segments:
            for piece in self._split_oversized_segment(segment):
                if not current:
                    current = piece
                    continue
                candidate = f"{current} {piece}".strip()
                if len(candidate) <= self._MAX_DELIVERY_MESSAGE_CHARS:
                    current = candidate
                    continue
                groups.append(current)
                current = piece
        if current:
            groups.append(current)
        return groups

    def _split_oversized_segment(self, segment: str) -> list[str]:
        text = segment.strip()
        if len(text) <= self._MAX_DELIVERY_MESSAGE_CHARS:
            return [text]

        pieces: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > self._MAX_DELIVERY_MESSAGE_CHARS:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _normalize_segment_text(self, segment: str) -> str:
        text = segment.strip()
        if not text:
            return ""
        if re.match(r"^\d+\.\s+\S+", text):
            return text
        terminal_checked = text.rstrip("\"')]}»”’")
        if terminal_checked.endswith((".", "!", "?", ":", ";")):
            return text
        if text.startswith(("- ", "* ", "#")):
            return text
        if len(text.split()) <= 12:
            return f"{text}."
        return text

    def _restore_full_core_response_plan(
        self,
        core_response_text: str,
        planned_messages: list[DeliveryMessage],
        *,
        user_message: str,
        core_agent_called: bool,
    ) -> list[DeliveryMessage]:
        if not core_agent_called or not core_response_text.strip() or not planned_messages:
            return planned_messages

        normalized_core = self._normalize_comparison_text(core_response_text)
        normalized_planned = self._normalize_comparison_text(
            " ".join(message.text for message in planned_messages)
        )
        if not normalized_core or not normalized_planned:
            return planned_messages

        core_numbered_count = self._numbered_item_count(core_response_text)
        planned_numbered_count = self._numbered_item_count(
            " ".join(message.text for message in planned_messages)
        )
        if core_numbered_count >= 4 and 0 < planned_numbered_count < core_numbered_count:
            return self._plan_delivery(core_response_text)

        coverage_ratio = len(normalized_planned) / len(normalized_core)
        if coverage_ratio >= 0.7:
            return planned_messages
        if normalized_planned not in normalized_core:
            if (
                self._requires_high_fidelity_preservation(
                    user_message=user_message,
                    core_response_text=core_response_text,
                )
                and not self._is_brief_request(user_message)
                and coverage_ratio < 0.55
            ):
                return self._plan_delivery(core_response_text)
            return planned_messages
        return self._plan_delivery(core_response_text)

    def _requires_high_fidelity_preservation(
        self, *, user_message: str, core_response_text: str
    ) -> bool:
        normalized_user = (user_message or "").strip().lower()
        cues = (
            "fetch ",
            "what it says",
            "what does it say",
            "summarize this page",
            "summarise this page",
            "summarize the page",
            "summarise the page",
            "website",
            "webpage",
            "url",
            "article",
        )
        asks_for_source_content = any(cue in normalized_user for cue in cues) or bool(
            re.search(r"https?://", normalized_user)
        )
        if not asks_for_source_content:
            return False
        return self._is_structure_rich_response(core_response_text)

    def _is_structure_rich_response(self, text: str) -> bool:
        normalized = text or ""
        if normalized.count("\n") >= 6:
            return True
        markers = (
            r"(^|\n)\s*#{2,}\s+",
            r"(^|\n)\s*[-*]\s+",
            r"(^|\n)\s*\d{1,2}\.\s+",
            r"https?://",
        )
        return any(re.search(pattern, normalized) for pattern in markers)

    def _is_brief_request(self, message: str) -> bool:
        normalized = (message or "").strip().lower()
        if not normalized:
            return False
        cues = (
            "quick",
            "brief",
            "short",
            "one line",
            "in 1 line",
            "in one sentence",
            "tl;dr",
        )
        return any(cue in normalized for cue in cues)

    def _enforce_requested_more_count(
        self,
        *,
        user_message: str,
        planned_messages: list[DeliveryMessage],
        core_response_text: str,
        core_agent_called: bool,
    ) -> list[DeliveryMessage]:
        if not core_agent_called or not planned_messages:
            return planned_messages
        requested_more = self._requested_more_count(user_message)
        if requested_more is None or requested_more <= 0:
            return planned_messages

        combined_planned = "\n".join(message.text for message in planned_messages)
        source_text = combined_planned if combined_planned.strip() else core_response_text
        numbered_items = self._extract_numbered_items(source_text)
        if len(numbered_items) <= requested_more:
            return planned_messages

        trimmed_items = numbered_items[:requested_more]
        rebuilt = "\n".join(f"{number}. {text}" for number, text in trimmed_items)
        if not rebuilt.strip():
            return planned_messages
        return self._plan_delivery(rebuilt)

    def _requested_more_count(self, user_message: str) -> int | None:
        normalized = (user_message or "").strip().lower()
        match = re.search(r"\b(\d{1,2})\s+(more|additional)\b", normalized)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _extract_numbered_items(self, text: str) -> list[tuple[int, str]]:
        items: list[tuple[int, str]] = []
        pattern = re.compile(r"(?s)(?<!\d)(\d{1,2})\.\s+(.*?)(?=(?<!\d)\d{1,2}\.\s+|$)")
        for number_raw, body_raw in pattern.findall(text or ""):
            body = re.sub(r"\s+", " ", body_raw.strip())
            if not body:
                continue
            try:
                number = int(number_raw)
            except ValueError:
                continue
            items.append((number, body))
        return items

    def _stitch_dangling_numeric_markers(
        self, planned_messages: list[DeliveryMessage]
    ) -> list[DeliveryMessage]:
        if len(planned_messages) < 2:
            return planned_messages

        stitched: list[DeliveryMessage] = []
        carry_marker = ""
        for index, message in enumerate(planned_messages):
            text = (message.text or "").strip()
            if not text:
                continue
            if carry_marker:
                text = f"{carry_marker} {text}".strip()
                carry_marker = ""

            next_text = ""
            if index + 1 < len(planned_messages):
                next_text = (planned_messages[index + 1].text or "").strip()

            marker = self._dangling_numeric_marker(text)
            if marker and next_text and not re.match(r"^(?:\*\*)?\d+\.", next_text):
                text = text[: -len(marker)].rstrip()
                carry_marker = marker

            if text:
                stitched.append(DeliveryMessage(text=text, delay_ms=int(message.delay_ms)))

        if carry_marker and stitched:
            last = stitched[-1]
            stitched[-1] = DeliveryMessage(
                text=f"{last.text} {carry_marker}".strip(),
                delay_ms=int(last.delay_ms),
            )

        return stitched or planned_messages

    def _dangling_numeric_marker(self, text: str) -> str:
        normalized = (text or "").rstrip()
        match = re.search(r"(?<!\w)((?:\*\*)?\d+\.)$", normalized)
        if not match:
            return ""
        marker = match.group(1)
        prefix = normalized[: match.start()].rstrip()
        if not prefix:
            return ""
        last_char = prefix[-1]
        if not marker.startswith("**") and last_char not in ".!?:;\"'”’»":
            return ""
        return marker

    def _enforce_detailed_request_coverage(
        self,
        *,
        user_message: str,
        planned_messages: list[DeliveryMessage],
        core_response_text: str,
        core_agent_called: bool,
    ) -> list[DeliveryMessage]:
        if not core_agent_called or not planned_messages or not core_response_text.strip():
            return planned_messages
        if not self._is_detailed_request(user_message):
            return planned_messages

        planned_text = "\n".join(message.text for message in planned_messages)
        normalized_core = self._normalize_comparison_text(core_response_text)
        normalized_planned = self._normalize_comparison_text(planned_text)
        if not normalized_core or not normalized_planned:
            return planned_messages

        coverage_ratio = len(normalized_planned) / len(normalized_core)
        if coverage_ratio >= 0.6:
            return planned_messages
        return self._plan_delivery(core_response_text)

    def _is_detailed_request(self, message: str) -> bool:
        normalized = (message or "").strip().lower()
        if not normalized:
            return False
        cues = (
            "detailed",
            "in detail",
            "long",
            "elaborate",
            "comprehensive",
            "deep dive",
            "more detail",
            "full explanation",
        )
        return any(cue in normalized for cue in cues)

    def _numbered_item_count(self, text: str) -> int:
        numbered = re.findall(r"(?<!\d)(\d{1,2})\.\s+", text or "")
        if not numbered:
            return 0
        try:
            return max(int(item) for item in numbered)
        except ValueError:
            return 0

    def _rewrite_history(self, session_id: str, planned_messages: list[DeliveryMessage]) -> None:
        if self.history_provider is None or not hasattr(
            self.history_provider, "rewrite_latest_assistant_turn"
        ):
            return
        self.history_provider.rewrite_latest_assistant_turn(
            session_id,
            [message.text for message in planned_messages if message.text.strip()],
        )

    def _append_non_core_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_messages: list[DeliveryMessage],
    ) -> None:
        if self.history_provider is None or not hasattr(
            self.history_provider, "append_conversation_turn"
        ):
            return
        first_message = next(
            (message.text for message in assistant_messages if message.text.strip()),
            "",
        )
        self.history_provider.append_conversation_turn(
            session_id,
            user_message=user_message,
            assistant_message=first_message,
        )

    def _normalize_comparison_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip()).lower()

    def _conversation_preferences(self, chat_request: ChatRequest) -> dict[str, Any]:
        preferences = {
            "tone": "warm, natural, human",
            "verbosity": "medium",
            "delivery_style": "multi_step",
            "delay_between_messages_ms": 5000,
        }
        preferences.update((chat_request.metadata or {}).get("conversation_preferences") or {})
        return preferences

    def _run_wm_manager(
        self,
        *,
        chat_request: ChatRequest,
        prior_memory: ResponseScopedWorkingMemory,
    ) -> dict[str, Any]:
        fallback = self._fallback_wm_decision(prior_memory, chat_request.message)
        if self.orchestration_agent is None:
            return fallback

        payload = {
            "session_id": chat_request.session_id,
            "user_message": chat_request.message,
            "current_response_memory": prior_memory.to_dict(),
            "output_schema": {
                "interrupt_type": "ack_continue|adapt_remaining|adapt_remaining_with_core|reset_and_new_query",
                "same_topic": "boolean",
                "topic_continuity_confidence": "number",
                "use_core_agent": "boolean",
                "reasoning": "string",
                "message_for_core_agent": "string",
                "queue_action": "replace|continue_from_working_memory|preserve_pending",
                "clear_working_memory": "boolean",
                "preserve_delivered_messages": "boolean",
                "preserve_pending_messages": "boolean",
            },
        }
        instructions = (
            "You are the working-memory manager for a thin conversation delivery layer. "
            "This memory belongs to the currently active topic and should persist across same-topic follow-ups. "
            "Classify the latest user message as one of: ack_continue, adapt_remaining, adapt_remaining_with_core, or reset_and_new_query. "
            "For ack_continue, preserve the same-topic memory and usually avoid a new core-agent call. "
            "For adapt_remaining, keep already delivered content and adjust only the future undelivered content using the user's latest message. "
            "For adapt_remaining_with_core, keep the same topic, preserve already delivered content, and call the core agent for new information that should replace or refine only the remaining undelivered content. Use this especially when the user constrains the next pending item, such as 'don't make the next joke about Switzerland'. "
            "For reset_and_new_query, clear the active-topic working memory and start a new topic. "
            "Return only valid JSON."
        )
        try:
            result = self._plan_with_orchestration_model(
                chat_request,
                task="Working memory routing",
                instructions=instructions,
                payload=payload,
            )
            return {
                "interrupt_type": str(result.get("interrupt_type") or fallback["interrupt_type"]),
                "same_topic": bool(result.get("same_topic", fallback["same_topic"])),
                "topic_continuity_confidence": float(
                    result.get(
                        "topic_continuity_confidence",
                        fallback["topic_continuity_confidence"],
                    )
                ),
                "use_core_agent": bool(result.get("use_core_agent", True)),
                "reasoning": str(result.get("reasoning") or fallback["reasoning"]),
                "message_for_core_agent": str(
                    result.get("message_for_core_agent") or chat_request.message
                ),
                "queue_action": str(result.get("queue_action") or fallback["queue_action"]),
                "clear_working_memory": bool(
                    result.get(
                        "clear_working_memory",
                        fallback["clear_working_memory"],
                    )
                ),
                "preserve_delivered_messages": bool(
                    result.get(
                        "preserve_delivered_messages",
                        fallback["preserve_delivered_messages"],
                    )
                ),
                "preserve_pending_messages": bool(
                    result.get(
                        "preserve_pending_messages",
                        fallback["preserve_pending_messages"],
                    )
                ),
            }
        except Exception:
            return fallback

    def _normalize_wm_decision(
        self,
        *,
        wm_decision: dict[str, Any],
        chat_request: ChatRequest,
        prior_memory: ResponseScopedWorkingMemory,
    ) -> dict[str, Any]:
        if self._should_force_new_topic(chat_request.message, prior_memory, wm_decision):
            return {
                **wm_decision,
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": (
                    "User message appears to be a fresh query on a different topic; "
                    "reset active-topic memory and start a new turn."
                ),
                "message_for_core_agent": chat_request.message,
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            }

        interrupt_type = str(wm_decision.get("interrupt_type") or "")
        has_pending = bool(prior_memory.pending_messages)
        preserve_pending = bool(wm_decision.get("preserve_pending_messages", False))
        if (
            interrupt_type == "ack_continue"
            and (preserve_pending or not self._is_ack_continue_message(chat_request.message))
            and not has_pending
        ):
            return {
                **wm_decision,
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": max(
                    float(wm_decision.get("topic_continuity_confidence") or 0.0),
                    0.8,
                ),
                "use_core_agent": True,
                "reasoning": (
                    "No pending queue exists, so the user cannot continue delivery; "
                    "treat this as a same-topic follow-up that needs a fresh answer."
                ),
                "message_for_core_agent": chat_request.message,
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            }
        return wm_decision

    def _should_force_new_topic(
        self,
        user_message: str,
        prior_memory: ResponseScopedWorkingMemory,
        wm_decision: dict[str, Any],
    ) -> bool:
        same_topic = bool(wm_decision.get("same_topic", False))
        interrupt_type = str(wm_decision.get("interrupt_type") or "")
        if not same_topic and interrupt_type == "reset_and_new_query":
            return False

        normalized = (user_message or "").strip().lower()
        if not normalized:
            return False
        if self._contains_followup_cue(normalized):
            return False

        prior_topic = str(prior_memory.topic_label or prior_memory.latest_user_message or "")
        if not prior_topic.strip():
            return False

        if self._topic_token_overlap(normalized, prior_topic.lower()) > 0:
            return False

        return self._looks_like_fresh_query(normalized)

    def _contains_followup_cue(self, normalized_message: str) -> bool:
        cues = (
            "more",
            "aur",
            "another",
            "continue",
            "next",
            "that",
            "this",
            "same",
            "detailed",
            "in detail",
            "elaborate",
            "deeper",
            "expand",
            "btao",
            "btayo",
            "बताओ",
        )
        return any(cue in normalized_message for cue in cues)

    def _looks_like_fresh_query(self, normalized_message: str) -> bool:
        if "?" in normalized_message:
            return True
        prefixes = (
            "what ",
            "who ",
            "when ",
            "where ",
            "why ",
            "how ",
            "tell ",
            "give ",
            "show ",
            "fetch ",
            "write ",
            "explain ",
            "summarize ",
            "summarise ",
        )
        return normalized_message.startswith(prefixes)

    def _topic_token_overlap(self, first: str, second: str) -> int:
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "it",
            "to",
            "of",
            "and",
            "in",
            "on",
            "for",
            "me",
            "my",
            "your",
            "please",
            "now",
        }
        first_tokens = {
            token
            for token in re.findall(r"[a-zA-Z0-9]+", first)
            if len(token) > 2 and token not in stopwords
        }
        second_tokens = {
            token
            for token in re.findall(r"[a-zA-Z0-9]+", second)
            if len(token) > 2 and token not in stopwords
        }
        return len(first_tokens.intersection(second_tokens))

    def _is_ack_continue_message(self, message: str) -> bool:
        normalized = (message or "").strip().lower()
        return normalized in {
            "ok",
            "okay",
            "nice",
            "good",
            "great",
            "cool",
            "next",
            "got it",
            "continue",
            "go on",
            "carry on",
            "thanks",
            "thank you",
        }

    def _fallback_wm_decision(
        self, prior_memory: ResponseScopedWorkingMemory, user_message: str
    ) -> dict[str, Any]:
        latest_user_message = user_message.strip().lower()
        has_active_topic = bool(
            prior_memory.latest_core_response.strip()
            or prior_memory.pending_messages
            or prior_memory.delivered_messages
        )
        ack_continue = self._is_ack_continue_message(latest_user_message)
        pending_reference = any(
            phrase in latest_user_message
            for phrase in (
                "next",
                "another",
                "upcoming",
                "the rest",
                "continue with",
                "next joke",
                "next example",
                "next part",
            )
        )
        negative_constraint = any(
            phrase in latest_user_message
            for phrase in (
                "don't",
                "do not",
                "not about",
                "without",
                "instead of",
                "avoid",
                "skip",
                "change the next",
                "replace the next",
            )
        )
        adapt_remaining = any(
            phrase in latest_user_message
            for phrase in (
                "explain more",
                "tell me more",
                "more about",
                "go deeper",
                "in more detail",
                "simplify",
                "simpler",
                "example",
                "focus on",
                "compare",
                "why",
                "how",
            )
        )
        reset_and_new_query = any(
            phrase in latest_user_message
            for phrase in (
                "forget that",
                "new topic",
                "different question",
                "instead",
                "unrelated",
            )
        )

        if not has_active_topic:
            return {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "No active topic exists, so a fresh core-agent answer is needed.",
                "message_for_core_agent": "",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            }

        if reset_and_new_query:
            return {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "The user shifted to a different topic, so the active-topic memory should reset.",
                "message_for_core_agent": "",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            }

        if ack_continue and prior_memory.pending_messages:
            return {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.98,
                "use_core_agent": False,
                "reasoning": "The user acknowledged the current topic and wants the remaining delivery to continue.",
                "message_for_core_agent": "",
                "queue_action": "preserve_pending",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": True,
            }

        if pending_reference and negative_constraint and prior_memory.pending_messages:
            return {
                "interrupt_type": "adapt_remaining_with_core",
                "same_topic": True,
                "topic_continuity_confidence": 0.97,
                "use_core_agent": True,
                "reasoning": "The user is still on the same topic but wants the next pending item constrained or replaced, so preserve delivered content and fetch a replacement for future delivery.",
                "message_for_core_agent": (
                    "Continue the current topic while preserving already delivered content. "
                    f"User constraint: {user_message.strip()} "
                    "Return only the new information needed for the remaining undelivered content."
                ),
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            }

        if pending_reference and prior_memory.pending_messages:
            return {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": False,
                "reasoning": "The user asked for the next pending item, so continue the remaining queue without resetting topic state.",
                "message_for_core_agent": "",
                "queue_action": "preserve_pending",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": True,
            }

        if adapt_remaining:
            return {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.9,
                "use_core_agent": True,
                "reasoning": "The user is still on the same topic but wants the remaining delivery adapted, likely with deeper or refined information.",
                "message_for_core_agent": "",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            }

        return {
            "interrupt_type": "reset_and_new_query",
            "same_topic": False,
            "topic_continuity_confidence": 0.2,
            "use_core_agent": True,
            "reasoning": "Treating the user message as a new topic and clearing the prior pending delivery.",
            "message_for_core_agent": "",
            "queue_action": "replace",
            "clear_working_memory": True,
            "preserve_delivered_messages": False,
            "preserve_pending_messages": False,
        }

    def _run_conversation_planner(
        self,
        *,
        chat_request: ChatRequest,
        prior_memory: ResponseScopedWorkingMemory,
        preferences: dict[str, Any],
        wm_decision: dict[str, Any],
        core_response_text: str,
        core_agent_called: bool,
    ) -> dict[str, Any]:
        fallback_messages = [item.to_dict() for item in self._plan_delivery(core_response_text)]
        fallback = {
            "reasoning": "Used the fallback delivery planner.",
            "messages": fallback_messages,
        }
        if self.orchestration_agent is None:
            return fallback

        payload = {
            "latest_user_message": chat_request.message,
            "user_message": chat_request.message,
            "conversation_preferences": preferences,
            "working_memory": prior_memory.to_dict(),
            "wm_decision": wm_decision,
            "core_agent_called": core_agent_called,
            "core_response_text": core_response_text,
            "delivered_summary": prior_memory.delivered_summary,
            "remaining_summary": prior_memory.remaining_summary,
            "output_schema": {
                "reasoning": "string",
                "messages": [
                    {
                        "text": "string",
                    }
                ],
            },
        }
        instructions = (
            "You are the delivery planner for a thin conversation layer. "
            "Given the latest user message, working memory, WM decision, and latest core response, produce the next assistant messages that will be sent to the user in order. "
            "Each message should usually be short, conversational, and at most 1 to 3 sentences. "
            "The user's latest message is a hard constraint for what comes next. "
            "Never repeat or restart content that already appears in delivered_summary or delivered_messages. "
            "For ack_continue, usually preserve the remaining queue and avoid restarting the explanation. "
            "For adapt_remaining, adjust only future undelivered content using the latest user message. "
            "For adapt_remaining_with_core, preserve the topic, preserve delivered content, and use the fresh core response to replace or refine only the remaining undelivered content while satisfying the user's constraint on the next pending item. "
            "For reset_and_new_query, ignore the previous topic and start fresh. "
            "If the user interrupted while there were pending messages, adjust the next messages so they sound responsive to the interruption while staying grounded in the latest core response. "
            "Return only user-visible assistant messages. Do not return internal reasoning, routing notes, or hidden summaries. "
            "Stay direct and to the point. Do not add generic closers like 'Let me know if you need anything else' or similar wrap-up lines unless the user explicitly asked for broader help or next steps. Do not end the topic or invite a topic switch when the user is still constraining the current topic. "
            "Keep it concise and natural. Return only valid JSON."
        )
        try:
            result = self._plan_with_orchestration_model(
                chat_request,
                task="Conversation delivery planning",
                instructions=instructions,
                payload=payload,
            )
            messages = result.get("messages") or []
            if not isinstance(messages, list) or not messages:
                return fallback
            return {
                "reasoning": str(result.get("reasoning") or fallback["reasoning"]),
                "messages": messages,
            }
        except Exception:
            return fallback

    def _plan_with_orchestration_model(
        self,
        chat_request: ChatRequest,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.orchestration_agent is None:
            raise RuntimeError("orchestration_agent_not_configured")
        metadata = chat_request.metadata or {}
        model_id = str(metadata.get("conversation_orchestration_model_id") or "").strip()
        if model_id and hasattr(self.orchestration_agent, "plan_with_model"):
            return self.orchestration_agent.plan_with_model(
                task=task,
                instructions=instructions,
                payload=payload,
                model_id=model_id,
            )
        return self.orchestration_agent.plan(
            task=task,
            instructions=instructions,
            payload=payload,
        )

    def _coerce_planned_messages(
        self,
        items: list[Any],
        *,
        fallback_text: str,
        delay_between_messages_ms: int,
    ) -> list[DeliveryMessage]:
        messages: list[DeliveryMessage] = []
        normalized_delay_ms = max(int(delay_between_messages_ms), 0)
        for index, item in enumerate(items):
            if isinstance(item, DeliveryMessage):
                messages.append(
                    DeliveryMessage(
                        text=item.text,
                        delay_ms=0 if index == 0 else normalized_delay_ms,
                    )
                )
                continue
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                messages.append(
                    DeliveryMessage(
                        text=text,
                        delay_ms=0 if index == 0 else normalized_delay_ms,
                    )
                )
                continue
            text = str(item or "").strip()
            if text:
                messages.append(
                    DeliveryMessage(
                        text=text,
                        delay_ms=0 if index == 0 else normalized_delay_ms,
                    )
                )
        if messages:
            return messages
        if fallback_text.strip():
            return self._plan_delivery(fallback_text)
        return []

    def _summarize_messages(self, messages: list[DeliveryMessage]) -> str:
        text = " ".join(message.text.strip() for message in messages if message.text.strip())
        return text[:500]

    def _summarize_pending_messages(self, messages: list[dict[str, Any]]) -> str:
        text = " ".join(
            str(message.get("text") or "").strip()
            for message in messages
            if str(message.get("text") or "").strip()
        )
        return text[:500]

    def _topic_label(self, user_message: str, core_response_text: str) -> str:
        source_text = user_message.strip() or core_response_text.strip() or "New Chat"
        return textwrap.shorten(source_text, width=80, placeholder="...")

    def _split_delivery_plan(
        self, planned_messages: list[DeliveryMessage]
    ) -> tuple[list[DeliveryMessage], list[dict[str, Any]]]:
        if not planned_messages:
            return [], []

        immediate_messages: list[DeliveryMessage] = []
        queued_messages: list[dict[str, Any]] = []
        now = _utc_now()
        cumulative_delay_ms = 0
        for index, message in enumerate(planned_messages):
            if index == 0 and int(message.delay_ms) <= 0:
                immediate_messages.append(message)
                continue
            cumulative_delay_ms += max(int(message.delay_ms), 0)
            queued_messages.append(
                {
                    "text": message.text,
                    "delay_ms": int(message.delay_ms),
                    "available_at": (now + timedelta(milliseconds=cumulative_delay_ms)).isoformat(),
                }
            )
        if not immediate_messages and queued_messages:
            first = queued_messages.pop(0)
            immediate_messages.append(
                DeliveryMessage(
                    text=str(first.get("text") or ""),
                    delay_ms=0,
                )
            )
        return immediate_messages, queued_messages

    def _parse_available_at(self, value: Any) -> datetime:
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value)
                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return _utc_now()

    def _queue_cleared_reason(
        self, *, queue_was_active: bool, manual_pause_requested: bool
    ) -> str | None:
        if manual_pause_requested:
            return "manual_pause"
        if queue_was_active:
            return "new_user_message"
        return None
