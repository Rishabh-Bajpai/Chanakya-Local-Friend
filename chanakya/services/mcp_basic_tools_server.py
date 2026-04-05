from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_TEXT_CHARS = 20000
MAX_HTTP_BODY_CHARS = 50000
MAX_WEATHER_BODY_CHARS = 12000
SAFE_SHELL_COMMANDS = {
    "date": ["date"],
    "pwd": ["pwd"],
    "uname": ["uname", "-a"],
    "whoami": ["whoami"],
}


def _resolve_path(path: str) -> Path:
    raw = (path or ".").strip()
    candidate = (REPO_ROOT / raw).resolve()
    candidate.relative_to(REPO_ROOT)
    return candidate


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


def _build_filesystem_server() -> FastMCP:
    mcp = FastMCP("Chanakya Filesystem Tools", json_response=True)

    @mcp.tool()
    def list_directory(path: str = ".") -> dict[str, Any]:
        """List files and directories under a repo-relative path."""
        resolved = _resolve_path(path)
        entries = []
        for item in sorted(resolved.iterdir(), key=lambda child: child.name.lower()):
            entries.append(
                {
                    "name": item.name,
                    "path": str(item.relative_to(REPO_ROOT)),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"path": str(resolved.relative_to(REPO_ROOT)), "entries": entries}

    @mcp.tool()
    def read_text_file(path: str, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
        """Read a UTF-8 text file from a repo-relative path."""
        resolved = _resolve_path(path)
        content = resolved.read_text(encoding="utf-8")
        trimmed = _trim(content, max(1, min(max_chars, MAX_TEXT_CHARS)))
        return {"path": str(resolved.relative_to(REPO_ROOT)), "content": trimmed}

    @mcp.tool()
    def write_text_file(path: str, content: str) -> dict[str, Any]:
        """Write a UTF-8 text file at a repo-relative path."""
        resolved = _resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "path": str(resolved.relative_to(REPO_ROOT)),
            "bytes_written": len(content.encode("utf-8")),
        }

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
        return {
            "repo_root": str(REPO_ROOT),
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
        result = subprocess.run(
            command,
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
            "command": command,
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
    raise ValueError(f"Unsupported MCP basic tools mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local Chanakya MCP basic tools server")
    parser.add_argument(
        "mode",
        choices=["filesystem", "git", "http", "json", "shell_utils", "weather"],
    )
    args = parser.parse_args()
    mcp = _build_server(args.mode)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
