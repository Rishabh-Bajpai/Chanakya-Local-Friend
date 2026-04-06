from fastapi import APIRouter, Request, Depends
from server.core.proxy_engine import proxy_engine
from server.core.dependencies import get_provider
from server.schemas.provider_schema import ProviderConfig

router = APIRouter(tags=["Chat"])

@router.post("/completions")
async def chat_completions(request: Request, provider: ProviderConfig = Depends(get_provider)):
    path = request.url.path.split("/v1/")[-1] 
    return await proxy_engine.forward_request(request, provider, path)
