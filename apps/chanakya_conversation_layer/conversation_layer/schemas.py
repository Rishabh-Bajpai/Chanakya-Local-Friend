from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DeliveryMessage:
    text: str
    delay_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "delay_ms": self.delay_ms,
        }


@dataclass(slots=True)
class ChatRequest:
    session_id: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.message and not self._has_non_text_content():
            raise ValueError("message is required")

    def _has_non_text_content(self) -> bool:
        metadata = self.metadata or {}
        image_files = metadata.get("image_files")
        if isinstance(image_files, list) and any(image_files):
            return True
        return bool(metadata.get("image_data"))


@dataclass(slots=True)
class ChatResponse:
    session_id: str
    response: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[DeliveryMessage] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages and self.response:
            self.messages = [DeliveryMessage(text=self.response, delay_ms=0)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "response": self.response,
            "metadata": self.metadata,
            "messages": [message.to_dict() for message in self.messages],
        }
