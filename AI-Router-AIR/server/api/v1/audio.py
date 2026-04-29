"""Audio endpoints for forwarding TTS and STT requests to providers."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from server.core.proxy_engine import proxy_engine
from server.core.dependencies import get_provider
from server.schemas.provider_schema import ProviderConfig
from server.services.provider_manager import provider_manager

router = APIRouter(tags=["Audio"])


@router.post("/speech")
async def audio_speech(request: Request, provider: ProviderConfig = Depends(get_provider)):
    """Forward text-to-speech requests to the resolved provider."""
    path = request.url.path.split("/v1/")[-1]

    # Try to extract stream from JSON if present
    is_stream = None
    try:
        body = await request.json()
        is_stream = body.get("stream")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    return await proxy_engine.forward_request(request, provider, path, is_stream=is_stream)


def _is_uploaded_file(value: object) -> bool:
    """Return whether a form value looks like an uploaded file object."""
    return all(hasattr(value, attr) for attr in ("filename", "file", "content_type"))


async def parse_form_to_multipart(form_data):
    """Split FastAPI form data into scalar fields and multipart file tuples."""
    files = {}
    data = {}
    for key, value in form_data.items():
        if _is_uploaded_file(value):
            files[key] = (value.filename, value.file, value.content_type)
        else:
            data[key] = value
    return data, files


@router.post("/transcriptions")
async def audio_transcriptions(request: Request):
    """Forward speech-to-text transcription uploads to an STT provider."""
    path = request.url.path.split("/v1/")[-1]

    # For multipart, calling request.form() consumes the stream.
    form_data = await request.form()
    model = form_data.get("model")
    is_stream = form_data.get("stream") == "true"

    provider = None
    if model:
        provider = provider_manager.get_provider_for_model(model)

    if not provider:
        provider = provider_manager.get_provider_by_type("stt")

    if not provider:
        raise HTTPException(status_code=503, detail="Provider not available")

    # Reconstruct the files and data to forward
    data, files = await parse_form_to_multipart(form_data)

    return await proxy_engine.forward_multipart_request(
        request, provider, path, data=data, files=files, is_stream=is_stream
    )


@router.post("/translations")
async def audio_translations(request: Request):
    """Forward speech translation uploads to an STT provider."""
    path = request.url.path.split("/v1/")[-1]

    form_data = await request.form()
    model = form_data.get("model")
    is_stream = form_data.get("stream") == "true"

    provider = None
    if model:
        provider = provider_manager.get_provider_for_model(model)

    if not provider:
        provider = provider_manager.get_provider_by_type("stt")

    if not provider:
        raise HTTPException(status_code=503, detail="Provider not available")

    data, files = await parse_form_to_multipart(form_data)

    return await proxy_engine.forward_multipart_request(
        request, provider, path, data=data, files=files, is_stream=is_stream
    )
