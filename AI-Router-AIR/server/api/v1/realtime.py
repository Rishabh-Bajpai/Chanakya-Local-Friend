"""Realtime API routes for client secrets and SIP call management."""

from fastapi import APIRouter, Request, Depends
from server.core.proxy_engine import proxy_engine
from server.core.dependencies import get_provider
from server.schemas.provider_schema import ProviderConfig

router = APIRouter(tags=["Realtime"])


@router.post("/client_secrets")
async def create_client_secret(
    request: Request, provider: ProviderConfig = Depends(get_provider)
):
    """Create an ephemeral client secret for Realtime sessions."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.post("/calls/{call_id}/accept")
async def accept_call(
    request: Request,
    call_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Accept a SIP call."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.post("/calls/{call_id}/hangup")
async def hangup_call(
    request: Request,
    call_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Hang up an active call."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)
