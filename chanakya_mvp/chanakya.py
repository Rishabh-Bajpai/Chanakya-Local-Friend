from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chanakya_mvp.config import get_openai_compatible_config, load_local_env
from chanakya_mvp.logging_utils import JsonlLogger
from chanakya_mvp.maf_runtime import MafAgentRuntime
from chanakya_mvp.manager import AgentManager
from chanakya_mvp.models import RequestEnvelope, Route, make_id
from chanakya_mvp.tools import WeatherTool


@dataclass(slots=True)
class ChanakyaReply:
    request_id: str
    route: Route
    message: str
    delegated_task_id: str | None = None
    waiting_input: bool = False


class ChanakyaPA:
    def __init__(self, manager: AgentManager, logger: JsonlLogger) -> None:
        load_local_env()
        self.manager = manager
        self.logger = logger
        self.weather_tool = WeatherTool()
        self.openai_cfg = get_openai_compatible_config()
        self.maf_runtime = MafAgentRuntime.from_env()
        self.waiting_map: dict[str, str] = {}

    def handle_message(self, text: str, context: dict[str, Any] | None = None) -> ChanakyaReply:
        ctx = context.copy() if context else {}
        route = self._route_request(text)
        request_id = make_id("req")

        envelope = RequestEnvelope(request_id=request_id, text=text, route=route, context=ctx)
        self.logger.log(
            "route_decision",
            {
                "request_id": envelope.request_id,
                "route": envelope.route.value,
                "text": envelope.text,
            },
        )

        if route == Route.DIRECT:
            msg = self._direct_response(text)
            self.logger.log("request_handled_direct", {"request_id": request_id, "status": "ok"})
            return ChanakyaReply(request_id=request_id, route=route, message=msg)

        if route == Route.TOOL:
            location = self._extract_location(text)
            if self.maf_runtime is not None:
                maf_weather = self.maf_runtime.run_weather(text, location)
                if maf_weather:
                    self.logger.log(
                        "tool_invocation",
                        {
                            "request_id": request_id,
                            "tool": "maf_weather_tool",
                            "status": "ok",
                            "location": location,
                            "summary": maf_weather,
                            "runtime": "maf_agent",
                        },
                    )
                    return ChanakyaReply(request_id=request_id, route=route, message=maf_weather)
            result = self.weather_tool.run(location)
            self.logger.log(
                "tool_invocation",
                {
                    "request_id": request_id,
                    "tool": "weather",
                    "status": result.status,
                    "location": location,
                    "summary": result.summary,
                },
            )
            return ChanakyaReply(request_id=request_id, route=route, message=result.summary)

        manager_response = self.manager.create_and_run_workflow(text, ctx)
        self.logger.log(
            "delegation_ack",
            {
                "request_id": request_id,
                "task_id": manager_response.parent_task_id,
                "status": manager_response.status.value,
            },
        )
        if manager_response.waiting_input_prompt:
            self.waiting_map[request_id] = manager_response.parent_task_id
            prompt = f"{manager_response.user_message} {manager_response.waiting_input_prompt}"
            return ChanakyaReply(
                request_id=request_id,
                route=route,
                message=prompt,
                delegated_task_id=manager_response.parent_task_id,
                waiting_input=True,
            )
        return ChanakyaReply(
            request_id=request_id,
            route=route,
            message=manager_response.user_message,
            delegated_task_id=manager_response.parent_task_id,
        )

    def submit_followup(self, originating_request_id: str, user_input: str) -> ChanakyaReply:
        parent_task_id = self.waiting_map.get(originating_request_id)
        if not parent_task_id:
            return ChanakyaReply(
                request_id=originating_request_id,
                route=Route.MANAGER,
                message="No pending task is waiting for input on this request.",
            )
        resumed = self.manager.resume_waiting_task(parent_task_id, user_input)
        self.logger.log(
            "followup_linked",
            {
                "originating_request_id": originating_request_id,
                "task_id": parent_task_id,
                "status": resumed.status.value,
            },
        )
        if resumed.status.value in {"done", "failed", "blocked"}:
            self.waiting_map.pop(originating_request_id, None)
        return ChanakyaReply(
            request_id=originating_request_id,
            route=Route.MANAGER,
            message=resumed.user_message,
            delegated_task_id=parent_task_id,
        )

    def _route_request(self, text: str) -> Route:
        t = text.lower()
        if "weather" in t or "temperature" in t:
            return Route.TOOL
        if any(k in t for k in ["build", "implement", "feature", "develop", "test"]):
            return Route.MANAGER
        return Route.DIRECT

    def _direct_response(self, text: str) -> str:
        if self.maf_runtime is not None:
            maf_reply = self.maf_runtime.run_direct(text)
            if maf_reply:
                return maf_reply

        model = self.openai_cfg.get("model")
        endpoint = self.openai_cfg.get("base_url")
        if model and endpoint:
            return (
                "Direct response path succeeded. "
                f"OpenAI-compatible endpoint detected for model `{model}` at `{endpoint}`."
            )
        return f"Direct response path succeeded for: {text.strip()}"

    @staticmethod
    def _extract_location(text: str) -> str | None:
        lower = text.lower()
        if " in " not in lower:
            return None
        location = text[lower.rfind(" in ") + 4 :].strip(" ?!.")
        return location or None
