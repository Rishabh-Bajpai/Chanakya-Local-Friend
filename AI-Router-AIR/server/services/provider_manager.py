"""Provider registry loading, model discovery, and routing helpers for AIR."""

import httpx
import logging
import asyncio
import re
from typing import List, Dict, Any, Optional
from server.core.config import settings, ProviderConfig

logger = logging.getLogger(__name__)


def infer_model_type(model: Dict[str, Any], default_type: str = "llm") -> str:
    """
    Infer the type of a model (llm, stt, tts) from its metadata.

    Priority:
    1. Check "task" field (HuggingFace-style metadata)
    2. Check "voices" field (strong TTS indicator)
    3. Check "id" naming conventions (e.g. whisper, stt)
    4. Fall back to `default_type`
    """
    m_id = str(model.get("id", "")).lower()

    # Case A: Check "task" field (e.g., HuggingFace style output)
    if "task" in model:
        task = str(model["task"]).lower()
        if "text-to-speech" in task or "tts" in task:
            return "tts"
        elif "automatic-speech-recognition" in task or "speech-to-text" in task or "stt" in task:
            return "stt"
        elif task in ["text-generation", "chat-completion", "embeddings"]:
            return "llm"

    # Case B: Check "voices" field (Strong indicator of a TTS model)
    if "voices" in model and isinstance(model["voices"], list) and len(model["voices"]) > 0:
        return "tts"

    # Case C: Check ID naming heuristics (tokenized matching)
    # Tokenize on non-alphanumeric boundaries to avoid false positives (like assistant -> stt)
    tokens = set(re.split(r'[^a-zA-Z0-9]', m_id))
    stt_keywords = {"whisper", "stt"}
    tts_keywords = {"tts", "kokoro"}

    if any(k in tokens for k in stt_keywords):
        return "stt"
    if any(k in tokens for k in tts_keywords):
        return "tts"

    return default_type


class ProviderManager:
    """
    Manages the lifecycle and discovery of AI providers and their models.

    This singleton service is responsible for:
    - Loading provider configurations from settings.
    - Fetching and caching available models from all providers.
    - Inferring model capabilities (LLM, STT, TTS) based on metadata.
    - Providing lookup methods for routing requests to the correct provider.
    """
    def __init__(self):
        # Cache for storing raw model data fetched from providers
        # Structure: {"all": [model_dict, ...], "by_provider": {provider_name: [model_dict, ...]}}
        self.models_cache: Dict[str, List[Dict[str, Any]]] = {}

        # Simple in-memory cache for capabilities (future use)
        # Format: {provider_name: {"chat": bool, "embeddings": bool, ...}}
        self.capabilities: Dict[str, Dict[str, bool]] = {}

    @property
    def providers(self) -> List[ProviderConfig]:
        """Always return the current list from settings (allows dynamic reload)."""
        return settings.PROVIDERS

    async def _fetch_models_from_provider(self, provider: ProviderConfig) -> List[Dict[str, Any]]:
        """Fetch and annotate the model list exposed by a single provider."""
        url = f"{provider.base_url.rstrip('/')}/models"
        headers = {
            "Content-Type": "application/json"
        }
        if provider.api_key and provider.api_key != "na":
            headers["Authorization"] = f"Bearer {provider.api_key}"

        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                logger.info(f"Querying models from {url}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    models = data
                elif isinstance(data, dict):
                    models = data.get("data", [])
                else:
                    models = []

                # Tag models with provider info for downstream routing
                for model in models:
                    model["provider_name"] = provider.name
                    model["provider_type"] = infer_model_type(model, default_type=provider.type)

                logger.info(f"Successfully fetched {len(models)} models from {provider.name}")
                return models
        except Exception as e:
            logger.error(f"Failed to fetch models from {provider.name} ({url}): {e}")
            # Try 127.0.0.1 if localhost failed
            if "localhost" in url:
                alt_url = url.replace("localhost", "127.0.0.1")
                try:
                     async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                        logger.info(f"Retrying with 127.0.0.1: {alt_url}")
                        response = await client.get(alt_url, headers=headers)
                        response.raise_for_status()
                        data = response.json()
                        if isinstance(data, list):
                            models = data
                        elif isinstance(data, dict):
                            models = data.get("data", [])
                        else:
                            models = []
                        for model in models:
                            model["provider_name"] = provider.name
                            model["provider_type"] = infer_model_type(model, default_type=provider.type)
                        return models
                except Exception as e2:
                     logger.error(f"Retry failed for {provider.name}: {e2}")

            return []

    async def refresh_models(self) -> List[Dict[str, Any]]:
        """Refreshes the list of available models from all providers."""
        all_models = []
        provider_models_cache = dict(self.models_cache.get("by_provider", {}))

        # Debug logging
        logger.info(f"Refreshing models. Found {len(self.providers)} providers.")
        for p in self.providers:
            logger.info(f"Provider: {p.name} ({p.type}) -> {p.base_url}")

        # Now fetching from ALL provider types by default.
        # Most OpenAI-compatible STT/TTS implementations also expose /models or at least return a list.
        # If they don't, we might need specific handling, but the user says their services have it.
        tasks = [self._fetch_models_from_provider(p) for p in self.providers]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for provider, res in zip(self.providers, results):
            if isinstance(res, list):
                provider_models_cache[provider.name] = res
                all_models.extend(res)
            else:
                logger.warning(f"Error during fetch for {provider.name}: {res}")
                all_models.extend(provider_models_cache.get(provider.name, []))

        configured_provider_names = {provider.name for provider in self.providers}
        provider_models_cache = {
            provider_name: models
            for provider_name, models in provider_models_cache.items()
            if provider_name in configured_provider_names
        }

        # Deduplicate models by ID to ensure a clean Unified Registry
        unique_models = {}
        for model in all_models:
            m_id = model.get("id")
            if m_id and m_id not in unique_models:
                unique_models[m_id] = model
            # Optional: Allow duplicates if from different providers?
            # User specifically asked why they see duplicates when only one is there.
            # So strict deduplication by ID is preferred.

        self.models_cache["by_provider"] = provider_models_cache
        self.models_cache["all"] = list(unique_models.values())
        return self.models_cache["all"]

    def get_provider_for_model(self, model_id: str) -> Optional[ProviderConfig]:
        """Finds the provider that hosts the given model_id."""
        # This is a bit tricky if multiple providers support the same model name.
        # We'll just pick the first one found in our cache or refresh if needed.
        if "all" not in self.models_cache:
            # This relies on refresh_models being called at startup or periodically
            pass

        # Search in cache
        for model in self.models_cache.get("all", []):
            if model["id"] == model_id:
                # Find provider config
                for p in self.providers:
                    if p.name == model.get("provider_name"):
                        return p

        # Fallback: if we can't find it, maybe the user passed a provider specific ID?
        # Or maybe it's a generic request.
        # For now, let's return None or a default if configured.
        return None

    def get_provider_by_type(self, p_type: str) -> Optional[ProviderConfig]:
        """Returns the first provider of a specific type.
           If no provider is explicitly configured with that type, checks inferred model capabilities."""

        # 1. Direct match in configuration
        for p in self.providers:
            if p.type == p_type:
                return p

        # 2. Fallback: Search in discovered models
        # If we have a provider that HAS models of this type, use it.
        if "all" in self.models_cache:
            for model in self.models_cache["all"]:
                if model.get("provider_type") == p_type:
                    # Found a model of the requested type, find its provider
                    provider_name = model.get("provider_name")
                    for p in self.providers:
                        if p.name == provider_name:
                            return p

        return None

    def get_service_status(self) -> Dict[str, bool]:
        """Checks which services (LLM, STT, TTS) are currently active based on discovered models/providers."""
        status = {
            "llm": False,
            "stt": False,
            "tts": False
        }

        # Check cached models first (more accurate for "Universal" providers)
        if "all" in self.models_cache:
            for model in self.models_cache["all"]:
                m_type = model.get("provider_type")
                if m_type in status:
                    status[m_type] = True

        # Fallback: Check explicitly configured provider types
        # If cache is empty but we have a provider configured explicitly as "stt", trust config.
        for p in self.providers:
            if p.type in status and not status[p.type]:
                status[p.type] = True

        return status

provider_manager = ProviderManager()
