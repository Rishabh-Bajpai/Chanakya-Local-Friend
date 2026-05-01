from __future__ import annotations

from core_agent_app.services.opencode_discovery import discover_opencode_options


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.responses = {
            "/agent": [
                {"name": "build"},
                {"name": "plan"},
            ],
            "/global/config": {
                "agent": {"build": {}, "plan": {}},
                "provider": {
                    "lmstudio": {
                        "models": {
                            "qwen/qwen3.5-9b": {},
                        }
                    }
                },
            },
            "/provider": {
                "all": [
                    {
                        "id": "lmstudio",
                        "models": {
                            "qwen/qwen3.5-9b": {
                                "name": "Qwen 3.5 9B",
                            }
                        },
                    }
                ]
            },
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, path: str):
        return FakeResponse(self.responses[path])


def test_discover_opencode_options_extracts_agents_and_models(monkeypatch):
    monkeypatch.setattr(
        "core_agent_app.services.opencode_discovery.httpx.Client",
        FakeClient,
    )

    payload = discover_opencode_options("http://127.0.0.1:18496")

    assert payload["remote_agents"] == ["build", "plan"]
    assert payload["models"] == [
        {
            "provider": "lmstudio",
            "id": "qwen/qwen3.5-9b",
            "label": "Qwen 3.5 9B",
        }
    ]
