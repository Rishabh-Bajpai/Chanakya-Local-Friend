from __future__ import annotations

from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent


class _FakeA2AResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeA2ASession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _FakeA2AAgent:
    def __init__(self, **kwargs) -> None:
        self.calls: list[dict[str, object]] = []

    def create_session(self, *, session_id: str | None = None):
        return _FakeA2ASession(session_id or "default")

    async def run(self, messages, session=None):
        message = messages[0]
        text = getattr(message, "text", None)
        if text is None:
            text = getattr(message, "content", None)
        self.calls.append(
            {
                "text": text if isinstance(text, str) else str(message),
                "session_id": getattr(session, "session_id", None),
            }
        )
        return _FakeA2AResponse('{"messages":[{"text":"planned","delay_ms":0}]}')


def test_orchestration_agent_can_plan_over_a2a_with_opencode_options() -> None:
    planner = MAFOrchestrationAgent(
        model="qwen-default",
        base_url="",
        api_key="",
        env_file_path=".env",
        backend="a2a",
        remote_agent_url="http://127.0.0.1:18770",
        default_remote_agent="planner",
        default_model_provider="lmstudio",
        default_model_id="qwen-default",
        a2a_agent_factory=_FakeA2AAgent,
    )

    result = planner.plan_with_model(
        task="Conversation delivery planning",
        instructions="Return JSON",
        payload={"message": "hello"},
        model_id="qwen-override",
    )

    assert result["messages"][0]["text"] == "planned"
    assert planner._a2a_agent is not None
    assert planner._a2a_agent.calls[0]["session_id"] == "conversation-layer-planner:qwen-override"
    assert (
        "[[opencode-options:agent=planner;model_provider=lmstudio;model_id=qwen-override]]"
        in str(planner._a2a_agent.calls[0]["text"])
    )
