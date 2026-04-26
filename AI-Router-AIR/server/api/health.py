"""Health check endpoint for the AIR server."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    """Return a simple health status response."""
    return {"status": "ok"}
