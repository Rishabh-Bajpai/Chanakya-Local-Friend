"""Custom exceptions and global error handling for AIR requests."""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger("air.exceptions")

class ProviderNotFoundError(HTTPException):
    """Raised when no provider matches the current request."""

    def __init__(self, detail: str = "No suitable provider found for this request"):
        super().__init__(status_code=404, detail=detail)

class ProviderUnavailableError(HTTPException):
    """Raised when a matching provider exists but cannot serve traffic."""

    def __init__(self, detail: str = "Provider is currently unavailable"):
        super().__init__(status_code=503, detail=detail)

class ProxyError(HTTPException):
    """Raised when forwarding a request to an upstream provider fails."""

    def __init__(self, detail: str = "Error proxying request to upstream provider"):
        super().__init__(status_code=502, detail=detail)

async def global_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions and return a generic server error response."""
    logger.error(f"Global exception on {request.method} {request.url.path}", exc_info=True)
    # Generic catch-all without leaking internal details
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error"}
    )
