from __future__ import annotations

import asyncio

from agent_framework import Message

from core_agent_app.db import create_session_factory
from core_agent_app.services.core_agent import (
    A2ACoreAgentAdapter,
    CORE_AGENT_INSTRUCTIONS,
)
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider


def test_core_agent_instructions_cover_shorthand_followups():
    assert "Use recent conversation history" in CORE_AGENT_INSTRUCTIONS
    assert "+6?" in CORE_AGENT_INSTRUCTIONS
    assert "add 4 to it" in CORE_AGENT_INSTRUCTIONS
    assert "Only ask for clarification when the reference is genuinely ambiguous" in (
        CORE_AGENT_INSTRUCTIONS
    )


def test_a2a_seeded_prompt_guides_shorthand_resolution(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'core-agent.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)

    adapter = A2ACoreAgentAdapter(
        url="http://a2a.example.test",
        debug=True,
        history_provider=history_provider,
        session_context_store=None,
        a2a_agent_factory=lambda *args, **kwargs: None,
    )

    asyncio.run(
        history_provider.save_messages(
            "s1",
            [
                    Message("user", ["5+6?"]),
                    Message("assistant", ["5 + 6 equals 11."]),
            ],
        )
    )

    prompt = asyncio.run(adapter._build_seeded_prompt("s1", "+6?"))

    assert "Resolve shorthand or referential follow-ups from the transcript" in prompt
    assert "User: 5+6?" in prompt
    assert "Assistant: 5 + 6 equals 11." in prompt
    assert "User: +6?" in prompt


def test_a2a_agent_prompt_can_include_opencode_agent_and_model_overrides(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'core-agent.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)

    adapter = A2ACoreAgentAdapter(
        url="http://a2a.example.test",
        debug=True,
        history_provider=history_provider,
        session_context_store=None,
        a2a_agent_factory=lambda *args, **kwargs: None,
        default_remote_agent="build",
        continuity_strategy="seeded_history",
    )

    prompt = adapter._build_agent_prompt(
        type(
            "Req",
            (),
            {
                "message": "hello",
                "metadata": {
                    "a2a_remote_agent": "plan",
                    "a2a_model_provider": "lmstudio",
                    "a2a_model_id": "qwen-test",
                },
            },
        )()
    )

    assert prompt.startswith(
        "[[opencode-options:agent=plan;model_provider=lmstudio;model_id=qwen-test;ephemeral_session=true]]"
    )
    assert prompt.endswith("\nhello")


def test_a2a_seeded_prompt_preserves_opencode_header_on_first_line(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'core-agent.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)

    adapter = A2ACoreAgentAdapter(
        url="http://a2a.example.test",
        debug=True,
        history_provider=history_provider,
        session_context_store=None,
        a2a_agent_factory=lambda *args, **kwargs: None,
        default_remote_agent="build",
        continuity_strategy="seeded_history",
    )

    asyncio.run(
        history_provider.save_messages(
            "s1",
            [
                    Message("user", ["Use plan mode"]),
                    Message("assistant", ["Okay."]),
            ],
        )
    )

    prompt = adapter._build_agent_prompt(
        type(
            "Req",
            (),
            {
                "message": "What is 8 minus 5?",
                "metadata": {
                    "a2a_remote_agent": "plan",
                    "a2a_model_provider": "lmstudio",
                    "a2a_model_id": "qwen/qwen3.5-9b",
                },
            },
        )()
    )
    options_header, clean_prompt = adapter._split_option_header(prompt)
    seeded_prompt = asyncio.run(adapter._build_seeded_prompt("s1", clean_prompt))
    combined = f"{options_header}\n{seeded_prompt}"

    assert combined.splitlines()[0] == (
        "[[opencode-options:agent=plan;model_provider=lmstudio;model_id=qwen/qwen3.5-9b;ephemeral_session=true]]"
    )
    assert "User: What is 8 minus 5?" in combined
