from __future__ import annotations

from pathlib import Path

import pytest

from chanakya.agent.profile_files import (
    FileAccessGuard,
    ensure_agent_profile_files,
    load_agent_prompt,
    parse_agent_md,
    parse_skills_md,
    select_relevant_skills,
)
from chanakya.model import AgentProfileModel


def _profile() -> AgentProfileModel:
    return AgentProfileModel(
        id="agent_researcher",
        name="Researcher",
        role="researcher",
        system_prompt="You are a research worker.",
        personality="curious, analytical",
        tool_ids_json=[],
        workspace="research-workspace",
        heartbeat_enabled=False,
        heartbeat_interval_seconds=300,
        heartbeat_file_path="chanakya_data/agents/agent_researcher/heartbeat.md",
        is_active=True,
        created_at="2026-04-03T00:00:00+00:00",
        updated_at="2026-04-03T00:00:00+00:00",
    )


def test_ensure_agent_profile_files_creates_required_documents(tmp_path: Path) -> None:
    profile = _profile()

    ensure_agent_profile_files(profile, tmp_path)

    root = tmp_path / "chanakya_data" / "agents" / profile.id
    assert (root / "AGENT.md").exists()
    assert (root / "SKILLS.md").exists()
    assert (root / "heartbeat.md").exists()
    assert (root / "access.json").exists()


def test_load_agent_prompt_uses_relevant_skills(tmp_path: Path) -> None:
    profile = _profile()
    ensure_agent_profile_files(profile, tmp_path)

    prompt = load_agent_prompt(
        profile,
        repo_root=tmp_path,
        usage_text="Need references and fact gathering for a report",
    )

    assert "Relevant Skills:" in prompt
    assert "gather_facts" in prompt


def test_file_access_guard_rejects_cross_agent_paths(tmp_path: Path) -> None:
    guard = FileAccessGuard(tmp_path)

    with pytest.raises(PermissionError):
        guard.assert_agent_path(
            "agent_one",
            (tmp_path / "chanakya_data" / "agents" / "agent_two" / "AGENT.md"),
        )


def test_parse_agent_and_skills_markdown() -> None:
    agent_content = (
        "# AGENT\n\n"
        "## Identity\n"
        "- Agent ID: agent_writer\n"
        "- Name: Writer\n"
        "- Role: writer\n\n"
        "## Description\n"
        "Create polished output.\n\n"
        "## Personality\n"
        "clear, polished\n\n"
        "## Instructions\n"
        "Follow research handoff and preserve caveats.\n\n"
        "## Skills\n"
        "- polish_output\n"
        "- structure_for_readability\n"
    )
    skills_content = (
        "# SKILLS\n\n"
        "## polish_output\n"
        "### Description\n"
        "Transform research notes into readable responses.\n\n"
        "### When to Use\n"
        "- Draft response\n\n"
        "### Constraints\n"
        "Do not invent unsupported claims.\n"
    )

    agent = parse_agent_md(agent_content)
    skills = parse_skills_md(skills_content)
    selected = select_relevant_skills(skills, usage_text="draft response", max_count=2)

    assert agent.agent_id == "agent_writer"
    assert agent.skill_names == ["polish_output", "structure_for_readability"]
    assert len(skills) == 1
    assert selected[0].name == "polish_output"
