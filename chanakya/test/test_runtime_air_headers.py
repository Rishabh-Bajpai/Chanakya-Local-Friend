from unittest.mock import patch

from chanakya.agent.runtime import create_openai_chat_client


def test_create_openai_chat_client_forwards_default_headers():
    with patch("chanakya.agent.runtime.OpenAIChatClient") as mock_client:
        create_openai_chat_client(
            model_id="test-model",
            env_file_path=".env",
            default_headers={
                "x-request-id": "req-1",
                "x-chanakya-request-id": "req-1",
                "x-session-id": "sess-1",
            },
        )

    kwargs = mock_client.call_args.kwargs
    assert kwargs["default_headers"] == {
        "x-request-id": "req-1",
        "x-chanakya-request-id": "req-1",
        "x-session-id": "sess-1",
    }
