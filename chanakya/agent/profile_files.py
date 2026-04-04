from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from chanakya.model import AgentProfileModel


_TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(slots=True)
class AgentSkillDefinition:
    name: str
    description: str
    triggers: list[str]
    constraints: str


@dataclass(slots=True)
class AgentFileProfile:
    agent_id: str
    name: str
    role: str
    description: str
    personality: str
    instructions: str
    skill_names: list[str]


class FileAccessGuard:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.agents_root = (self.repo_root / "chanakya_data" / "agents").resolve()

    def allowed_root(self, agent_id: str) -> Path:
        return (self.agents_root / agent_id).resolve()

    def resolve_agent_path(self, agent_id: str, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute():
            raise ValueError("Agent file path must be relative")
        cleaned_parts = tuple(part for part in raw.parts if part not in ("", "."))
        if not cleaned_parts:
            raise ValueError("Agent file path must not be empty")
        if any(part == ".." for part in cleaned_parts):
            raise ValueError("Agent file path must not contain parent traversal")

        root = self.allowed_root(agent_id)
        target = (root.joinpath(*cleaned_parts)).resolve()
        if target != root and root not in target.parents:
            raise PermissionError(f"Access denied outside agent scope: {agent_id}")
        return target

    def assert_agent_path(self, agent_id: str, target: Path) -> None:
        root = self.allowed_root(agent_id)
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"Agent cannot access files outside scope: {agent_id}")


def default_heartbeat_relative_path(agent_id: str) -> str:
    return f"chanakya_data/agents/{agent_id}/heartbeat.md"


def ensure_agent_profile_files(profile: AgentProfileModel, repo_root: Path) -> None:
    guard = FileAccessGuard(repo_root)
    root = guard.allowed_root(profile.id)
    root.mkdir(parents=True, exist_ok=True)

    agent_path = guard.resolve_agent_path(profile.id, "AGENT.md")
    if not agent_path.exists():
        agent_path.write_text(_render_agent_md(profile), encoding="utf-8")

    skills_path = guard.resolve_agent_path(profile.id, "SKILLS.md")
    if not skills_path.exists():
        skills_path.write_text(_render_skills_md(profile), encoding="utf-8")

    heartbeat_path = guard.resolve_agent_path(profile.id, "heartbeat.md")
    if not heartbeat_path.exists():
        heartbeat_path.write_text(_render_heartbeat_md(profile), encoding="utf-8")

    access_path = guard.resolve_agent_path(profile.id, "access.json")
    if not access_path.exists():
        access_path.write_text(
            (
                "{\n"
                f'  "agent_id": "{profile.id}",\n'
                f'  "allowed_root": "chanakya_data/agents/{profile.id}",\n'
                '  "policy": "owner_only"\n'
                "}\n"
            ),
            encoding="utf-8",
        )


def load_agent_prompt(
    profile: AgentProfileModel,
    *,
    repo_root: Path,
    usage_text: str = "",
) -> str:
    guard = FileAccessGuard(repo_root)
    agent_md = guard.resolve_agent_path(profile.id, "AGENT.md")
    skills_md = guard.resolve_agent_path(profile.id, "SKILLS.md")
    if not agent_md.exists() or not skills_md.exists():
        return str(profile.system_prompt)

    agent_profile = parse_agent_md(agent_md.read_text(encoding="utf-8"))
    skills = parse_skills_md(skills_md.read_text(encoding="utf-8"))
    selected = select_relevant_skills(skills, usage_text=usage_text, max_count=3)
    return compose_prompt_from_files(agent_profile, selected)


def parse_agent_md(content: str) -> AgentFileProfile:
    sections = _parse_sections(content)
    identity = _parse_identity_section(sections.get("identity", ""))
    description = sections.get("description", "").strip()
    personality = sections.get("personality", "").strip()
    instructions = sections.get("instructions", "").strip()
    skill_names = _parse_bullet_list(sections.get("skills", ""))
    if not identity.get("agent_id"):
        raise ValueError("AGENT.md missing Identity Agent ID")
    if not identity.get("name"):
        raise ValueError("AGENT.md missing Identity Name")
    if not identity.get("role"):
        raise ValueError("AGENT.md missing Identity Role")
    if not description:
        raise ValueError("AGENT.md missing Description section")
    if not personality:
        raise ValueError("AGENT.md missing Personality section")
    if not instructions:
        raise ValueError("AGENT.md missing Instructions section")
    if not skill_names:
        raise ValueError("AGENT.md missing Skills section")
    return AgentFileProfile(
        agent_id=identity["agent_id"],
        name=identity["name"],
        role=identity["role"],
        description=description,
        personality=personality,
        instructions=instructions,
        skill_names=skill_names,
    )


def parse_skills_md(content: str) -> list[AgentSkillDefinition]:
    blocks = _split_skill_blocks(content)
    skills: list[AgentSkillDefinition] = []
    for name, block in blocks:
        sections = _parse_subsections(block)
        description = sections.get("description", "").strip()
        if not description:
            description = _extract_inline_field(block, "description")
        triggers = _parse_bullet_list(sections.get("when to use", ""))
        if not triggers:
            triggers = _parse_bullet_list(_extract_inline_list_field(block, "when to use"))
        constraints = sections.get("constraints", "").strip()
        if not constraints:
            constraints = _extract_inline_field(block, "constraints")
        if not description:
            raise ValueError(f"SKILLS.md skill '{name}' missing description")
        skills.append(
            AgentSkillDefinition(
                name=name.strip(),
                description=description,
                triggers=triggers,
                constraints=constraints,
            )
        )
    if not skills:
        raise ValueError("SKILLS.md must include at least one skill block")
    return skills


def select_relevant_skills(
    skills: list[AgentSkillDefinition],
    *,
    usage_text: str,
    max_count: int,
) -> list[AgentSkillDefinition]:
    if not skills:
        return []
    if max_count <= 0:
        return []

    normalized = set(_tokenize(usage_text))
    if not normalized:
        return skills[:max_count]

    scored: list[tuple[int, AgentSkillDefinition]] = []
    for skill in skills:
        score = 0
        lowered_name = skill.name.lower()
        if lowered_name and lowered_name in usage_text.lower():
            score += 5
        score += 2 * len(normalized.intersection(_tokenize(skill.name)))
        score += len(normalized.intersection(_tokenize(skill.description)))
        trigger_tokens: set[str] = set()
        for trigger in skill.triggers:
            trigger_tokens.update(_tokenize(trigger))
        score += len(normalized.intersection(trigger_tokens))
        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [item[1] for item in scored[:max_count]]
    return skills[:1]


def compose_prompt_from_files(
    agent_profile: AgentFileProfile,
    selected_skills: list[AgentSkillDefinition],
) -> str:
    lines = [
        f"You are {agent_profile.name}.",
        f"Agent ID: {agent_profile.agent_id}",
        f"Role: {agent_profile.role}",
        "",
        "Description:",
        agent_profile.description,
        "",
        "Personality:",
        agent_profile.personality,
        "",
        "Core Instructions:",
        agent_profile.instructions,
    ]
    if selected_skills:
        lines.extend(["", "Relevant Skills:"])
        for skill in selected_skills:
            lines.append(f"- {skill.name}: {skill.description}")
            if skill.triggers:
                lines.append(f"  When to use: {'; '.join(skill.triggers)}")
            if skill.constraints:
                lines.append(f"  Constraints: {skill.constraints}")
    return "\n".join(lines).strip()


def _render_agent_md(profile: AgentProfileModel) -> str:
    skills = _default_skill_names_for_role(profile.role)
    return (
        "# AGENT\n\n"
        "## Identity\n"
        f"- Agent ID: {profile.id}\n"
        f"- Name: {profile.name}\n"
        f"- Role: {profile.role}\n\n"
        "## Description\n"
        f"{profile.system_prompt.strip() or 'No description set.'}\n\n"
        "## Personality\n"
        f"{profile.personality.strip() or 'neutral'}\n\n"
        "## Instructions\n"
        "Always follow the task hierarchy and return structured, factual output.\n\n"
        "## Skills\n" + "\n".join(f"- {skill}" for skill in skills) + "\n"
    )


def _render_skills_md(profile: AgentProfileModel) -> str:
    role = profile.role.strip().lower()
    defaults = _default_skills_for_role(role)
    blocks: list[str] = ["# SKILLS\n"]
    for item in defaults:
        blocks.append(
            (
                f"## {item['name']}\n"
                "### Description\n"
                f"{item['description']}\n\n"
                "### When to Use\n" + "\n".join(f"- {value}" for value in item["triggers"]) + "\n\n"
                "### Constraints\n"
                f"{item['constraints']}\n"
            )
        )
    return "\n".join(blocks).strip() + "\n"


def _render_heartbeat_md(profile: AgentProfileModel) -> str:
    return (
        f"# Heartbeat for {profile.name}\n\n"
        "- Pending task check: none yet\n"
        "- Notes: heartbeat execution will be added in Milestone 9\n"
        "- Last updated: pending\n"
    )


def _default_skill_names_for_role(role: str) -> list[str]:
    return [item["name"] for item in _default_skills_for_role(role.strip().lower())]


def _default_skills_for_role(role: str) -> list[dict[str, str | list[str]]]:
    role_defaults: dict[str, list[dict[str, str | list[str]]]] = {
        "manager": [
            {
                "name": "route_request",
                "description": "Select the correct specialist and preserve workflow determinism.",
                "triggers": ["Delegation", "Top-level routing", "Workflow selection"],
                "constraints": "Do not solve worker tasks directly.",
            },
            {
                "name": "aggregate_results",
                "description": "Merge specialist outputs into concise final summaries.",
                "triggers": ["Final response", "Supervisor summary", "Task closure"],
                "constraints": "Do not invent claims beyond worker outputs.",
            },
        ],
        "developer": [
            {
                "name": "implement_changes",
                "description": "Produce implementation changes aligned with the brief.",
                "triggers": ["Code changes", "Feature implementation", "Bug fix"],
                "constraints": "Do not claim testing is complete.",
            },
            {
                "name": "handoff_to_tester",
                "description": "Provide assumptions, risk notes, and test focus areas.",
                "triggers": ["Implementation complete", "Prepare QA handoff"],
                "constraints": "Keep output structured and explicit.",
            },
        ],
        "tester": [
            {
                "name": "validate_implementation",
                "description": "Assess implementation outcome against requirements.",
                "triggers": ["Post-implementation validation", "Regression checks"],
                "constraints": "Do not rewrite implementation plan.",
            },
            {
                "name": "report_quality_risk",
                "description": "Report defects and residual risks with pass/fail guidance.",
                "triggers": ["Defect findings", "Quality recommendation"],
                "constraints": "Keep recommendations evidence-based.",
            },
        ],
        "researcher": [
            {
                "name": "gather_facts",
                "description": "Collect references, facts, and structured findings.",
                "triggers": ["Research request", "Fact collection", "Reference gathering"],
                "constraints": "Preserve uncertainty when evidence is incomplete.",
            },
            {
                "name": "prepare_writer_handoff",
                "description": "Provide concise notes for downstream writing tasks.",
                "triggers": ["Research complete", "Writer handoff"],
                "constraints": "Do not produce unsupported final claims.",
            },
        ],
        "writer": [
            {
                "name": "polish_output",
                "description": "Transform research notes into clear user-facing communication.",
                "triggers": ["Draft response", "User-facing write-up"],
                "constraints": "Preserve caveats and avoid fabricated statements.",
            },
            {
                "name": "structure_for_readability",
                "description": "Apply clear structure and concise delivery.",
                "triggers": ["Long explanations", "Summaries", "Instructions"],
                "constraints": "Keep tone grounded and factual.",
            },
        ],
    }
    return role_defaults.get(
        role,
        [
            {
                "name": "execute_assigned_task",
                "description": "Perform assigned task within role boundaries.",
                "triggers": ["General request", "Assigned task execution"],
                "constraints": "Follow hierarchy and avoid unsupported claims.",
            }
        ],
    )


def _parse_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _parse_identity_section(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        cleaned = line.strip().lstrip("- ").strip()
        if ":" not in cleaned:
            continue
        key, value = cleaned.split(":", 1)
        normalized = key.strip().lower().replace(" ", "_")
        result[normalized] = value.strip()
    return {
        "agent_id": result.get("agent_id", ""),
        "name": result.get("name", ""),
        "role": result.get("role", ""),
    }


def _parse_bullet_list(content: str) -> list[str]:
    items: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value:
                items.append(value)
    return items


def _split_skill_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current_name = ""
    current_lines: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            if current_name:
                blocks.append((current_name, "\n".join(current_lines).strip()))
            current_name = line[3:].strip()
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        blocks.append((current_name, "\n".join(current_lines).strip()))
    return blocks


def _parse_subsections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            current = line[4:].strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _extract_inline_field(content: str, field_name: str) -> str:
    pattern = re.compile(rf"^{re.escape(field_name)}\s*:\s*(.+)$", re.IGNORECASE)
    for line in content.splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _extract_inline_list_field(content: str, field_name: str) -> str:
    found = _extract_inline_field(content, field_name)
    if not found:
        return ""
    items = [item.strip() for item in found.split(";") if item.strip()]
    return "\n".join(f"- {item}" for item in items)


def _tokenize(value: str) -> set[str]:
    return {token for token in _TOKEN_PATTERN.findall(value.lower())}
