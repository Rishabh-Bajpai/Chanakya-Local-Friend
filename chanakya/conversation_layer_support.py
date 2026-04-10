from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chanakya.config import get_conversation_openai_config


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
        cfg = get_conversation_openai_config()
        base_url = str(cfg.get("base_url") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        model = str(cfg.get("model") or "").strip()
        self._enabled = bool(base_url and api_key and model)
        self._state_store = InMemoryResponseStateStore()
        self._orchestration_agent = (
            MAFOrchestrationAgent(
                model=model,
                base_url=base_url,
                api_key=api_key,
                env_file_path=".env",
            )
            if self._enabled
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self._orchestration_agent is not None

    def wrap_reply(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationLayerResult:
        if not self.enabled:
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
            orchestration_agent=self._orchestration_agent,
            state_store=self._state_store,
        )
        response = wrapper.handle(
            ChatRequest(
                session_id=session_id,
                message=user_message,
                metadata={
                    "conversation_orchestration_model_id": model_id,
                    "conversation_preferences": {
                        "tone": "warm, natural, human",
                        "verbosity": "medium",
                    },
                },
            )
        )
        return ConversationLayerResult(
            response=response.response,
            messages=[message.to_dict() for message in response.messages],
            metadata=dict(response.metadata),
        )

    def deliver_next_message(self, session_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "idle", "working_memory": {"session_id": session_id}}
        wrapper = ConversationWrapper(
            agent=_FixedResponseAgent(response_text="", response_metadata={}),
            orchestration_agent=self._orchestration_agent,
            state_store=self._state_store,
        )
        return wrapper.deliver_next_message(session_id)

    def request_manual_pause(self, session_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"session_id": session_id}
        wrapper = ConversationWrapper(
            agent=_FixedResponseAgent(response_text="", response_metadata={}),
            orchestration_agent=self._orchestration_agent,
            state_store=self._state_store,
        )
        return wrapper.request_manual_pause(session_id)

    def list_debug_view(self, session_id: str) -> dict[str, Any]:
        return self._state_store.list_debug_view(session_id)
