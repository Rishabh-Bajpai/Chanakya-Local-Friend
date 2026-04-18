from __future__ import annotations

from dataclasses import dataclass

from conversation_layer.schemas import ChatRequest, ChatResponse
from core_agent_app import create_app


@dataclass(slots=True)
class FakeWrapperStateStore:
    def clear(self, session_id: str) -> None:
        return None


@dataclass(slots=True)
class FakeWrapper:
    state_store: FakeWrapperStateStore

    def runtime_options(self) -> dict:
        return {
            "conversation_preferences": {
                "supported_fields": [
                    "delay_between_messages_ms",
                    "conversation_tone_instruction",
                    "tts_instruction",
                ],
                "defaults": {
                    "delay_between_messages_ms": 5000,
                    "conversation_tone_instruction": "default tone",
                    "tts_instruction": "default tts",
                },
            }
        }

    def handle(self, chat_request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            session_id=chat_request.session_id,
            response="ok",
            metadata={
                "core_agent_backend": chat_request.metadata.get("core_agent_backend"),
                "core_agent_target": chat_request.metadata.get("core_agent_target"),
            },
        )

    def list_debug_view(self, session_id: str) -> dict:
        return {"session_id": session_id}

    def request_manual_pause(self, session_id: str) -> dict:
        return {"session_id": session_id}

    def deliver_next_message(self, session_id: str) -> dict:
        return {"status": "idle", "working_memory": {"session_id": session_id}}

    def get_agent_debug_state(self, session_id: str) -> dict:
        return {"session_id": session_id}


@dataclass(slots=True)
class FakeRawAgent:
    def runtime_options(self) -> dict:
        return {
            "default_backend": "openai_compatible",
            "default_target": "default",
            "targets": [
                {
                    "backend": "openai_compatible",
                    "key": "default",
                    "label": "OpenAI-Compatible",
                },
                {
                    "backend": "a2a",
                    "key": "opencode",
                    "label": "OpenCode A2A",
                    "remote_agents": ["build", "plan"],
                    "models": [
                        {
                            "provider": "lmstudio",
                            "id": "qwen-test",
                            "label": "Qwen Test",
                        }
                    ],
                },
            ],
        }

    def get_debug_state(self, session_id: str) -> dict:
        return {"session_id": session_id}


def test_runtime_options_route_returns_configured_targets(tmp_path):
    app = create_app(
        test_config={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
            "AGENT_DATABASE_URL": f"sqlite:///{tmp_path / 'agent.db'}",
            "RAW_AGENT": FakeRawAgent(),
        },
        wrapper=FakeWrapper(state_store=FakeWrapperStateStore()),
    )
    client = app.test_client()

    response = client.get("/runtime/options")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["core_agent"]["default_backend"] == "openai_compatible"
    assert payload["core_agent"]["targets"][1]["key"] == "opencode"
    assert payload["core_agent"]["targets"][1]["remote_agents"] == ["build", "plan"]
    assert "conversation_orchestration" in payload
    assert "default_model_id" in payload["conversation_orchestration"]
    assert "conversation_layer" in payload
    assert (
        payload["conversation_layer"]["conversation_preferences"]["supported_fields"][
            -1
        ]
        == "tts_instruction"
    )


def test_chat_route_preserves_requested_backend_choice(tmp_path):
    app = create_app(
        test_config={
            "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
            "AGENT_DATABASE_URL": f"sqlite:///{tmp_path / 'agent.db'}",
            "RAW_AGENT": FakeRawAgent(),
        },
        wrapper=FakeWrapper(state_store=FakeWrapperStateStore()),
    )
    client = app.test_client()

    response = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "message": "hello",
            "metadata": {
                "core_agent_backend": "a2a",
                "core_agent_target": "opencode",
            },
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["metadata"]["core_agent_backend"] == "a2a"
    assert payload["metadata"]["core_agent_target"] == "opencode"
