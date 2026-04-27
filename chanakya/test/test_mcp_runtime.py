from __future__ import annotations

from types import SimpleNamespace

from chanakya.mcp_runtime import extract_tool_execution_traces, normalize_tool_spec_summary


class _ToolWithoutId:
    def __init__(self, name: str, server_name: str) -> None:
        self.name = name
        self.server_name = server_name


def test_extract_tool_execution_traces_tolerates_specs_without_id() -> None:
    specs = [_ToolWithoutId(name="mcp_filesystem", server_name="filesystem_server")]
    response = SimpleNamespace(
        messages=[
            SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call_1",
                        name="mcp_filesystem_write_text_file",
                        arguments={"path": "/workspace/app.py"},
                    ),
                    SimpleNamespace(
                        type="function_result",
                        call_id="call_1",
                        result={"ok": True},
                        exception=None,
                    ),
                ]
            )
        ]
    )

    traces = extract_tool_execution_traces(response, specs)

    assert len(traces) == 1
    assert traces[0].tool_id == "mcp_filesystem"
    assert traces[0].tool_name == "mcp_filesystem"
    assert traces[0].server_name == "filesystem_server"
    assert traces[0].status == "succeeded"


def test_extract_tool_execution_traces_falls_back_to_unknown_tool_when_unmapped() -> None:
    response = SimpleNamespace(
        messages=[
            SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call_2",
                        name="some_tool_run",
                        arguments={"value": 1},
                    ),
                    SimpleNamespace(
                        type="function_result",
                        call_id="call_2",
                        result=None,
                        exception="boom",
                    ),
                ]
            )
        ]
    )

    traces = extract_tool_execution_traces(response, [])

    assert len(traces) == 1
    assert traces[0].tool_id == "unknown_tool"
    assert traces[0].tool_name == "some_tool_run"
    assert traces[0].server_name == "unknown_server"
    assert traces[0].status == "failed"


def test_normalize_tool_spec_summary_tolerates_sparse_tool_object() -> None:
    spec = SimpleNamespace(name="mcp_fetch")

    summary = normalize_tool_spec_summary(spec)

    assert summary == {
        "tool_id": "mcp_fetch",
        "tool_name": "mcp_fetch",
        "server_name": "unknown_server",
    }
