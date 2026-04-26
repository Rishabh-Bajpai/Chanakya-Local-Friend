"""FastAPI dependencies for routing AIR requests to the right provider."""

from fastapi import Request
from server.services.provider_manager import provider_manager
from server.schemas.provider_schema import ProviderConfig
from server.core.exceptions import ProviderNotFoundError


async def get_provider(request: Request) -> ProviderConfig:
    """Resolve the provider for the current request path and payload."""
    path = request.url.path

    if "models" in path:
        # Not applicable if just returning models
        raise ProviderNotFoundError(f"Provider resolution not applicable for models endpoint: {path}")

    # Needs body extraction for detailed chat completions mapping config
    body = None
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
              body = await request.json()
        elif "multipart/form-data" in content_type:
              body = await request.form()

    if "chat/completions" in path or "completions" in path:
        model = body.get("model") if body else None
        if model:
            provider = provider_manager.get_provider_for_model(model)
            if provider:
                return provider

        provider = provider_manager.get_provider_by_type("llm")
        if provider:
            return provider
        raise ProviderNotFoundError(f"No LLM provider found for model: {model}")

    elif "audio/speech" in path:
        model = body.get("model") if body else None
        if model:
            provider = provider_manager.get_provider_for_model(model)
            if provider:
                return provider

        provider = provider_manager.get_provider_by_type("tts")
        if provider:
            return provider
        raise ProviderNotFoundError(f"No TTS provider found for model: {model}")

    elif "audio/transcriptions" in path or "audio/translations" in path:
        model = body.get("model") if body else None
        if model:
            provider = provider_manager.get_provider_for_model(model)
            if provider:
                return provider
        provider = provider_manager.get_provider_by_type("stt")
        if provider:
            return provider
        raise ProviderNotFoundError(f"No STT provider found for model: {model}")

    elif "embeddings" in path:
        provider = provider_manager.get_provider_by_type("llm")
        if provider:
            return provider
        raise ProviderNotFoundError("No LLM provider found for embeddings")

    raise ProviderNotFoundError()
