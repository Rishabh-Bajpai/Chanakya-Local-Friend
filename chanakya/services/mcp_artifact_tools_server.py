"""MCP server exposing first-class artifact management tools.

Artifacts are simple single-file deliverables that classic chat can create and
update explicitly without routing through work management.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from chanakya.config import get_database_url
from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.domain import make_id
from chanakya.services.mcp_feedback import (
    build_missing_argument_payload,
    build_wrong_id_payload,
)
from chanakya.store import ChanakyaStore
from chanakya.services.sandbox_workspace import get_artifact_storage_root

_FILENAME_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def _build_store() -> tuple[ChanakyaStore, sessionmaker[Session]]:
    engine = build_engine(get_database_url())
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory), session_factory


def _artifact_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "download_url": f"/api/artifacts/{record['id']}/download",
        "detail_url": f"/api/artifacts/{record['id']}",
    }


def _normalize_optional(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _sanitize_filename(name: str) -> str:
    cleaned = _FILENAME_SANITIZE_PATTERN.sub("_", name.strip()).strip("._")
    return cleaned or "artifact.txt"


def _artifact_title(name: str, title: str | None) -> str:
    cleaned = _normalize_optional(title)
    if cleaned:
        return cleaned
    stem = Path(name).stem.strip()
    return stem or name


def _artifact_relative_path(artifact_id: str, filename: str) -> Path:
    return Path(artifact_id) / filename


def _write_artifact_file(
    *,
    workspace: Path,
    artifact_id: str,
    filename: str,
    content: str,
) -> tuple[str, int]:
    relative_path = _artifact_relative_path(artifact_id, filename)
    file_path = workspace / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return relative_path.as_posix(), int(file_path.stat().st_size)


def _create_artifact(
    store: ChanakyaStore,
    *,
    session_id: str,
    request_id: str,
    name: str,
    content: str,
    kind: str = "text",
    mime_type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    work_id: str | None = None,
    source_agent_id: str | None = None,
    source_agent_name: str | None = None,
) -> dict[str, Any]:
    if not _normalize_optional(session_id):
        return build_missing_argument_payload(
            argument="session_id",
            hint="Retry with the active classic chat or work session_id.",
        )
    if not _normalize_optional(request_id):
        return build_missing_argument_payload(
            argument="request_id",
            hint="Retry with the current request_id from the active turn.",
        )
    if not name.strip():
        return build_missing_argument_payload(
            argument="name",
            hint="Retry with a concrete filename such as notes.md, script.py, or diagram.svg.",
        )
    artifact_id = make_id("artifact")
    filename = _sanitize_filename(name)
    workspace = get_artifact_storage_root(create=True)
    relative_path, size_bytes = _write_artifact_file(
        workspace=workspace,
        artifact_id=artifact_id,
        filename=filename,
        content=content,
    )
    guessed_mime_type, _ = mimetypes.guess_type(filename)
    record = store.create_artifact(
        artifact_id=artifact_id,
        request_id=request_id,
        session_id=session_id,
        work_id=_normalize_optional(work_id),
        name=filename,
        title=_artifact_title(filename, title),
        summary=summary,
        path=relative_path,
        mime_type=_normalize_optional(mime_type) or guessed_mime_type,
        kind=_normalize_optional(kind) or "text",
        size_bytes=size_bytes,
        source_agent_id=_normalize_optional(source_agent_id),
        source_agent_name=_normalize_optional(source_agent_name),
        latest_request_id=request_id,
    )
    return {"ok": True, "artifact": _artifact_payload(record)}


def _update_artifact(
    store: ChanakyaStore,
    *,
    artifact_id: str,
    content: str,
    request_id: str | None = None,
    session_id: str | None = None,
    work_id: str | None = None,
    name: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    kind: str | None = None,
    mime_type: str | None = None,
    source_agent_id: str | None = None,
    source_agent_name: str | None = None,
) -> dict[str, Any]:
    try:
        artifact = store.get_artifact(artifact_id)
    except KeyError:
        return _artifact_not_found_payload(
            store,
            artifact_id=artifact_id,
            session_id=session_id,
            request_id=request_id,
            work_id=work_id,
        )

    normalized_request_id = _normalize_optional(request_id) or artifact.latest_request_id or artifact.request_id
    normalized_session_id = _normalize_optional(session_id) or artifact.session_id
    normalized_work_id = _normalize_optional(work_id) if work_id is not None else artifact.work_id
    filename = _sanitize_filename(name or artifact.name)
    workspace = get_artifact_storage_root(create=True)
    old_path = workspace / artifact.path
    relative_path, size_bytes = _write_artifact_file(
        workspace=workspace,
        artifact_id=artifact.id,
        filename=filename,
        content=content,
    )
    new_path = workspace / relative_path
    if old_path != new_path and old_path.exists():
        old_path.unlink()

    guessed_mime_type, _ = mimetypes.guess_type(filename)
    record = store.update_artifact(
        artifact_id,
        session_id=normalized_session_id,
        work_id=normalized_work_id,
        name=filename,
        title=_artifact_title(filename, title or artifact.title),
        summary=summary if summary is not None else artifact.summary,
        path=relative_path,
        mime_type=_normalize_optional(mime_type) or guessed_mime_type or artifact.mime_type,
        kind=_normalize_optional(kind) or artifact.kind,
        size_bytes=size_bytes,
        source_agent_id=_normalize_optional(source_agent_id) or artifact.source_agent_id,
        source_agent_name=_normalize_optional(source_agent_name) or artifact.source_agent_name,
        latest_request_id=normalized_request_id,
    )
    return {"ok": True, "artifact": _artifact_payload(record)}


def _artifact_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "title": record.get("title"),
        "kind": record.get("kind"),
        "download_url": f"/api/artifacts/{record['id']}/download",
        "detail_url": f"/api/artifacts/{record['id']}",
        "path": record.get("path"),
        "work_id": record.get("work_id"),
        "request_id": record.get("request_id"),
        "latest_request_id": record.get("latest_request_id"),
    }


def _artifact_candidates(
    store: ChanakyaStore,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    work_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if _normalize_optional(work_id):
        records = store.list_artifacts_for_work(str(work_id).strip())
    elif _normalize_optional(session_id):
        records = store.list_artifacts_for_session(str(session_id).strip())
    elif _normalize_optional(request_id):
        records = store.list_artifacts_for_request(str(request_id).strip())
    else:
        records = store.list_recent_artifacts(limit=limit)
    return [_artifact_summary(item) for item in records[:limit]]


def _artifact_not_found_payload(
    store: ChanakyaStore,
    *,
    artifact_id: str,
    session_id: str | None = None,
    request_id: str | None = None,
    work_id: str | None = None,
) -> dict[str, Any]:
    candidates = _artifact_candidates(
        store,
        session_id=session_id,
        request_id=request_id,
        work_id=work_id,
    )
    return build_wrong_id_payload(
        object_name="artifact",
        bad_id=artifact_id,
        candidates_key="available_artifacts",
        candidates=candidates,
        retry_hint="Call list_artifacts to inspect valid artifact IDs, then retry with one of those IDs.",
        empty_scope_message="No matching artifacts were found in the current scope.",
    )


def _delete_artifact(
    store: ChanakyaStore,
    *,
    artifact_id: str,
    session_id: str | None = None,
    request_id: str | None = None,
    work_id: str | None = None,
) -> dict[str, Any]:
    try:
        artifact = store.get_artifact(artifact_id)
    except KeyError:
        return _artifact_not_found_payload(
            store,
            artifact_id=artifact_id,
            session_id=session_id,
            request_id=request_id,
            work_id=work_id,
        )
    artifact_root = get_artifact_storage_root(create=False)
    artifact_dir = artifact_root / artifact.id
    if artifact_dir.exists():
        for path in sorted(artifact_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        artifact_dir.rmdir()
    store.delete_artifact(artifact.id)
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "message": f"Deleted artifact {artifact.id} ({artifact.name}).",
    }


def _locate_artifact(
    store: ChanakyaStore,
    *,
    artifact_id: str,
    session_id: str | None = None,
    request_id: str | None = None,
    work_id: str | None = None,
) -> dict[str, Any]:
    try:
        artifact = store.get_artifact(artifact_id)
    except KeyError:
        return _artifact_not_found_payload(
            store,
            artifact_id=artifact_id,
            session_id=session_id,
            request_id=request_id,
            work_id=work_id,
        )
    artifact_root = get_artifact_storage_root(create=False)
    absolute_path = (artifact_root / artifact.path).resolve()
    return {
        "ok": True,
        "artifact": _artifact_payload(store.artifacts._to_dict(artifact)),
        "storage_root": str(artifact_root),
        "absolute_path": str(absolute_path),
    }


def _build_artifact_tools_server() -> FastMCP:
    mcp = FastMCP("Chanakya Artifact Tools", json_response=True)
    store, _session_factory = _build_store()

    @mcp.tool()
    def create_artifact(
        session_id: str,
        request_id: str,
        name: str,
        content: str,
        kind: str = "text",
        mime_type: str = "",
        title: str = "",
        summary: str = "",
        work_id: str = "",
        source_agent_id: str = "",
        source_agent_name: str = "",
    ) -> dict[str, Any]:
        """Create a first-class artifact and save its file content.

        Use this for user-facing single-file deliverables such as code, notes,
        markdown documents, SVG files, and similar saved outputs.
        """

        return _create_artifact(
            store,
            session_id=session_id,
            request_id=request_id,
            name=name,
            content=content,
            kind=kind,
            mime_type=mime_type,
            title=title,
            summary=summary,
            work_id=work_id,
            source_agent_id=source_agent_id,
            source_agent_name=source_agent_name,
        )

    @mcp.tool()
    def update_artifact(
        artifact_id: str,
        content: str,
        request_id: str = "",
        session_id: str = "",
        work_id: str = "",
        name: str = "",
        title: str = "",
        summary: str = "",
        kind: str = "",
        mime_type: str = "",
        source_agent_id: str = "",
        source_agent_name: str = "",
    ) -> dict[str, Any]:
        """Update an existing first-class artifact and replace its saved content."""

        return _update_artifact(
            store,
            artifact_id=artifact_id,
            content=content,
            request_id=request_id,
            session_id=session_id,
            work_id=work_id,
            name=name,
            title=title,
            summary=summary,
            kind=kind,
            mime_type=mime_type,
            source_agent_id=source_agent_id,
            source_agent_name=source_agent_name,
        )

    @mcp.tool()
    def list_artifacts(session_id: str = "", work_id: str = "", request_id: str = "") -> dict[str, Any]:
        """List artifacts for a session, work item, or request."""

        if work_id.strip():
            artifacts = store.list_artifacts_for_work(work_id.strip())
        elif session_id.strip():
            artifacts = store.list_artifacts_for_session(session_id.strip())
        elif request_id.strip():
            artifacts = store.list_artifacts_for_request(request_id.strip())
        else:
            return {"ok": False, "error": "session_id, work_id, or request_id is required"}
        return {
            "ok": True,
            "artifacts": [_artifact_payload(item) for item in artifacts],
            "count": len(artifacts),
        }

    @mcp.tool()
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        """Return a single artifact record."""

        try:
            artifact = store.get_artifact(artifact_id)
        except KeyError:
            return _artifact_not_found_payload(store, artifact_id=artifact_id)
        return {
            "ok": True,
            "artifact": _artifact_payload(store.artifacts._to_dict(artifact)),
        }

    @mcp.tool()
    def read_artifact_text(artifact_id: str) -> dict[str, Any]:
        """Read an artifact's text content when it is stored as a text file."""

        try:
            artifact = store.get_artifact(artifact_id)
        except KeyError:
            return _artifact_not_found_payload(store, artifact_id=artifact_id)
        artifact_root = get_artifact_storage_root(create=False)
        file_path = artifact_root / artifact.path
        if not file_path.exists():
            return {"ok": False, "error": f"Artifact file missing: {artifact.path}"}
        return {
            "ok": True,
            "artifact": _artifact_payload(store.artifacts._to_dict(artifact)),
            "content": file_path.read_text(encoding="utf-8"),
        }

    @mcp.tool()
    def locate_artifact(
        artifact_id: str,
        session_id: str = "",
        request_id: str = "",
        work_id: str = "",
    ) -> dict[str, Any]:
        """Get the download link and file location for an artifact."""

        return _locate_artifact(
            store,
            artifact_id=artifact_id,
            session_id=session_id,
            request_id=request_id,
            work_id=work_id,
        )

    @mcp.tool()
    def delete_artifact(
        artifact_id: str,
        session_id: str = "",
        request_id: str = "",
        work_id: str = "",
    ) -> dict[str, Any]:
        """Delete an artifact record and its saved file."""

        return _delete_artifact(
            store,
            artifact_id=artifact_id,
            session_id=session_id,
            request_id=request_id,
            work_id=work_id,
        )

    return mcp


if __name__ == "__main__":
    _build_artifact_tools_server().run(transport="stdio")
