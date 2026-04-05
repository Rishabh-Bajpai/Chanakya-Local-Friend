from __future__ import annotations

import re
from pathlib import Path

from chanakya.config import get_data_dir

_WORK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def get_shared_workspace_root() -> Path:
    root = get_data_dir() / "shared_workspace"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o775)
    return root


def normalize_work_id(work_id: str | None) -> str:
    value = "" if work_id is None else work_id.strip()
    if not value:
        return "temp"
    if value in {".", ".."}:
        raise ValueError("Invalid work_id for sandbox workspace")
    if "/" in value or "\\" in value:
        raise ValueError("work_id must not contain path separators")
    if not _WORK_ID_PATTERN.fullmatch(value):
        raise ValueError("work_id contains unsupported characters")
    return value


def resolve_shared_workspace(work_id: str | None) -> Path:
    root = get_shared_workspace_root().resolve()
    safe_work_id = normalize_work_id(work_id)
    target = (root / safe_work_id).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("Resolved sandbox workspace escapes shared root")
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o775)
    return target
