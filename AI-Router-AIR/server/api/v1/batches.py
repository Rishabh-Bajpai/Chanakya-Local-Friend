"""Batches API routes for creating, retrieving, listing, and cancelling batches."""

from fastapi import APIRouter, Request, Depends
from server.core.proxy_engine import proxy_engine
from server.core.dependencies import get_provider
from server.schemas.provider_schema import ProviderConfig

router = APIRouter(tags=["Batches"])


@router.post("/")
async def create_batch(
    request: Request, provider: ProviderConfig = Depends(get_provider)
):
    """Execute a batch of requests."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.get("/{batch_id}")
async def retrieve_batch(
    request: Request,
    batch_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Get the status of a batch."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.get("/")
async def list_batches(
    request: Request, provider: ProviderConfig = Depends(get_provider)
):
    """List all batches in the organization."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.post("/{batch_id}/cancel")
async def cancel_batch(
    request: Request,
    batch_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Cancel an active batch."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)
