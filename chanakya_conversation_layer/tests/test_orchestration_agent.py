from __future__ import annotations

from types import SimpleNamespace

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
    assert (
        planner._a2a_agent.calls[0]["session_id"]
        == "conversation-layer-planner:qwen-override"
    )
    assert (
        "[[opencode-options:agent=planner;model_provider=lmstudio;model_id=qwen-override]]"
        in str(planner._a2a_agent.calls[0]["text"])
    )


def test_orchestration_agent_disables_openai_transport_retries() -> None:
    planner = MAFOrchestrationAgent(
        model="qwen-default",
        base_url="http://127.0.0.1:1234/v1",
        api_key="test-key",
        env_file_path=".env",
    )

    assert planner._agent is not None
    assert planner._agent.client.client is not None
    assert planner._agent.client.client.max_retries == 0

    override_agent = planner._agent_for_model("qwen-override")

    assert override_agent.client.client is not None
    assert override_agent.client.client.max_retries == 0


def test_orchestration_agent_uses_fresh_openai_agent_per_plan_call(monkeypatch) -> None:
    created_clients: list[object] = []
    seen_client_ids: list[int] = []

    class _FakeAgent:
        def __init__(self, *, client, name: str, description: str, instructions: str) -> None:
            self.client = client

        async def run(self, prompt: str):
            seen_client_ids.append(id(self.client))
            return SimpleNamespace(text='{"messages":[{"text":"planned","delay_ms":0}]}')

    def _fake_build_openai_chat_client(self, model_id: str, default_headers=None):
        client = SimpleNamespace(client=SimpleNamespace(max_retries=0), model_id=model_id, default_headers=default_headers)
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        "conversation_layer.services.orchestration_agent.Agent",
        _FakeAgent,
    )
    monkeypatch.setattr(
        MAFOrchestrationAgent,
        "_build_openai_chat_client",
        _fake_build_openai_chat_client,
    )

    planner = MAFOrchestrationAgent(
        model="qwen-default",
        base_url="http://127.0.0.1:1234/v1",
        api_key="test-key",
        env_file_path=".env",
    )

    first = planner.plan(
        task="Working memory routing",
        instructions="Return JSON",
        payload={"message": "hello"},
    )
    second = planner.plan(
        task="Conversation delivery planning",
        instructions="Return JSON",
        payload={"message": "hello again"},
    )

    assert first["messages"][0]["text"] == "planned"
    assert second["messages"][0]["text"] == "planned"
    assert len(created_clients) >= 3
    assert len(seen_client_ids) == 2
    assert seen_client_ids[0] != seen_client_ids[1]
