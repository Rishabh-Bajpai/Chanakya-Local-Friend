from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


USER_AGENT = "ChanakyaCoreAgent/0.1 (+https://local.app)"
MAX_FETCH_CHARS = 4000
MAX_SEARCH_ITEMS = 5


def get_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_url(url: str) -> str:
    normalized_url = (url or "").strip()
    if not normalized_url:
        raise ValueError("url is required")
    if not normalized_url.startswith(("http://", "https://")):
        normalized_url = f"https://{normalized_url}"

    request = Request(
        normalized_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        content_type = response.headers.get("Content-Type", "")
        body = response.read().decode(charset, errors="replace")

    if "html" in content_type:
        body = _html_to_text(body)

    cleaned = _normalize_whitespace(body)
    truncated = cleaned[:MAX_FETCH_CHARS]
    if len(cleaned) > MAX_FETCH_CHARS:
        truncated += "\n\n[truncated]"
    return truncated or "No readable content found."


def search_web(query: str) -> str:
    normalized_query = (query or "").strip()
    if not normalized_query:
        raise ValueError("query is required")

    request = Request(
        "https://api.duckduckgo.com/?q="
        f"{quote_plus(normalized_query)}&format=json&no_html=1&skip_disambig=1",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    lines = [f"Search results for: {normalized_query}"]
    abstract = _normalize_whitespace(payload.get("Abstract") or "")
    abstract_url = payload.get("AbstractURL") or ""
    heading = payload.get("Heading") or ""
    if abstract:
        heading_prefix = f"{heading}: " if heading else ""
        lines.append(f"1. {heading_prefix}{abstract}")
        if abstract_url:
            lines.append(f"   URL: {abstract_url}")

    entries = _collect_search_entries(payload)
    offset = 2 if abstract else 1
    for index, entry in enumerate(entries[:MAX_SEARCH_ITEMS], start=offset):
        lines.append(f"{index}. {entry['text']}")
        if entry["url"]:
            lines.append(f"   URL: {entry['url']}")

    if len(lines) == 1:
        return f"No useful web search results found for: {normalized_query}"
    return "\n".join(lines)


def _html_to_text(html: str) -> str:
    without_scripts = re.sub(
        r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    with_breaks = re.sub(
        r"</(p|div|section|article|li|h[1-6]|br)>",
        "\n",
        without_scripts,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", with_breaks)
    return unescape(text)


def _normalize_whitespace(text: str) -> str:
    collapsed = re.sub(r"\r", "", text)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    collapsed = re.sub(r"[ \t]{2,}", " ", collapsed)
    return collapsed.strip()


def _collect_search_entries(payload: dict) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in payload.get("Results") or []:
        text = _normalize_whitespace(item.get("Text") or "")
        url = item.get("FirstURL") or ""
        if text:
            entries.append({"text": text, "url": url})

    for item in payload.get("RelatedTopics") or []:
        if isinstance(item, dict) and item.get("Topics"):
            for nested in item.get("Topics") or []:
                text = _normalize_whitespace(nested.get("Text") or "")
                url = nested.get("FirstURL") or ""
                if text:
                    entries.append({"text": text, "url": url})
            continue
        text = _normalize_whitespace(item.get("Text") or "")
        url = item.get("FirstURL") or ""
        if text:
            entries.append({"text": text, "url": url})
    return entries
