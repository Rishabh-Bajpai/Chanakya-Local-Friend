from __future__ import annotations

from dataclasses import dataclass

from conversation_layer.schemas import ChatRequest, ChatResponse
from core_agent_app.services.core_agent import (
    BackendTargetConfig,
    RoutedCoreAgentAdapter,
)


@dataclass(slots=True)
class FakeAdapter:
    name: str
    calls: list[ChatRequest]

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        self.calls.append(chat_request)
        return ChatResponse(
            session_id=chat_request.session_id,
            response=f"reply from {self.name}",
            metadata={"source": self.name},
        )


def _build_router() -> tuple[RoutedCoreAgentAdapter, FakeAdapter, FakeAdapter]:
    openai = FakeAdapter(name="openai", calls=[])
    a2a = FakeAdapter(name="a2a", calls=[])
    router = RoutedCoreAgentAdapter(
        targets={
            ("openai_compatible", "default"): BackendTargetConfig(
                key="default",
                backend="openai_compatible",
                label="OpenAI-Compatible",
                description="Default openai target",
                adapter=openai,
                metadata={"url": "http://openai.local/v1", "model": "demo-model"},
            ),
            ("a2a", "opencode"): BackendTargetConfig(
                key="opencode",
                backend="a2a",
                label="OpenCode A2A",
                description="Default a2a target",
                adapter=a2a,
                metadata={"url": "http://127.0.0.1:18770"},
            ),
        },
        default_backend="openai_compatible",
        default_target="default",
    )
    return router, openai, a2a


def test_routed_core_agent_uses_default_target_when_request_has_no_choice():
    router, openai, a2a = _build_router()

    response = router.respond(ChatRequest(session_id="s1", message="hello"))

    assert response.response == "reply from openai"
    assert len(openai.calls) == 1
    assert len(a2a.calls) == 0
    assert response.metadata["core_agent_backend"] == "openai_compatible"
    assert response.metadata["core_agent_target"] == "default"


def test_routed_core_agent_respects_per_request_a2a_target_choice():
    router, openai, a2a = _build_router()

    response = router.respond(
        ChatRequest(
            session_id="s1",
            message="hello",
            metadata={
                "core_agent_backend": "a2a",
                "core_agent_target": "opencode",
            },
        )
    )

    assert response.response == "reply from a2a"
    assert len(openai.calls) == 0
    assert len(a2a.calls) == 1
    assert response.metadata["core_agent_backend"] == "a2a"
    assert response.metadata["core_agent_target"] == "opencode"
    assert response.metadata["core_agent_target_url"] == "http://127.0.0.1:18770"


def test_routed_core_agent_rejects_unknown_target():
    router, _, _ = _build_router()

    try:
        router.respond(
            ChatRequest(
                session_id="s1",
                message="hello",
                metadata={
                    "core_agent_backend": "a2a",
                    "core_agent_target": "missing",
                },
            )
        )
    except ValueError as exc:
        assert "Unknown core agent target 'a2a:missing'" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for unknown target")
