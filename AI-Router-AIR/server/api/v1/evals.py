"""Evals API routes for managing evaluations, runs, and output items."""

from fastapi import APIRouter, Request, Depends
from server.core.proxy_engine import proxy_engine
from server.core.dependencies import get_provider
from server.schemas.provider_schema import ProviderConfig

router = APIRouter(tags=["Evals"])


@router.post("/")
async def create_eval(
    request: Request, provider: ProviderConfig = Depends(get_provider)
):
    """Structure an evaluation."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.get("/{eval_id}")
async def retrieve_eval(
    request: Request,
    eval_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Get evaluation details."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.post("/{eval_id}")
async def update_eval(
    request: Request,
    eval_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Update evaluation properties."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.delete("/{eval_id}")
async def delete_eval(
    request: Request,
    eval_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Delete an evaluation."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.get("/")
async def list_evals(
    request: Request, provider: ProviderConfig = Depends(get_provider)
):
    """List evaluations in a project."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.post("/{eval_id}/runs")
async def create_eval_run(
    request: Request,
    eval_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """Start an evaluation run."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)


@router.get("/runs/{run_id}/output_items")
async def list_output_items(
    request: Request,
    run_id: str,
    provider: ProviderConfig = Depends(get_provider),
):
    """List items from an evaluation run."""
    path = request.url.path.split("/v1/")[-1]
    return await proxy_engine.forward_request(request, provider, path)
