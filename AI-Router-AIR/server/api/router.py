from fastapi import APIRouter
from server.api.v1 import router as v1_router
from server.api import admin, health

router = APIRouter()

router.include_router(v1_router.router, prefix="/v1")
router.include_router(admin.router, prefix="/api/config")
router.include_router(health.router)
