from __future__ import annotations

import re
import shutil
from pathlib import Path

from chanakya.config import get_data_dir
from chanakya.debug import debug_log

_WORK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CLASSIC_ARTIFACT_WORKSPACE_ID = "artifacts"


def get_shared_workspace_root() -> Path:
    root = get_data_dir() / "shared_workspace"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o775)
    return root


def get_artifact_storage_root(*, create: bool = True) -> Path:
    root = (get_shared_workspace_root() / CLASSIC_ARTIFACT_WORKSPACE_ID).resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o775)
    return root


def normalize_work_id(work_id: str | None) -> str:
    value = "" if work_id is None else work_id.strip()
    if not value:
        return CLASSIC_ARTIFACT_WORKSPACE_ID
    if value in {".", ".."}:
        raise ValueError("Invalid work_id for sandbox workspace")
    if "/" in value or "\\" in value:
        raise ValueError("work_id must not contain path separators")
    if not _WORK_ID_PATTERN.fullmatch(value):
        raise ValueError("work_id contains unsupported characters")
    return value


def resolve_shared_workspace(
    work_id: str | None,
    *,
    create: bool = True,
    allow_create_missing_classic: bool = True,
) -> Path:
    root = get_shared_workspace_root().resolve()
    safe_work_id = normalize_work_id(work_id)
    target = (root / safe_work_id).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("Resolved sandbox workspace escapes shared root")
    if create:
        if (
            safe_work_id.startswith("cwork_")
            and not target.exists()
            and not allow_create_missing_classic
        ):
            raise FileNotFoundError(
                "Unknown classic work sandbox. Refusing to create a new cwork_* workspace from tool input."
            )
        created = not target.exists()
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o775)
        if created:
            debug_log(
                "sandbox_workspace_created",
                {
                    "work_id": safe_work_id,
                    "path": str(target),
                },
            )
    return target


def delete_shared_workspace(work_id: str | None) -> dict[str, str | bool]:
    root = get_shared_workspace_root().resolve()
    safe_work_id = normalize_work_id(work_id)
    target = (root / safe_work_id).resolve()
    if target == root or root not in target.parents:
        raise PermissionError("Resolved sandbox workspace escapes shared root")
    if not target.exists():
        return {"ok": True, "work_id": safe_work_id, "path": str(target), "deleted": False}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        debug_log(
            "sandbox_workspace_delete_failed",
            {
                "work_id": safe_work_id,
                "path": str(target),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return {
            "ok": False,
            "work_id": safe_work_id,
            "path": str(target),
            "error": str(exc),
        }
    return {"ok": True, "work_id": safe_work_id, "path": str(target), "deleted": True}
