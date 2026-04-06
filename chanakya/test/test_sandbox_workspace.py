from __future__ import annotations

from pathlib import Path

import pytest

import chanakya.services.sandbox_workspace as sandbox_workspace


def test_resolve_shared_workspace_uses_temp_for_empty_work_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    workspace = sandbox_workspace.resolve_shared_workspace(None)

    assert workspace == (tmp_path / "shared_workspace" / "temp")
    assert workspace.exists()
    assert oct(workspace.stat().st_mode & 0o777) == "0o775"


@pytest.mark.parametrize("work_id", ["../escape", "..", "bad/value", "bad\\value"])
def test_normalize_work_id_rejects_invalid_values(work_id: str) -> None:
    with pytest.raises(ValueError):
        sandbox_workspace.normalize_work_id(work_id)
