from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from mcp.server.fastmcp import FastMCP
from chanakya.config import get_data_dir
from chanakya.services.mcp_feedback import build_recovery_payload
from chanakya.services.sandbox_workspace import resolve_shared_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_TEXT_CHARS = 20000
MAX_HTTP_BODY_CHARS = 50000
MAX_WEATHER_BODY_CHARS = 12000
MAX_MAP_BODY_CHARS = 12000
DEFAULT_USER_AGENT = "Chanakya-MAF-Demo/0.1 (+https://github.com/Rishabh-Bajpai/MAF-demo)"
SAFE_SHELL_COMMANDS = {
    "date": ["date"],
    "pwd": ["pwd"],
    "uname": ["uname", "-a"],
    "whoami": ["whoami"],
}


def get_classic_chat_workspace_root() -> Path:
    root = (get_data_dir() / "classic_chat_workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o775)
    return root


def _resolve_path(path: str) -> Path:
    raw = (path or ".").strip()
    workspace_root = get_classic_chat_workspace_root()
    candidate = (workspace_root / raw).resolve()
    candidate.relative_to(workspace_root)
    return candidate


def _resolve_filesystem_workspace(work_id: str = "temp") -> Path:
    return resolve_shared_workspace(work_id, allow_create_missing_classic=False).resolve()


def _resolve_filesystem_path(path: str, work_id: str = "temp") -> tuple[Path, Path]:
    raw = (path or ".").strip()
    workspace_root = _resolve_filesystem_workspace(work_id)
    candidate = (workspace_root / raw).resolve()
    candidate.relative_to(workspace_root)
    return workspace_root, candidate


def _filesystem_error_payload(
    *,
    error: Exception,
    path: str,
    work_id: str,
) -> dict[str, Any]:
    hint = "Retry with a valid path inside the shared workspace."
    workspace_root = ""
    entries: list[dict[str, Any]] = []
    try:
        root = _resolve_filesystem_workspace(work_id)
        workspace_root = str(root)
        if root.exists():
            entries = [
                {
                    "name": item.name,
                    "path": str(item.relative_to(root)),
                    "is_dir": item.is_dir(),
                }
                for item in sorted(root.iterdir(), key=lambda child: child.name.lower())[:10]
            ]
    except Exception:
        hint = (
            "Retry with a valid existing work_id or create the workspace through the appropriate tool first."
        )
    return build_recovery_payload(
        error=str(error),
        hint=hint,
        path=path,
        work_id=work_id,
        workspace_root=workspace_root,
        available_entries=entries,
    )


def _list_directory(path: str = ".", work_id: str = "temp") -> dict[str, Any]:
    workspace_root, resolved = _resolve_filesystem_path(path, work_id)
    if not resolved.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")
    entries = []
    for item in sorted(resolved.iterdir(), key=lambda child: child.name.lower()):
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(workspace_root)),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return {
        "path": str(resolved.relative_to(workspace_root)),
        "workspace_root": str(workspace_root),
        "work_id": work_id,
        "entries": entries,
    }


def _read_text_file(path: str, work_id: str = "temp", max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    workspace_root, resolved = _resolve_filesystem_path(path, work_id)
    if not resolved.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not resolved.is_file():
        raise IsADirectoryError(f"Path is not a file: {path}")
    content = resolved.read_text(encoding="utf-8")
    trimmed = _trim(content, max(1, min(max_chars, MAX_TEXT_CHARS)))
    return {
        "path": str(resolved.relative_to(workspace_root)),
        "workspace_root": str(workspace_root),
        "work_id": work_id,
        "content": trimmed,
    }


def _write_text_file(path: str, content: str, work_id: str = "temp") -> dict[str, Any]:
    workspace_root, resolved = _resolve_filesystem_path(path, work_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": str(resolved.relative_to(workspace_root)),
        "workspace_root": str(workspace_root),
        "work_id": work_id,
        "bytes_written": len(content.encode("utf-8")),
    }


def _trim(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _decode_json_text(text: str) -> Any:
    return json.loads(text)


def _lookup_json_path(payload: Any, path: str) -> Any:
    current = payload
    for part in [segment for segment in path.split(".") if segment]:
        if isinstance(current, list):
            current = current[int(part)]
            continue
        if isinstance(current, dict):
            current = current[part]
            continue
        raise KeyError(part)
    return current


def _run_git(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "output": _trim(output),
        "repo_root": str(REPO_ROOT),
    }


def _http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    encoded_body = body.encode("utf-8") if body is not None else None
    req = request.Request(url=url, data=encoded_body, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(str(key), str(value))
    try:
        with request.urlopen(req, timeout=max(1, timeout_seconds)) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": getattr(response, "status", None),
                "url": response.geturl(),
                "headers": dict(response.headers.items()),
                "body": _trim(raw_body, MAX_HTTP_BODY_CHARS),
            }
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "url": url,
            "headers": dict(exc.headers.items()),
            "body": _trim(raw_body, MAX_HTTP_BODY_CHARS),
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "headers": {},
            "body": "",
            "error": str(exc),
        }


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    result = _http_request(
        method="GET",
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    if not result.get("ok"):
        return result
    body = str(result.get("body") or "")
    try:
        return {"ok": True, "payload": json.loads(body), "raw": _trim(body, MAX_MAP_BODY_CHARS)}
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to decode JSON response: {exc}",
            "raw": _trim(body, MAX_MAP_BODY_CHARS),
        }


def _build_nominatim_url(endpoint: str, **params: object) -> str:
    query = parse.urlencode({key: value for key, value in params.items() if value is not None})
    return f"https://nominatim.openstreetmap.org/{endpoint}?{query}"


def _nominatim_headers() -> dict[str, str]:
    user_agent = os.getenv("NOMINATIM_USER_AGENT", DEFAULT_USER_AGENT)
    return {"User-Agent": user_agent, "Accept": "application/json"}


def _normalize_place(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": item.get("display_name"),
        "latitude": item.get("lat"),
        "longitude": item.get("lon"),
        "class": item.get("class"),
        "type": item.get("type"),
        "importance": item.get("importance"),
    }


def _geocode_place(query: str, *, limit: int = 1) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 10))
    url = _build_nominatim_url(
        "search",
        q=(query or "").strip(),
        format="jsonv2",
        addressdetails=1,
        limit=bounded_limit,
    )
    response = _http_get_json(url, headers=_nominatim_headers(), timeout_seconds=20)
    if not response.get("ok"):
        return response
    payload = response.get("payload")
    if not isinstance(payload, list):
        return {
            "ok": False,
            "error": "Unexpected Nominatim response",
            "raw": response.get("raw", ""),
        }
    places = [_normalize_place(item) for item in payload if isinstance(item, dict)]
    return {"ok": True, "places": places, "raw": response.get("raw", "")}


def _parse_coordinate(value: object, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if name == "latitude" and not (-90 <= numeric <= 90):
        raise ValueError("latitude must be between -90 and 90")
    if name == "longitude" and not (-180 <= numeric <= 180):
        raise ValueError("longitude must be between -180 and 180")
    return numeric


def _reverse_geocode_lookup(latitude: float, longitude: float) -> dict[str, Any]:
    lat = _parse_coordinate(latitude, "latitude")
    lon = _parse_coordinate(longitude, "longitude")
    url = _build_nominatim_url(
        "reverse",
        lat=f"{lat:.6f}",
        lon=f"{lon:.6f}",
        format="jsonv2",
        addressdetails=1,
    )
    response = _http_get_json(url, headers=_nominatim_headers(), timeout_seconds=20)
    if not response.get("ok"):
        return response
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "Unexpected Nominatim response",
            "raw": response.get("raw", ""),
        }
    address = payload.get("address") if isinstance(payload.get("address"), dict) else {}
    return {
        "ok": True,
        "result": {
            "display_name": payload.get("display_name"),
            "latitude": payload.get("lat"),
            "longitude": payload.get("lon"),
            "address": address,
            "osm_type": payload.get("osm_type"),
            "osm_id": payload.get("osm_id"),
        },
    }


def _route_places(origin: str, destination: str, profile: str = "driving") -> dict[str, Any]:
    normalized_profile = profile.strip().lower() or "driving"
    if normalized_profile not in {"driving", "walking", "cycling"}:
        raise ValueError("profile must be one of: driving, walking, cycling")
    origin_result = _geocode_place(origin, limit=1)
    if not origin_result.get("ok"):
        return {
            "ok": False,
            "error": f"origin lookup failed: {origin_result.get('error', 'unknown error')}",
        }
    destination_result = _geocode_place(destination, limit=1)
    if not destination_result.get("ok"):
        return {
            "ok": False,
            "error": f"destination lookup failed: {destination_result.get('error', 'unknown error')}",
        }
    origin_places = origin_result.get("places") or []
    destination_places = destination_result.get("places") or []
    if not origin_places:
        return {"ok": False, "error": f"No route origin found for: {origin}"}
    if not destination_places:
        return {"ok": False, "error": f"No route destination found for: {destination}"}
    origin_place = origin_places[0]
    destination_place = destination_places[0]
    coordinates = (
        f"{origin_place['longitude']},{origin_place['latitude']};"
        f"{destination_place['longitude']},{destination_place['latitude']}"
    )
    route_url = (
        f"https://router.project-osrm.org/route/v1/{normalized_profile}/{coordinates}"
        "?overview=false&steps=false"
    )
    route_response = _http_get_json(route_url, timeout_seconds=20)
    if not route_response.get("ok"):
        return route_response
    payload = route_response.get("payload")
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "Unexpected OSRM response",
            "raw": route_response.get("raw", ""),
        }
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        return {"ok": False, "error": "No route returned", "raw": route_response.get("raw", "")}
    route = routes[0]
    distance_meters = route.get("distance")
    duration_seconds = route.get("duration")
    return {
        "ok": True,
        "profile": normalized_profile,
        "origin": origin_place,
        "destination": destination_place,
        "route": {
            "distance_meters": distance_meters,
            "distance_km": round(float(distance_meters) / 1000, 2)
            if isinstance(distance_meters, (int, float))
            else None,
            "duration_seconds": duration_seconds,
            "duration_minutes": round(float(duration_seconds) / 60, 1)
            if isinstance(duration_seconds, (int, float))
            else None,
        },
    }


def _build_filesystem_server() -> FastMCP:
    mcp = FastMCP("Chanakya Filesystem Tools", json_response=True)

    @mcp.tool()
    def list_directory(path: str = ".", work_id: str = "temp") -> dict[str, Any]:
        """List files in the shared sandbox workspace for the given work_id."""
        try:
            return _list_directory(path, work_id)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            return _filesystem_error_payload(error=exc, path=path, work_id=work_id)

    @mcp.tool()
    def read_text_file(
        path: str,
        work_id: str = "temp",
        max_chars: int = MAX_TEXT_CHARS,
    ) -> dict[str, Any]:
        """Read a UTF-8 text file from the shared sandbox workspace for the given work_id."""
        try:
            return _read_text_file(path, work_id, max_chars)
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError) as exc:
            return _filesystem_error_payload(error=exc, path=path, work_id=work_id)

    @mcp.tool()
    def write_text_file(path: str, content: str, work_id: str = "temp") -> dict[str, Any]:
        """Write a UTF-8 text file inside the shared sandbox workspace for the given work_id."""
        try:
            return _write_text_file(path, content, work_id)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            return _filesystem_error_payload(error=exc, path=path, work_id=work_id)

    return mcp


def _build_git_server() -> FastMCP:
    mcp = FastMCP("Chanakya Git Tools", json_response=True)

    @mcp.tool()
    def git_status() -> dict[str, Any]:
        """Show git working tree status."""
        return _run_git(["status", "--short", "--branch"])

    @mcp.tool()
    def git_diff(pathspec: str = "") -> dict[str, Any]:
        """Show git diff for the repo or a single pathspec."""
        args = ["diff"]
        if pathspec.strip():
            args.extend(["--", pathspec.strip()])
        return _run_git(args)

    @mcp.tool()
    def git_log(limit: int = 10) -> dict[str, Any]:
        """Show recent git commits."""
        bounded = max(1, min(limit, 50))
        return _run_git(["log", f"-n{bounded}", "--oneline", "--decorate"])

    return mcp


def _build_http_server() -> FastMCP:
    mcp = FastMCP("Chanakya HTTP Tools", json_response=True)

    @mcp.tool()
    def http_request(
        url: str,
        method: str = "GET",
        headers_json: str = "{}",
        body: str = "",
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        """Perform an HTTP request without requiring external login."""
        headers = _decode_json_text(headers_json) if headers_json.strip() else {}
        if not isinstance(headers, dict):
            raise ValueError("headers_json must decode to an object")
        return _http_request(
            method=method,
            url=url,
            headers={str(key): str(value) for key, value in headers.items()},
            body=body or None,
            timeout_seconds=max(1, min(timeout_seconds, 60)),
        )

    return mcp


def _build_json_server() -> FastMCP:
    mcp = FastMCP("Chanakya JSON Tools", json_response=True)

    @mcp.tool()
    def format_json(text: str, indent: int = 2, sort_keys: bool = False) -> dict[str, Any]:
        """Format JSON text into a stable pretty-printed representation."""
        payload = _decode_json_text(text)
        bounded_indent = max(0, min(indent, 8))
        formatted = json.dumps(
            payload, indent=bounded_indent, sort_keys=sort_keys, ensure_ascii=True
        )
        return {"formatted": _trim(formatted)}

    @mcp.tool()
    def query_json(text: str, path: str) -> dict[str, Any]:
        """Look up a dotted path inside JSON, like items.0.name."""
        payload = _decode_json_text(text)
        value = _lookup_json_path(payload, path)
        return {"path": path, "value": value}

    return mcp


def _build_shell_utils_server() -> FastMCP:
    mcp = FastMCP("Chanakya Shell Utility Tools", json_response=True)

    @mcp.tool()
    def shell_info() -> dict[str, Any]:
        """Return a few basic host environment details."""
        workspace_root = get_classic_chat_workspace_root()
        return {
            "workspace_root": str(workspace_root),
            "cwd": os.getcwd(),
            "python_executable": os.sys.executable,
            "platform": os.uname().sysname if hasattr(os, "uname") else os.name,
        }

    @mcp.tool()
    def run_basic_command(command_name: str) -> dict[str, Any]:
        """Run a very small allowlisted host command like pwd, date, whoami, or uname."""
        command = SAFE_SHELL_COMMANDS.get(command_name.strip())
        if command is None:
            raise ValueError(f"Unsupported command_name: {command_name}")
        workspace_root = get_classic_chat_workspace_root()
        result = subprocess.run(
            command,
            cwd=workspace_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "command": command,
            "workspace_root": str(workspace_root),
            "output": _trim(output),
        }

    return mcp


def _build_weather_server() -> FastMCP:
    mcp = FastMCP("Chanakya Weather Tools", json_response=True)

    @mcp.tool()
    def get_weather(location: str) -> dict[str, Any]:
        """Fetch a simple current weather summary from wttr.in without login."""
        query = parse.quote((location or "").strip())
        if not query:
            raise ValueError("location is required")
        url = f"https://wttr.in/{query}?format=j1"
        result = _http_request(method="GET", url=url, timeout_seconds=20)
        if not result.get("ok"):
            return result
        body = str(result.get("body") or "")
        payload = json.loads(body)
        current = (payload.get("current_condition") or [{}])[0]
        nearest = (payload.get("nearest_area") or [{}])[0]
        area_name = ((nearest.get("areaName") or [{}])[0]).get("value")
        region = ((nearest.get("region") or [{}])[0]).get("value")
        country = ((nearest.get("country") or [{}])[0]).get("value")
        description = ((current.get("weatherDesc") or [{}])[0]).get("value")
        summary = {
            "location": ", ".join(part for part in (area_name, region, country) if part),
            "temperature_c": current.get("temp_C"),
            "feels_like_c": current.get("FeelsLikeC"),
            "humidity": current.get("humidity"),
            "wind_kmph": current.get("windspeedKmph"),
            "description": description,
        }
        return {
            "ok": True,
            "summary": summary,
            "raw": _trim(body, MAX_WEATHER_BODY_CHARS),
        }

    return mcp


def _build_map_server() -> FastMCP:
    mcp = FastMCP("Chanakya Map Tools", json_response=True)

    @mcp.tool()
    def search_place(query: str, limit: int = 5) -> dict[str, Any]:
        """Search OpenStreetMap places by free-text query."""
        if not query.strip():
            raise ValueError("query is required")
        result = _geocode_place(query, limit=limit)
        if not result.get("ok"):
            return result
        return {"ok": True, "query": query, "results": result.get("places", [])}

    @mcp.tool()
    def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
        """Resolve coordinates into the nearest OpenStreetMap address."""
        return _reverse_geocode_lookup(latitude, longitude)

    @mcp.tool()
    def route_between(origin: str, destination: str, profile: str = "driving") -> dict[str, Any]:
        """Route between two place names using OSM geocoding plus OSRM routing."""
        return _route_places(origin, destination, profile)

    return mcp


def _build_server(mode: str) -> FastMCP:
    if mode == "filesystem":
        return _build_filesystem_server()
    if mode == "git":
        return _build_git_server()
    if mode == "http":
        return _build_http_server()
    if mode == "json":
        return _build_json_server()
    if mode == "shell_utils":
        return _build_shell_utils_server()
    if mode == "weather":
        return _build_weather_server()
    if mode == "map":
        return _build_map_server()
    raise ValueError(f"Unsupported MCP basic tools mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local Chanakya MCP basic tools server")
    parser.add_argument(
        "mode",
        choices=["filesystem", "git", "http", "json", "shell_utils", "weather", "map"],
    )
    args = parser.parse_args()
    mcp = _build_server(args.mode)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
