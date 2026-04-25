from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chanakya.agent.runtime import normalize_runtime_backend
from chanakya.config import get_a2a_agent_url, get_conversation_openai_config


def _ensure_conversation_layer_import_path() -> None:
    root = Path(__file__).resolve().parents[1]
    package_root = root / "chanakya_conversation_layer"
    package_root_str = str(package_root)
    if package_root.exists() and package_root_str not in sys.path:
        sys.path.insert(0, package_root_str)


_ensure_conversation_layer_import_path()

from conversation_layer.schemas import ChatRequest, ChatResponse  # noqa: E402
from conversation_layer.services.conversation_wrapper import (  # noqa: E402
    ConversationWrapper,
)
from conversation_layer.services.orchestration_agent import (  # noqa: E402
    MAFOrchestrationAgent,
)
from conversation_layer.services.working_memory import (  # noqa: E402
    InMemoryResponseStateStore,
)


DEFAULT_CONVERSATION_TONE_INSTRUCTION = (
    "Use a natural, friendly, conversational tone that feels good in spoken dialogue."
)
DEFAULT_TTS_INSTRUCTION = (
    "Make the text easy for TTS to read naturally. Use clear spoken phrasing and avoid awkward punctuation patterns."
)


def get_conversation_preference_defaults() -> dict[str, str]:
    return {
        "conversation_tone_instruction": DEFAULT_CONVERSATION_TONE_INSTRUCTION,
        "tts_instruction": DEFAULT_TTS_INSTRUCTION,
    }


@dataclass(slots=True)
class ConversationLayerResult:
    response: str
    messages: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass(slots=True)
class _FixedResponseAgent:
    response_text: str
    response_metadata: dict[str, Any]

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            session_id=chat_request.session_id,
            response=self.response_text,
            metadata=dict(self.response_metadata),
        )


class ConversationLayerSupport:
    def __init__(self) -> None:
        self._openai_config = get_conversation_openai_config()
        self._default_a2a_url = get_a2a_agent_url()
        self._state_store = InMemoryResponseStateStore()

    @property
    def enabled(self) -> bool:
        return True

    def _build_orchestration_agent(
        self,
        *,
        backend: str | None,
        model_id: str | None,
        a2a_url: str | None,
        a2a_remote_agent: str | None,
        a2a_model_provider: str | None,
        a2a_model_id: str | None,
    ) -> MAFOrchestrationAgent | None:
        selected_backend = normalize_runtime_backend(backend)
        if selected_backend == "a2a":
            remote_url = str(a2a_url or self._default_a2a_url or "").strip()
            if not remote_url:
                return None
            resolved_model_id = str(model_id or a2a_model_id or "").strip()
            return MAFOrchestrationAgent(
                model=resolved_model_id,
                base_url="",
                api_key="",
                env_file_path=".env",
                backend="a2a",
                remote_agent_url=remote_url,
                default_remote_agent=str(a2a_remote_agent or "").strip() or None,
                default_model_provider=str(a2a_model_provider or "").strip() or None,
                default_model_id=resolved_model_id or None,
            )
        base_url = str(self._openai_config.get("base_url") or "").strip()
        api_key = str(self._openai_config.get("api_key") or "").strip()
        resolved_model_id = str(model_id or self._openai_config.get("model") or "").strip()
        if not (base_url and api_key and resolved_model_id):
            return None
        return MAFOrchestrationAgent(
            model=resolved_model_id,
            base_url=base_url,
            api_key=api_key,
            env_file_path=".env",
            backend="openai_compatible",
        )

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
        metadata: dict[str, Any] | None = None,
    ) -> ConversationLayerResult:
        orchestration_agent = self._build_orchestration_agent(
            backend=backend,
            model_id=model_id,
            a2a_url=a2a_url,
            a2a_remote_agent=a2a_remote_agent,
            a2a_model_provider=a2a_model_provider,
            a2a_model_id=a2a_model_id,
        )
        if orchestration_agent is None:
            return ConversationLayerResult(
                response=assistant_message,
                messages=[{"text": assistant_message, "delay_ms": 0}],
                metadata={},
            )
        wrapper = ConversationWrapper(
            agent=_FixedResponseAgent(
                response_text=assistant_message,
                response_metadata=metadata or {},
            ),
            orchestration_agent=orchestration_agent,
            state_store=self._state_store,
        )
        response = wrapper.handle(
            ChatRequest(
                session_id=session_id,
                message=user_message,
                metadata={
                    "conversation_orchestration_model_id": model_id or a2a_model_id,
                    "conversation_preferences": {
                        "tone": "warm, natural, human",
                        "verbosity": "medium",
                        **(
                            {"conversation_tone_instruction": conversation_tone_instruction}
                            if conversation_tone_instruction
                            else {}
                        ),
                        **(
                            {"tts_instruction": tts_instruction}
                            if tts_instruction
                            else {}
                        ),
                    },
                },
            )
        )
        response_metadata = dict(response.metadata)
        response_metadata.setdefault(
            "conversation_layer_backend",
            "a2a" if normalize_runtime_backend(backend) == "a2a" else "openai_compatible",
        )
        if normalize_runtime_backend(backend) == "a2a":
            response_metadata.setdefault(
                "conversation_layer_a2a_url",
                str(a2a_url or self._default_a2a_url or "").strip() or None,
            )
            response_metadata.setdefault(
                "conversation_layer_a2a_remote_agent",
                str(a2a_remote_agent or "").strip() or None,
            )
            response_metadata.setdefault(
                "conversation_layer_a2a_model_provider",
                str(a2a_model_provider or "").strip() or None,
            )
            response_metadata.setdefault(
                "conversation_layer_a2a_model_id",
                str(model_id or a2a_model_id or "").strip() or None,
            )
        return ConversationLayerResult(
            response=response.response,
            messages=[message.to_dict() for message in response.messages],
            metadata=response_metadata,
        )

    def deliver_next_message(self, session_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "idle", "working_memory": {"session_id": session_id}}
        wrapper = ConversationWrapper(
            agent=_FixedResponseAgent(response_text="", response_metadata={}),
            orchestration_agent=None,
            state_store=self._state_store,
        )
        return wrapper.deliver_next_message(session_id)

    def clear_session_state(self, session_id: str) -> None:
        self._state_store.clear(session_id)

    def request_manual_pause(self, session_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"session_id": session_id}
        wrapper = ConversationWrapper(
            agent=_FixedResponseAgent(response_text="", response_metadata={}),
            orchestration_agent=None,
            state_store=self._state_store,
        )
        return wrapper.request_manual_pause(session_id)

    def list_debug_view(self, session_id: str) -> dict[str, Any]:
        return self._state_store.list_debug_view(session_id)
