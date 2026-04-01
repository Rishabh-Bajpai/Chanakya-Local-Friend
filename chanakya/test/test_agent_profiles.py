from __future__ import annotations

import json
from pathlib import Path

from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.model import AgentProfileModel
from chanakya.seed import load_agent_seeds
from chanakya.store import ChanakyaStore


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


def test_load_agent_seeds_creates_missing_agents_without_overwriting_existing(
    tmp_path: Path,
) -> None:
    store = _build_store()
    existing = AgentProfileModel(
        id="agent_developer",
        name="Custom Developer",
        role="developer",
        system_prompt="You are the customized developer agent.",
        personality="sharp, fast",
        tool_ids_json=["mcp_fetch"],
        workspace="custom-dev-workspace",
        heartbeat_enabled=True,
        heartbeat_interval_seconds=120,
        heartbeat_file_path="chanakya_data/heartbeats/custom-developer.md",
        is_active=True,
        created_at="2026-04-01T00:00:00+00:00",
        updated_at="2026-04-01T00:00:00+00:00",
    )
    store.upsert_agent_profile(existing)

    seed_file = tmp_path / "agents.json"
    seed_file.write_text(
        json.dumps(
            [
                {
                    "id": "agent_developer",
                    "name": "Seed Developer",
                    "role": "developer",
                    "system_prompt": "You are the seeded developer agent.",
                    "personality": "methodical",
                    "tool_ids": [],
                    "workspace": "seed-workspace",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/heartbeats/developer.md",
                    "is_active": True,
                },
                {
                    "id": "agent_tester",
                    "name": "Seed Tester",
                    "role": "tester",
                    "system_prompt": "You are the seeded tester agent.",
                    "personality": "skeptical",
                    "tool_ids": [],
                    "workspace": "qa-workspace",
                    "heartbeat_enabled": False,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_file_path": "chanakya_data/heartbeats/tester.md",
                    "is_active": True,
                },
            ]
        ),
        encoding="utf-8",
    )

    load_agent_seeds(store, seed_file)

    developer = store.get_agent_profile("agent_developer")
    tester = store.get_agent_profile("agent_tester")

    assert developer.name == "Custom Developer"
    assert developer.system_prompt == "You are the customized developer agent."
    assert developer.personality == "sharp, fast"
    assert developer.tool_ids_json == ["mcp_fetch"]
    assert developer.workspace == "custom-dev-workspace"
    assert developer.heartbeat_enabled is True
    assert developer.heartbeat_interval_seconds == 120
    assert developer.heartbeat_file_path == "chanakya_data/heartbeats/custom-developer.md"

    assert tester.name == "Seed Tester"
    assert tester.role == "tester"
    assert tester.system_prompt == "You are the seeded tester agent."
