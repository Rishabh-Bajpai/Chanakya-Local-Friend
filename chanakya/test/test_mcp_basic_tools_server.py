import json
from pathlib import Path

import pytest

from chanakya.services import mcp_basic_tools_server as server


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
