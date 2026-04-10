from __future__ import annotations

from typing import Any

import httpx


def discover_opencode_options(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    normalized_base_url = (base_url or "").strip().rstrip("/")
    if not normalized_base_url:
        return {}

    with httpx.Client(base_url=normalized_base_url, timeout=timeout) as client:
        agents = _fetch_json(client, "/agent")
        config = _fetch_json(client, "/global/config")
        providers = _fetch_json(client, "/provider")

    remote_agents = _extract_agents(agents, config)
    models = _extract_models(config, providers)
    return {
        "remote_agents": remote_agents,
        "models": models,
    }


def _fetch_json(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


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


def _extract_models(
    config_payload: Any, providers_payload: Any
) -> list[dict[str, str]]:
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
                label = str(details.get("name") or model_id)
                models.append(
                    {
                        "provider": str(provider_id),
                        "id": str(model_id),
                        "label": label,
                    }
                )
    return sorted(
        models, key=lambda item: (item["provider"], item["label"], item["id"])
    )


def _provider_model_lookup(
    providers_payload: Any,
) -> dict[tuple[str, str], dict[str, Any]]:
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
            if not isinstance(model_details, dict):
                continue
            lookup[(provider_id, str(model_id))] = model_details
    return lookup
