from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Message
from agent_framework.openai import OpenAIChatClient
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState
from sqlalchemy.orm import Session, sessionmaker

from chanakya.agent.runtime import build_profile_agent
from chanakya.config import (
    get_agent_request_timeout_seconds,
    get_long_running_agent_request_timeout_seconds,
    get_subagent_group_chat_round_multiplier,
)
from chanakya.debug import debug_log, with_transient_retry
from chanakya.domain import make_id, now_iso
from chanakya.model import AgentProfileModel, TemporaryAgentModel
from chanakya.services.async_loop import run_in_maf_loop
from chanakya.store import ChanakyaStore

TEMPORARY_AGENT_STATUS_CREATED = "created"
TEMPORARY_AGENT_STATUS_ACTIVE = "active"
TEMPORARY_AGENT_STATUS_CLEANING_UP = "cleaning_up"
TEMPORARY_AGENT_STATUS_CLEANED = "cleaned"
TEMPORARY_AGENT_STATUS_FAILED = "failed"

WORKER_ROLES_WITH_SUBAGENTS = {"developer", "tester", "researcher", "writer"}


@dataclass(slots=True)
class TemporaryAgentPlan:
    name_suffix: str
    role: str
    purpose: str
    instructions: str
    expected_output: str
    tool_ids: list[str]


@dataclass(slots=True)
class WorkerSubagentPlan:
    needs_subagents: bool
    orchestration_mode: str
    goal: str
    helpers: list[TemporaryAgentPlan]


@dataclass(slots=True)
class WorkerSubagentDecision:
    should_create_subagents: bool
    reason: str
    complexity: str
    helper_count: int


@dataclass(slots=True)
class CreatedTemporaryAgent:
    record: TemporaryAgentModel
    task_id: str


@dataclass(slots=True)
class WorkerSubagentRunResult:
    output_text: str
    child_task_ids: list[str]
    worker_agent_ids: list[str]
    temporary_agent_ids: list[str]


def can_create_temporary_subagents(profile: AgentProfileModel) -> bool:
    return profile.role in WORKER_ROLES_WITH_SUBAGENTS


def build_subagent_planning_prompt(
    *,
    worker_profile: AgentProfileModel,
    message: str,
    effective_prompt: str,
) -> str:
    return (
        f"You are {worker_profile.name}, acting as a {worker_profile.role}. "
        "Decide whether this task should be decomposed into temporary helper subagents. "
        "Return only valid JSON. Use helper agents only when they materially improve quality, speed, or parallel fact gathering. "
        "Do not create more than 3 helpers. If no helpers are needed, return an empty helpers array. "
        'Supported orchestration_mode values are "direct" and "group_chat". Use "direct" only when needs_subagents is false and helpers is empty. '
        'Use "group_chat" whenever helpers are provided.\n\n'
        f"Original request: {message}\n\n"
        f"Your current execution prompt: {effective_prompt}\n\n"
        "Return JSON with this exact schema:\n"
        '{"needs_subagents":false,"orchestration_mode":"direct","goal":"...","helpers":[]}\n'
        "If helpers are needed, return:\n"
        '{"needs_subagents":true,"orchestration_mode":"group_chat","goal":"...","helpers":[{"name_suffix":"facts","role":"research_helper","purpose":"...","instructions":"...","expected_output":"...","tool_ids":[]}]}'
    )


def build_subagent_decision_prompt(
    *,
    worker_profile: AgentProfileModel,
    message: str,
    effective_prompt: str,
) -> str:
    return (
        f"You are {worker_profile.name}, acting as a {worker_profile.role}. "
        "Decide whether this worker task should create temporary helper subagents before executing. "
        "Return only valid JSON. Use subagents only when decomposition materially improves the result through bounded parallel work, scoped investigation, or task separation. "
        "Do not assume subagents are required just because the task is non-trivial.\n\n"
        f"Original request: {message}\n\n"
        f"Worker execution prompt: {effective_prompt}\n\n"
        "Return JSON with this exact schema:\n"
        '{"should_create_subagents":false,"reason":"...","complexity":"low|medium|high","helper_count":0}'
    )


def parse_worker_subagent_decision(raw: str) -> WorkerSubagentDecision | None:
    payload = _extract_json_object(raw)
    if payload is None:
        return None
    complexity = str(payload.get("complexity", "medium")).strip().lower() or "medium"
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    reason = str(payload.get("reason", "")).strip()
    helper_count_raw = payload.get("helper_count", 0)
    try:
        helper_count = max(0, min(int(helper_count_raw), 3))
    except (TypeError, ValueError):
        helper_count = 0
    should_create_subagents = bool(payload.get("should_create_subagents", False))
    if should_create_subagents and helper_count <= 0:
        return None
    if not should_create_subagents:
        helper_count = 0
    return WorkerSubagentDecision(
        should_create_subagents=should_create_subagents,
        reason=reason or "No reason provided.",
        complexity=complexity,
        helper_count=helper_count,
    )


def parse_worker_subagent_plan(raw: str) -> WorkerSubagentPlan | None:
    payload = _extract_json_object(raw)
    if payload is None:
        return None
    needs_subagents = bool(payload.get("needs_subagents", False))
    orchestration_mode = str(payload.get("orchestration_mode", "direct")).strip().lower()
    if orchestration_mode not in {"direct", "group_chat"}:
        orchestration_mode = "direct"
    goal = str(payload.get("goal", "")).strip()
    helper_payloads = payload.get("helpers", [])
    if not isinstance(helper_payloads, list):
        return None
    helpers: list[TemporaryAgentPlan] = []
    for item in helper_payloads[:3]:
        if not isinstance(item, dict):
            continue
        name_suffix = str(item.get("name_suffix", "helper")).strip() or "helper"
        role = str(item.get("role", "temporary_helper")).strip() or "temporary_helper"
        purpose = str(item.get("purpose", "")).strip()
        instructions = str(item.get("instructions", "")).strip()
        expected_output = str(item.get("expected_output", "")).strip()
        tool_ids_raw = item.get("tool_ids", [])
        tool_ids = (
            [str(value).strip() for value in tool_ids_raw if str(value).strip()]
            if isinstance(tool_ids_raw, list)
            else []
        )
        if not purpose or not instructions or not expected_output:
            continue
        helpers.append(
            TemporaryAgentPlan(
                name_suffix=name_suffix,
                role=role,
                purpose=purpose,
                instructions=instructions,
                expected_output=expected_output,
                tool_ids=tool_ids,
            )
        )
    if needs_subagents and not helpers:
        return None
    if not needs_subagents:
        helpers = []
        orchestration_mode = "direct"
    elif helpers:
        orchestration_mode = "group_chat"
    return WorkerSubagentPlan(
        needs_subagents=needs_subagents,
        orchestration_mode=orchestration_mode,
        goal=goal or "Produce the parent worker result.",
        helpers=helpers,
    )


class WorkerSubagentOrchestrator:
    def __init__(
        self,
        *,
        store: ChanakyaStore,
        session_factory: sessionmaker[Session],
        client: OpenAIChatClient | None = None,
        client_factory: Callable[[], OpenAIChatClient] | None = None,
        backend_getter: Callable[[], str] | None = None,
        profile_runner_async: Callable[..., Awaitable[str]] | None = None,
    ) -> None:
        self.store = store
        self.session_factory = session_factory
        self._client = client
        self._client_factory = client_factory
        self._backend_getter = backend_getter
        self._profile_runner_async = profile_runner_async

    @property
    def client(self) -> OpenAIChatClient:
        """Return the active client, preferring the factory when available."""
        if self._client_factory is not None:
            return self._client_factory()
        assert self._client is not None, "Either client or client_factory must be provided"
        return self._client

    @property
    def backend(self) -> str:
        if self._backend_getter is None:
            return "local"
        return str(self._backend_getter() or "local").strip().lower() or "local"

    def execute(
        self,
        *,
        session_id: str,
        request_id: str,
        worker_profile: AgentProfileModel,
        worker_task_id: str,
        message: str,
        effective_prompt: str,
        plan: WorkerSubagentPlan,
    ) -> WorkerSubagentRunResult:
        if not can_create_temporary_subagents(worker_profile):
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=worker_task_id,
                event_type="subagent_creation_rejected",
                payload={
                    "parent_agent_id": worker_profile.id,
                    "role": worker_profile.role,
                    "reason": "high_level_agent_not_allowed",
                },
            )
            raise ValueError(f"Role cannot create temporary subagents: {worker_profile.role}")

        created_agents = self._create_temporary_agents(
            session_id=session_id,
            request_id=request_id,
            worker_profile=worker_profile,
            worker_task_id=worker_task_id,
            message=message,
            effective_prompt=effective_prompt,
            plan=plan,
        )
        activated_at = now_iso()
        for item in created_agents:
            self.store.update_temporary_agent(
                item.record.id,
                status=TEMPORARY_AGENT_STATUS_ACTIVE,
                activated_at=activated_at,
            )

        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=worker_task_id,
            event_type="subagent_group_started",
            payload={
                "temporary_agent_ids": [item.record.id for item in created_agents],
                "goal": plan.goal,
                "orchestration_mode": plan.orchestration_mode,
            },
        )

        try:
            outputs = run_in_maf_loop(
                self._run_group_chat_async(
                    worker_profile=worker_profile,
                    message=message,
                    effective_prompt=effective_prompt,
                    plan=plan,
                    created_agents=created_agents,
                )
            )
            helper_outputs, final_output = self._map_group_chat_outputs(outputs, created_agents)
            finished_at = now_iso()
            for item, helper_output in zip(created_agents, helper_outputs):
                self.store.update_task(
                    item.task_id,
                    status="done",
                    result_json={
                        "purpose": item.record.purpose,
                        "expected_output": item.record.metadata_json.get("expected_output"),
                        "helper_output": helper_output,
                    },
                    finished_at=finished_at,
                )
                self.store.create_task_event(
                    session_id=session_id,
                    request_id=request_id,
                    task_id=item.task_id,
                    event_type="subagent_output_ready",
                    payload={
                        "temporary_agent_id": item.record.id,
                        "parent_task_id": worker_task_id,
                    },
                )
            self._cleanup_temporary_agents(
                session_id=session_id,
                request_id=request_id,
                worker_task_id=worker_task_id,
                created_agents=created_agents,
                cleanup_reason="completed",
            )
            return WorkerSubagentRunResult(
                output_text=final_output.strip(),
                child_task_ids=[item.task_id for item in created_agents],
                worker_agent_ids=[item.record.id for item in created_agents],
                temporary_agent_ids=[item.record.id for item in created_agents],
            )
        except Exception as exc:
            finished_at = now_iso()
            for item in created_agents:
                self.store.update_task(
                    item.task_id,
                    status="failed",
                    error_text=str(exc),
                    finished_at=finished_at,
                )
            self._cleanup_temporary_agents(
                session_id=session_id,
                request_id=request_id,
                worker_task_id=worker_task_id,
                created_agents=created_agents,
                cleanup_reason=f"failed: {exc}",
                failed=True,
            )
            raise

    def _create_temporary_agents(
        self,
        *,
        session_id: str,
        request_id: str,
        worker_profile: AgentProfileModel,
        worker_task_id: str,
        message: str,
        effective_prompt: str,
        plan: WorkerSubagentPlan,
    ) -> list[CreatedTemporaryAgent]:
        created_agents: list[CreatedTemporaryAgent] = []
        for helper in plan.helpers:
            temporary_agent_id = make_id("tagent")
            timestamp = now_iso()
            inherited_tool_ids = [
                tool_id
                for tool_id in helper.tool_ids
                if tool_id in list(worker_profile.tool_ids_json or [])
            ]
            record = TemporaryAgentModel(
                id=temporary_agent_id,
                request_id=request_id,
                session_id=session_id,
                parent_agent_id=worker_profile.id,
                parent_task_id=worker_task_id,
                creator_role=worker_profile.role,
                name=f"{worker_profile.name} :: {helper.name_suffix}",
                role=helper.role,
                purpose=helper.purpose,
                system_prompt=helper.instructions,
                tool_ids_json=inherited_tool_ids,
                workspace=worker_profile.workspace,
                status=TEMPORARY_AGENT_STATUS_CREATED,
                cleanup_reason=None,
                metadata_json={
                    "expected_output": helper.expected_output,
                    "goal": plan.goal,
                    "message": message,
                    "effective_prompt": effective_prompt,
                },
                created_at=timestamp,
                updated_at=timestamp,
                activated_at=None,
                cleaned_up_at=None,
            )
            self.store.create_temporary_agent(record)
            task_id = make_id("task")
            self.store.create_task(
                task_id=task_id,
                request_id=request_id,
                parent_task_id=worker_task_id,
                title=record.name,
                summary=helper.purpose,
                status="in_progress",
                owner_agent_id=record.id,
                task_type="temporary_subagent_execution",
                input_json={
                    "purpose": helper.purpose,
                    "instructions": helper.instructions,
                    "expected_output": helper.expected_output,
                    "parent_agent_id": worker_profile.id,
                    "parent_task_id": worker_task_id,
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=task_id,
                event_type="task_created",
                payload={
                    "title": record.name,
                    "owner_agent_id": record.id,
                    "task_type": "temporary_subagent_execution",
                },
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=worker_task_id,
                event_type="subagent_created",
                payload={
                    "temporary_agent_id": record.id,
                    "temporary_task_id": task_id,
                    "parent_agent_id": worker_profile.id,
                    "purpose": helper.purpose,
                },
            )
            created_agents.append(CreatedTemporaryAgent(record=record, task_id=task_id))
        return created_agents

    async def _run_group_chat_async(
        self,
        *,
        worker_profile: AgentProfileModel,
        message: str,
        effective_prompt: str,
        plan: WorkerSubagentPlan,
        created_agents: list[CreatedTemporaryAgent],
    ) -> list[str]:
        if self.backend == "a2a":
            return await self._run_group_chat_via_profile_runner_async(
                worker_profile=worker_profile,
                message=message,
                effective_prompt=effective_prompt,
                plan=plan,
                created_agents=created_agents,
            )
        worker_agent, _ = build_profile_agent(
            worker_profile,
            self.session_factory,
            client=self.client,
            include_history=False,
            usage_text=effective_prompt,
        )
        participants = [worker_agent]
        sequence = [worker_profile.name]
        helper_descriptions: list[str] = []
        for item in created_agents:
            temporary_profile = AgentProfileModel(
                id=item.record.id,
                name=item.record.name,
                role=item.record.role,
                system_prompt=item.record.system_prompt,
                personality=f"temporary helper for {worker_profile.name}",
                tool_ids_json=item.record.tool_ids_json,
                workspace=item.record.workspace,
                heartbeat_enabled=False,
                heartbeat_interval_seconds=300,
                heartbeat_file_path=None,
                is_active=True,
                created_at=item.record.created_at,
                updated_at=item.record.updated_at,
            )
            helper_agent, _ = build_profile_agent(
                temporary_profile,
                self.session_factory,
                client=self.client,
                include_history=False,
                usage_text=f"{message}\n\n{item.record.purpose}",
            )
            participants.append(helper_agent)
            sequence.append(item.record.name)
            helper_descriptions.append(
                f"- {item.record.name}: purpose={item.record.purpose}; expected_output={item.record.metadata_json.get('expected_output', '')}"
            )
        sequence.append(worker_profile.name)

        async def selection_func(state: GroupChatState) -> str:
            index = min(state.current_round, len(sequence) - 1)
            return sequence[index]

        round_multiplier = get_subagent_group_chat_round_multiplier()
        workflow = GroupChatBuilder(
            participants=participants,
            selection_func=selection_func,
            max_rounds=max(len(sequence), len(sequence) * round_multiplier),
            intermediate_outputs=True,
        ).build()
        kickoff = (
            f"Parent request: {message}\n\n"
            f"Primary worker prompt: {effective_prompt}\n\n"
            f"Local orchestration goal: {plan.goal}\n\n"
            "Temporary helper roster:\n" + "\n".join(helper_descriptions) + "\n\n"
            "Speaker rules:\n"
            "- First message: parent worker decomposes and delegates helper tasks.\n"
            "- Helper messages: only perform your own scoped task and return your result.\n"
            "- Final message: parent worker synthesizes helper outputs into the result for the parent task.\n"
            "- No one should ask clarifying questions in this workflow.\n"
        )
        timeout_seconds = self._resolve_worker_timeout_seconds(message, effective_prompt)
        result = await with_transient_retry(
            lambda: asyncio.wait_for(
                workflow.run(
                    message=Message(role="user", text=kickoff),
                    include_status_events=True,
                ),
                timeout=timeout_seconds,
            ),
            label=f"subagent_group_chat:{worker_profile.id}",
        )
        outputs = self._extract_stage_outputs(result)
        if not outputs:
            raise ValueError("Temporary subagent workflow did not produce any usable text outputs")
        debug_log(
            "temporary_subagent_group_chat_completed",
            {
                "worker_agent_id": worker_profile.id,
                "temporary_agent_ids": [item.record.id for item in created_agents],
                "output_count": len(outputs),
                "planned_turns": len(sequence),
            },
        )
        return outputs

    async def _run_group_chat_via_profile_runner_async(
        self,
        *,
        worker_profile: AgentProfileModel,
        message: str,
        effective_prompt: str,
        plan: WorkerSubagentPlan,
        created_agents: list[CreatedTemporaryAgent],
    ) -> list[str]:
        if self._profile_runner_async is None:
            raise RuntimeError("A2A subagent execution requires a profile runner")
        worker_intro = await self._profile_runner_async(
            worker_profile,
            (
                f"Parent request: {message}\n\n"
                f"Your execution prompt: {effective_prompt}\n\n"
                f"Delegation goal: {plan.goal}\n\n"
                "Decompose the work for the named helpers and state the exact scoped task for each. "
                "Do not ask questions."
            ),
            include_history=False,
            store=False,
            use_work_session=False,
        )
        outputs = [worker_intro]
        helper_outputs: list[tuple[str, str]] = []
        for item in created_agents:
            temporary_profile = AgentProfileModel(
                id=item.record.id,
                name=item.record.name,
                role=item.record.role,
                system_prompt=item.record.system_prompt,
                personality=f"temporary helper for {worker_profile.name}",
                tool_ids_json=item.record.tool_ids_json,
                workspace=item.record.workspace,
                heartbeat_enabled=False,
                heartbeat_interval_seconds=300,
                heartbeat_file_path=None,
                is_active=True,
                created_at=item.record.created_at,
                updated_at=item.record.updated_at,
            )
            helper_prompt = (
                f"Parent request: {message}\n\n"
                f"Parent worker prompt: {effective_prompt}\n\n"
                f"Parent worker delegation note:\n{worker_intro}\n\n"
                f"Your purpose: {item.record.purpose}\n"
                f"Expected output: {item.record.metadata_json.get('expected_output', '')}\n\n"
                "Return only your scoped result with no extra commentary."
            )
            helper_output = await self._profile_runner_async(
                temporary_profile,
                helper_prompt,
                include_history=False,
                store=False,
                use_work_session=False,
            )
            helper_outputs.append((item.record.name, helper_output))
            outputs.append(helper_output)
        synthesis_chunks = [
            f"Parent request: {message}",
            f"Your execution prompt: {effective_prompt}",
            f"Delegation goal: {plan.goal}",
            f"Your earlier delegation note:\n{worker_intro}",
            "Helper outputs:",
        ]
        for helper_name, helper_output in helper_outputs:
            synthesis_chunks.append(f"{helper_name}:\n{helper_output}")
        synthesis_chunks.append(
            "Produce the final worker result for the parent task. Do not ask questions."
        )
        final_output = await self._profile_runner_async(
            worker_profile,
            "\n\n".join(synthesis_chunks),
            include_history=False,
            store=False,
            use_work_session=False,
        )
        outputs.append(final_output)
        return outputs

    def _resolve_worker_timeout_seconds(self, message: str, effective_prompt: str) -> int:
        combined = f"{message}\n{effective_prompt}".lower()
        long_running_markers = [
            "clone this website",
            "clone website",
            "crawl",
            "crawler",
            "download assets",
            "download the site",
            "subpages",
            "scrape",
            "mirror site",
        ]
        if any(marker in combined for marker in long_running_markers):
            return get_long_running_agent_request_timeout_seconds()
        return get_agent_request_timeout_seconds()

    def _map_group_chat_outputs(
        self,
        outputs: list[str],
        created_agents: list[CreatedTemporaryAgent],
    ) -> tuple[list[str], str]:
        if not outputs:
            raise ValueError("Temporary subagent workflow did not return any outputs")
        helper_count = len(created_agents)
        if len(outputs) == helper_count + 2:
            return outputs[1:-1], outputs[-1]
        if len(outputs) == helper_count + 1:
            return outputs[:-1], outputs[-1]
        if len(outputs) > helper_count + 2:
            trimmed = outputs[-(helper_count + 1) :]
            return trimmed[:-1], trimmed[-1]
        final_output = outputs[-1]
        helper_outputs = outputs[:-1]
        if len(helper_outputs) < helper_count:
            helper_outputs = ([""] * (helper_count - len(helper_outputs))) + helper_outputs
        return helper_outputs[:helper_count], final_output

    def _cleanup_temporary_agents(
        self,
        *,
        session_id: str,
        request_id: str,
        worker_task_id: str,
        created_agents: list[CreatedTemporaryAgent],
        cleanup_reason: str,
        failed: bool = False,
    ) -> None:
        started_at = now_iso()
        for item in created_agents:
            self.store.update_temporary_agent(
                item.record.id,
                status=TEMPORARY_AGENT_STATUS_CLEANING_UP,
                cleanup_reason=cleanup_reason,
            )
        self.store.create_task_event(
            session_id=session_id,
            request_id=request_id,
            task_id=worker_task_id,
            event_type="subagent_cleanup_started",
            payload={
                "temporary_agent_ids": [item.record.id for item in created_agents],
                "cleanup_reason": cleanup_reason,
                "started_at": started_at,
            },
        )
        cleaned_up_at = now_iso()
        final_status = TEMPORARY_AGENT_STATUS_FAILED if failed else TEMPORARY_AGENT_STATUS_CLEANED
        event_type = "subagent_cleanup_failed" if failed else "subagent_cleaned"
        for item in created_agents:
            self.store.update_temporary_agent(
                item.record.id,
                status=final_status,
                cleanup_reason=cleanup_reason,
                cleaned_up_at=cleaned_up_at,
            )
            self.store.create_task_event(
                session_id=session_id,
                request_id=request_id,
                task_id=item.task_id,
                event_type=event_type,
                payload={
                    "temporary_agent_id": item.record.id,
                    "cleanup_reason": cleanup_reason,
                    "cleaned_up_at": cleaned_up_at,
                },
            )

    def _extract_stage_outputs(self, result: Any) -> list[str]:
        outputs = result.get_outputs()
        stage_texts: list[str] = []
        for output in outputs:
            text = self._clean_group_chat_output(self._flatten_output_text(output))
            if text:
                stage_texts.append(text)
        if stage_texts:
            return stage_texts
        timeline = getattr(result, "status_timeline", None)
        if callable(timeline):
            timeline_events = timeline()
            if isinstance(timeline_events, list):
                for event in timeline_events:
                    value = getattr(event, "value", None)
                    text = self._clean_group_chat_output(self._flatten_output_text(value))
                    if text:
                        stage_texts.append(text)
        return stage_texts

    def _clean_group_chat_output(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=re.DOTALL)
        cleaned = cleaned.replace("The group chat has reached the maximum number of rounds.", "")
        speaker_rules_marker = "- No one should ask clarifying questions in this workflow."
        if speaker_rules_marker in cleaned:
            cleaned = cleaned.split(speaker_rules_marker, 1)[1].strip()
        orchestration_markers = [
            "Parent request:",
            "Primary worker prompt:",
            "Local orchestration goal:",
            "Temporary helper roster:",
            "Speaker rules:",
        ]
        lines = cleaned.splitlines()
        filtered_lines: list[str] = []
        skip_bullet_rules = False
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(marker) for marker in orchestration_markers):
                skip_bullet_rules = stripped.startswith("Speaker rules:")
                continue
            if skip_bullet_rules and stripped.startswith("-"):
                continue
            skip_bullet_rules = False
            filtered_lines.append(line)
        cleaned = "\n".join(filtered_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _flatten_output_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Message):
            return (value.text or "").strip()
        if isinstance(value, (list, tuple)):
            parts = [self._flatten_output_text(item) for item in value]
            cleaned = [part for part in parts if part]
            return "\n\n".join(cleaned).strip()
        text = getattr(value, "text", None)
        if isinstance(text, str):
            return text.strip()
        if isinstance(text, (list, tuple)):
            return self._flatten_output_text(text)
        return str(value).strip()


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None
