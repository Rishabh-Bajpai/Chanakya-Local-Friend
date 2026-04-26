from conversation_layer.schemas import ChatRequest
from conversation_layer.services.conversation_wrapper import ConversationWrapper


class _FakePlanner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def plan_with_model(self, **kwargs):
        self.calls.append(kwargs)
        return {"delivery_plan": [{"text": "hello", "purpose": "intro"}], "reasoning": "ok"}


class _FakeAgent:
    def respond(self, chat_request):
        raise AssertionError("should not be called")


def test_conversation_wrapper_passes_request_headers_to_planner():
    planner = _FakePlanner()
    wrapper = ConversationWrapper(agent=_FakeAgent(), orchestration_agent=planner)

    wrapper._plan_with_orchestration_model(  # type: ignore[attr-defined]
        ChatRequest(
            session_id="session-1",
            message="Hi",
            metadata={
                "conversation_orchestration_model_id": "qwen-test",
                "request_id": "req-top",
            },
        ),
        task="Conversation delivery planning",
        instructions="test",
        payload={},
    )

    assert planner.calls[0]["request_headers"] == {
        "x-request-id": "req-top",
        "x-chanakya-request-id": "req-top",
        "x-session-id": "session-1",
    }
