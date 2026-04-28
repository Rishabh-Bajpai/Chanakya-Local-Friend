from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolExecutionTrace:
    tool_id: str
    tool_name: str
    server_name: str
    status: str
    input_payload: str | None
    output_text: str | None
    error_text: str | None


def _tool_spec_id(spec: Any) -> str | None:
    raw = getattr(spec, "id", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raw_name = getattr(spec, "name", None)
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    return None


def _tool_spec_name(spec: Any, fallback: str = "unknown_tool") -> str:
    raw = getattr(spec, "name", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    spec_id = _tool_spec_id(spec)
    if spec_id:
        return spec_id
    return fallback


def _tool_spec_server_name(spec: Any) -> str:
    raw = getattr(spec, "server_name", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "unknown_server"


def normalize_tool_spec_summary(spec: Any) -> dict[str, Any]:
    tool_id = _tool_spec_id(spec)
    functions: list[dict[str, str]] = []
    for function in list(getattr(spec, "functions", []) or []):
        function_name = str(getattr(function, "name", "") or "").strip()
        if not function_name:
            continue
        functions.append(
            {
                "name": function_name,
                "description": str(getattr(function, "description", "") or "").strip(),
            }
        )
    return {
        "tool_id": tool_id,
        "tool_name": _tool_spec_name(spec, fallback=tool_id or "unknown_tool"),
        "server_name": _tool_spec_server_name(spec),
        "functions": functions,
        "function_count": len(functions),
        "description": " ".join(item["description"] for item in functions if item.get("description")).strip() or None,
    }


def _tool_id_from_function_name(function_name: str, known_specs: dict[str, Any]) -> str | None:
    for tool_id in known_specs:
        if function_name.startswith(f"{tool_id}_"):
            return tool_id
    return None


def _stringify_payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def extract_tool_execution_traces(response: Any, specs: list[Any]) -> list[ToolExecutionTrace]:
    known_specs: dict[str, Any] = {}
    for spec in specs:
        tool_id = _tool_spec_id(spec)
        if tool_id and tool_id not in known_specs:
            known_specs[tool_id] = spec
    calls_by_id: dict[str, dict[str, str | None]] = {}
    traces: list[ToolExecutionTrace] = []

    messages = getattr(response, "messages", [])
    for message in messages:
        contents = getattr(message, "contents", [])
        for content in contents:
            content_type = getattr(content, "type", None)
            if content_type == "function_call":
                call_id = getattr(content, "call_id", None)
                function_name = str(getattr(content, "name", ""))
                tool_id = _tool_id_from_function_name(function_name, known_specs)
                spec = known_specs.get(tool_id) if tool_id else None
                if call_id:
                    calls_by_id[str(call_id)] = {
                        "tool_id": _tool_spec_id(spec) if spec else (tool_id or "unknown_tool"),
                        "tool_name": _tool_spec_name(spec, fallback=function_name) if spec else function_name,
                        "server_name": _tool_spec_server_name(spec) if spec else "unknown_server",
                        "input_payload": _stringify_payload(getattr(content, "arguments", None)),
                    }
            if content_type == "function_result":
                call_id = str(getattr(content, "call_id", ""))
                prior = calls_by_id.get(call_id, {})
                traces.append(
                    ToolExecutionTrace(
                        tool_id=str(prior.get("tool_id", "unknown_tool")),
                        tool_name=str(prior.get("tool_name", "unknown_tool")),
                        server_name=str(prior.get("server_name", "unknown_server")),
                        status="failed" if getattr(content, "exception", None) else "succeeded",
                        input_payload=(
                            str(prior["input_payload"])
                            if prior.get("input_payload") is not None
                            else None
                        ),
                        output_text=_stringify_payload(getattr(content, "result", None)),
                        error_text=_stringify_payload(getattr(content, "exception", None)),
                    )
                )
    return traces
