from __future__ import annotations

import asyncio

from agent_framework import Message

from core_agent_app.db import create_session_factory
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider


def test_sqlalchemy_history_provider_round_trip(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'history.db'}")
    provider = SQLAlchemyHistoryProvider(session_factory)

    asyncio.run(
        provider.save_messages(
            "session-1",
            [Message("user", ["hello"]), Message("assistant", ["hi there"])],
        )
    )
    restored = asyncio.run(provider.get_messages("session-1"))

    assert [message.role for message in restored] == ["user", "assistant"]
    assert [message.text for message in restored] == ["hello", "hi there"]


def test_rewrite_latest_assistant_turn_replaces_trailing_assistant_block(tmp_path):
    session_factory = create_session_factory(
        f"sqlite:///{tmp_path / 'history-rewrite.db'}"
    )
    provider = SQLAlchemyHistoryProvider(session_factory)

    asyncio.run(
        provider.save_messages(
            "session-1",
            [
                Message("user", ["hello"]),
                Message("assistant", ["first chunk"]),
                Message("assistant", ["second chunk"]),
            ],
        )
    )

    provider.rewrite_latest_assistant_turn(
        "session-1",
        ["rewritten chunk 1", "rewritten chunk 2"],
    )
    history = provider.list_messages("session-1")

    assert [item["role"] for item in history] == ["user", "assistant", "assistant"]
    assert [item["text"] for item in history] == [
        "hello",
        "rewritten chunk 1",
        "rewritten chunk 2",
    ]


def test_append_conversation_turn_persists_user_and_assistant_messages(tmp_path):
    session_factory = create_session_factory(
        f"sqlite:///{tmp_path / 'history-append.db'}"
    )
    provider = SQLAlchemyHistoryProvider(session_factory)

    provider.append_conversation_turn(
        "session-1",
        user_message="follow up",
        assistant_message="visible reply",
    )
    history = provider.list_messages("session-1")

    assert [item["role"] for item in history] == ["user", "assistant"]
    assert [item["text"] for item in history] == ["follow up", "visible reply"]


def test_list_and_delete_sessions(tmp_path):
    session_factory = create_session_factory(
        f"sqlite:///{tmp_path / 'history-sessions.db'}"
    )
    provider = SQLAlchemyHistoryProvider(session_factory)

    asyncio.run(
        provider.save_messages(
            "session-a",
            [Message("user", ["hello a"]), Message("assistant", ["reply a"])],
        )
    )
    asyncio.run(
        provider.save_messages(
            "session-b",
            [Message("user", ["hello b"]), Message("assistant", ["reply b"])],
        )
    )

    sessions = provider.list_sessions()
    assert [item["session_id"] for item in sessions] == ["session-a", "session-b"]

    provider.delete_session("session-a")

    sessions_after_delete = provider.list_sessions()
    assert [item["session_id"] for item in sessions_after_delete] == ["session-b"]
