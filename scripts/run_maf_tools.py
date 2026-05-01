from __future__ import annotations

from chanakya.agent.runtime import MAFRuntime
from chanakya.config import load_local_env
from chanakya.db import build_engine, build_session_factory
from chanakya.model import AgentProfileModel, Base
from chanakya.services.tool_loader import initialize_all_tools


def test_maf_tools() -> None:
    print("Starting MAF tool smoke test...", flush=True)
    load_local_env()
    initialize_all_tools()

    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    profile = AgentProfileModel.from_seed(
        {
            "id": "agent_smoke_tester",
            "name": "Smoke Tester",
            "role": "tester",
            "system_prompt": "Use available tools to answer user requests clearly.",
            "personality": "concise",
            "tool_ids": ["mcp_calculator"],
            "workspace": None,
            "heartbeat_enabled": False,
            "heartbeat_interval_seconds": 300,
            "heartbeat_file_path": None,
            "is_active": True,
        }
    )

    runtime = MAFRuntime(profile, session_factory)
    result = runtime.run("session_smoke", "Multiply 5 by 2", request_id="req_smoke")
    print("\nResult text:", result.text)
    print("\nTool availability:", result.availability)
    print("\nTool traces:", result.tool_traces)


if __name__ == "__main__":
    test_maf_tools()
