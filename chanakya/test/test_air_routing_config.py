from chanakya.config import get_conversation_openai_config, get_openai_compatible_config


def test_air_enabled_prefers_air_base_url(monkeypatch):
    monkeypatch.setenv("AIR_ENABLED", "true")
    monkeypatch.setenv("AIR_SERVER_URL", "http://localhost:5512/")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")

    config = get_openai_compatible_config()

    assert config["base_url"] == "http://localhost:5512/v1"


def test_conversation_air_enabled_prefers_air_base_url(monkeypatch):
    monkeypatch.setenv("AIR_ENABLED", "true")
    monkeypatch.setenv("AIR_SERVER_URL", "http://localhost:5512/")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("CONVERSATION_OPENAI_BASE_URL", "http://localhost:1234/v1")

    config = get_conversation_openai_config()

    assert config["base_url"] == "http://localhost:5512/v1"
