from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def discover_a2a_options(a2a_url: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    normalized_a2a_url = str(a2a_url or "").strip().rstrip("/")
    if not normalized_a2a_url:
        return {"opencode_url": None, "remote_agents": [], "providers": [], "models": []}

    opencode_url = _derive_opencode_url(normalized_a2a_url)
    agents_payload = _fetch_json(opencode_url, "/agent", timeout_seconds=timeout_seconds)
    config_payload = _fetch_json(opencode_url, "/global/config", timeout_seconds=timeout_seconds)
    providers_payload = _fetch_json(opencode_url, "/provider", timeout_seconds=timeout_seconds)

    models = _extract_models(config_payload, providers_payload)
    provider_ids = sorted(
        dict.fromkeys(item["provider"] for item in models if item.get("provider"))
    )
    return {
        "opencode_url": opencode_url,
        "remote_agents": _extract_agents(agents_payload, config_payload),
        "providers": provider_ids,
        "models": models,
    }


def _derive_opencode_url(a2a_url: str) -> str:
    parsed = urlparse(a2a_url)
    scheme = parsed.scheme or "http"
    hostname = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18770
    if port == 18770:
        port = 18496
    return f"{scheme}://{hostname}:{port}"


def _fetch_json(base_url: str, path: str, *, timeout_seconds: float) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=max(1.0, timeout_seconds)) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _extract_agents(agents_payload: Any, config_payload: Any) -> list[str]:
    discovered: list[str] = []
    if isinstance(agents_payload, list):
        for item in agents_payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                discovered.append(name)
    if discovered:
        return sorted(dict.fromkeys(discovered))

    configured = (config_payload or {}).get("agent") or {}
    if isinstance(configured, dict):
        return sorted(key for key in configured if str(key).strip())
    return []


def _extract_models(config_payload: Any, providers_payload: Any) -> list[dict[str, str]]:
    configured_providers = (config_payload or {}).get("provider") or {}
    available_models = _provider_model_lookup(providers_payload)
    models: list[dict[str, str]] = []
    if isinstance(configured_providers, dict):
        for provider_id, provider_config in configured_providers.items():
            if not isinstance(provider_config, dict):
                continue
            configured_models = provider_config.get("models") or {}
            for model_id in configured_models:
                model_key = (str(provider_id), str(model_id))
                details = available_models.get(model_key, {})
                models.append(
                    {
                        "provider": str(provider_id),
                        "id": str(model_id),
                        "label": str(details.get("name") or model_id),
                    }
                )
    return sorted(models, key=lambda item: (item["provider"], item["label"], item["id"]))


def _provider_model_lookup(providers_payload: Any) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(providers_payload, dict):
        return lookup
    for provider in providers_payload.get("all") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id:
            continue
        raw_models = provider.get("models") or {}
        if not isinstance(raw_models, dict):
            continue
        for model_id, model_details in raw_models.items():
            if isinstance(model_details, dict):
                lookup[(provider_id, str(model_id))] = model_details
    return lookup
