from fastapi import APIRouter
from server.services.provider_manager import provider_manager

router = APIRouter(tags=["Models"])

@router.get("/")
async def list_models(refresh: bool = False):
    if refresh:
        models = await provider_manager.refresh_models()
    else:
        models = provider_manager.models_cache.get("all", [])
        # If cache is empty, we might want to refresh anyway or just return empty
        if not models:
             models = await provider_manager.refresh_models()
             
    return {"object": "list", "data": models}
