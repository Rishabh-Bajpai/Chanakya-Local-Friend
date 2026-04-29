from core_agent_app.config import Config


def test_air_enabled_prefers_air_base_url(monkeypatch):
    monkeypatch.setenv("AIR_ENABLED", "true")
    monkeypatch.setenv("AIR_SERVER_URL", "http://localhost:5512/")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("CONVERSATION_OPENAI_BASE_URL", "http://localhost:1234/v1")

    config = Config()

    assert config.openai_base_url == "http://localhost:5512/v1"
    assert config.conversation_openai_base_url == "http://localhost:5512/v1"
