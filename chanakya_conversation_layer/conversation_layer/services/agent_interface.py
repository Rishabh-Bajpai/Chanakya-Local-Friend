from __future__ import annotations

from typing import Protocol

from conversation_layer.schemas import ChatRequest, ChatResponse


class AgentInterface(Protocol):
    def respond(self, chat_request: ChatRequest) -> ChatResponse: ...
