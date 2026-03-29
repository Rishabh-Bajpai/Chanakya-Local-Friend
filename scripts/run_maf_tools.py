import asyncio
import sys
import os

from chanakya.config import load_local_env
from chanakya.db import build_engine, build_session_factory
from chanakya.agent.runtime import MAFRuntime
from chanakya.store import ChanakyaStore
from chanakya.services.tool_loader import initialize_all_tools

def test_maf_tools():
    print("STARTING SCRIPT", flush=True)
    load_local_env()
    initialize_all_tools()
    
    engine = build_engine("sqlite:///:memory:")
    from chanakya.model import Base
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    store = ChanakyaStore(session_factory)

    from chanakya.model import AgentProfileModel
    profile = AgentProfileModel(
        id="test_agent",
        name="Test",
        role="tester",
        system_prompt="Multiply 5 by 2 using your tools",
        tool_ids_json=["mcp_calculator"],
    )

    runtime = MAFRuntime(profile, session_factory)
    print("Testing connection phase...")
    result = runtime.run("test_sess", "Multiply 5 by 2", request_id="test_req")
    print("\nResult Text:", result.text)
    print("\nAvailability:", result.availability)
    print("\nTool Traces:", result.tool_traces)


if __name__ == "__main__":
    test_maf_tools()
