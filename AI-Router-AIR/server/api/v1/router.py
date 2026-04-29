"""Versioned API router for OpenAI-compatible AIR endpoints."""

from fastapi import APIRouter
from . import audio, batches, chat, evals, models, realtime

router = APIRouter()

router.include_router(chat.router, prefix="/chat")
router.include_router(audio.router, prefix="/audio")
router.include_router(models.router, prefix="/models")
router.include_router(realtime.router, prefix="/realtime")
router.include_router(batches.router, prefix="/batches")
router.include_router(evals.router, prefix="/evals")

# We can also add a catch-all for other /v1/ endpoints if needed
# from fastapi import Request
# from server.core.proxy_engine import proxy_engine
# from server.core.dependencies import get_provider
# @router.post("/{path:path}")
# async def catch_all_post(request: Request, path: str):
#     provider = await get_provider(request)
#     return await proxy_engine.forward_request(request, provider, path)
