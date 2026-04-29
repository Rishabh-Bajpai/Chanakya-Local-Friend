from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from chanakya.config import get_database_url
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.services.memory_manager_service import MemoryManagerService
from chanakya.store import ChanakyaStore


def _build_store() -> tuple[ChanakyaStore, sessionmaker[Session]]:
    engine = build_engine(get_database_url())
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory), session_factory


def _build_memory_agent_server() -> FastMCP:
    mcp = FastMCP("Chanakya Memory Agent", json_response=True)
    store, _session_factory = _build_store()
    service = MemoryManagerService(store)

    @mcp.tool()
    def memory_agent_request(memory_request: str) -> dict[str, Any]:
        """Handle long-term memory requests through the dedicated memory manager.

        Pass a single string. It may be plain text, or a JSON string with fields
        such as request, session_id, and request_id if extra context is needed.
        The memory manager decides whether to add, update, delete, recall, or ask
        for clarification.
        """

        return service.handle_memory_request(memory_request=memory_request)

    return mcp


def main() -> None:
    mcp = _build_memory_agent_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
