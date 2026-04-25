import json
from pathlib import Path

import pytest

from chanakya.services import mcp_basic_tools_server as server
from chanakya.services import sandbox_workspace


def test_filesystem_tools_write_and_read_from_shared_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    result = server._write_text_file("notes/output.txt", "hello", work_id="work_123")

    workspace = sandbox_workspace.resolve_shared_workspace("work_123")
    assert result["ok"] is True
    assert result["work_id"] == "work_123"
    assert result["path"] == "notes/output.txt"
    assert Path(str(result["workspace_root"])) == workspace
    assert (workspace / "notes" / "output.txt").read_text(encoding="utf-8") == "hello"

    read_back = server._read_text_file("notes/output.txt", work_id="work_123")
    assert read_back["content"] == "hello"
    assert read_back["work_id"] == "work_123"


def test_filesystem_tools_are_scoped_by_work_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    server._write_text_file("shared.txt", "alpha", work_id="work_a")
    server._write_text_file("shared.txt", "beta", work_id="work_b")

    assert server._read_text_file("shared.txt", work_id="work_a")["content"] == "alpha"
    assert server._read_text_file("shared.txt", work_id="work_b")["content"] == "beta"


def test_list_directory_uses_shared_workspace_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    workspace = sandbox_workspace.resolve_shared_workspace("work_list")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "main.py").write_text("print('hi')", encoding="utf-8")

    result = server._list_directory("src", work_id="work_list")

    assert result["work_id"] == "work_list"
    assert result["path"] == "src"
    assert result["entries"] == [
        {
            "name": "main.py",
            "path": "src/main.py",
            "is_dir": False,
            "size": 11,
        }
    ]


def test_filesystem_tools_reject_unknown_classic_workspaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)

    with pytest.raises(FileNotFoundError):
        server._write_text_file("notes.txt", "hello", work_id="cwork_missing")


def test_filesystem_error_helper_lists_available_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox_workspace, "get_data_dir", lambda: tmp_path)
    workspace = sandbox_workspace.resolve_shared_workspace("work_list")
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "main.py").write_text("print('hi')", encoding="utf-8")

    result = server._filesystem_error_payload(
        error=FileNotFoundError("Path not found: missing.txt"),
        path="missing.txt",
        work_id="work_list",
    )

    assert result["ok"] is False
    assert result["available_entries"][0]["name"] == "src"
    assert "Retry with a valid path" in result["hint"]


def test_http_get_json_failure_trims_with_map_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = "x" * (server.MAX_MAP_BODY_CHARS + 50)

    def _fake_http_request(**_: object) -> dict[str, object]:
        return {"ok": True, "body": oversized}

    monkeypatch.setattr(server, "_http_request", _fake_http_request)

    result = server._http_get_json("https://example.com/test")

    assert result["ok"] is False
    assert len(str(result["raw"])) <= server.MAX_MAP_BODY_CHARS + len("\n...[truncated]")


def test_nominatim_headers_allow_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOMINATIM_USER_AGENT", "Custom-Agent/1.0")

    headers = server._nominatim_headers()

    assert headers["User-Agent"] == "Custom-Agent/1.0"
    assert headers["Accept"] == "application/json"


def test_geocode_place_normalizes_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get_json(url: str, **_: object) -> dict[str, object]:
        assert "nominatim.openstreetmap.org/search" in url
        return {
            "ok": True,
            "payload": [
                {
                    "display_name": "Berlin, Germany",
                    "lat": "52.5173890",
                    "lon": "13.3951309",
                    "class": "boundary",
                    "type": "administrative",
                    "importance": 0.84,
                }
            ],
            "raw": "{}",
        }

    monkeypatch.setattr(server, "_http_get_json", _fake_http_get_json)

    result = server._geocode_place("Berlin", limit=1)

    assert result["ok"] is True
    assert result["places"] == [
        {
            "display_name": "Berlin, Germany",
            "latitude": "52.5173890",
            "longitude": "13.3951309",
            "class": "boundary",
            "type": "administrative",
            "importance": 0.84,
        }
    ]


def test_reverse_geocode_lookup_returns_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get_json(url: str, **_: object) -> dict[str, object]:
        assert "nominatim.openstreetmap.org/reverse" in url
        return {
            "ok": True,
            "payload": {
                "display_name": "Brandenburg Gate, Berlin, Germany",
                "lat": "52.516275",
                "lon": "13.377704",
                "address": {"city": "Berlin", "country": "Germany"},
                "osm_type": "way",
                "osm_id": 123,
            },
            "raw": "{}",
        }

    monkeypatch.setattr(server, "_http_get_json", _fake_http_get_json)

    result = server._reverse_geocode_lookup(52.516275, 13.377704)

    assert result == {
        "ok": True,
        "result": {
            "display_name": "Brandenburg Gate, Berlin, Germany",
            "latitude": "52.516275",
            "longitude": "13.377704",
            "address": {"city": "Berlin", "country": "Germany"},
            "osm_type": "way",
            "osm_id": 123,
        },
    }


def test_route_places_combines_nominatim_and_osrm(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http_get_json(url: str, **_: object) -> dict[str, object]:
        if "nominatim.openstreetmap.org/search" in url and "Times+Square" in url:
            return {
                "ok": True,
                "payload": [{"display_name": "Times Square", "lat": "40.7580", "lon": "-73.9855"}],
                "raw": "{}",
            }
        if "nominatim.openstreetmap.org/search" in url and "Central+Park" in url:
            return {
                "ok": True,
                "payload": [{"display_name": "Central Park", "lat": "40.7829", "lon": "-73.9654"}],
                "raw": "{}",
            }
        if "router.project-osrm.org/route/v1/walking/" in url:
            return {
                "ok": True,
                "payload": {"routes": [{"distance": 3200.0, "duration": 2400.0}]},
                "raw": "{}",
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(server, "_http_get_json", _fake_http_get_json)

    result = server._route_places("Times Square", "Central Park", profile="walking")

    assert result["ok"] is True
    assert result["profile"] == "walking"
    assert result["origin"]["display_name"] == "Times Square"
    assert result["destination"]["display_name"] == "Central Park"
    assert result["route"] == {
        "distance_meters": 3200.0,
        "distance_km": 3.2,
        "duration_seconds": 2400.0,
        "duration_minutes": 40.0,
    }


def test_seed_agents_include_map_and_timer_tools() -> None:
    seed_path = Path(__file__).resolve().parents[1] / "seeds" / "agents.json"
    agents = json.loads(seed_path.read_text(encoding="utf-8"))
    tool_ids_by_agent = {item["id"]: set(item["tool_ids"]) for item in agents}

    assert "mcp_map" in tool_ids_by_agent["agent_chanakya"]
    assert "mcp_timer" in tool_ids_by_agent["agent_chanakya"]
    assert "mcp_map" not in tool_ids_by_agent["agent_informer"]
    assert "mcp_map" not in tool_ids_by_agent["agent_researcher"]
    assert "mcp_timer" not in tool_ids_by_agent["agent_informer"]
    assert "mcp_timer" not in tool_ids_by_agent["agent_researcher"]


def test_run_git_uses_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _CompletedProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "ok\n"
            self.stderr = ""

    def _fake_run(command: list[str], **kwargs: object) -> _CompletedProcess:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return _CompletedProcess()

    monkeypatch.setattr(server.subprocess, "run", _fake_run)

    result = server._run_git(["status", "--short", "--branch"])

    assert captured == {
        "command": ["git", "status", "--short", "--branch"],
        "cwd": server.REPO_ROOT,
    }
    assert result["ok"] is True
    assert result["repo_root"] == str(server.REPO_ROOT)
