"""FastAPI entrypoint for the AIR server and dashboard views."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env into os.environ BEFORE any local imports that may trigger
# Settings() (which calls load_providers() → os.getenv()).  Without this
# the provider vars saved in apps/AI-Router-AIR/.env are invisible and
# settings.PROVIDERS will be empty on every restart.
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server.api import router as api_router
from server.core.exceptions import global_exception_handler
from server.core.logging import LOG_FILE_PATH, logger as air_logger

app = FastAPI(
    title="AI Router (AIR)", description="Unified API for LLM, STT, and TTS", version="0.1.0"
)

BASE_DIR = Path(__file__).resolve().parent

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# CORS configuration
origins = ["*"]  # Allow all origins for now, can be restricted later

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(Exception, global_exception_handler)

app.include_router(api_router.router)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


async def _refresh_with_retry(
    provider_manager,
    logger: logging.Logger,
    max_retries: int = 5,
    delay: float = 1.0,
):
    """Refresh the model cache with exponential backoff retries."""
    for attempt in range(max_retries):
        try:
            await provider_manager.refresh_models()
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "Model cache refresh attempt %d/%d failed: %s. Retrying in %.1fs…",
                    attempt + 1, max_retries, e, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            else:
                logger.warning(
                    "Model cache refresh failed after %d attempts: %s",
                    max_retries, e,
                )


@app.on_event("startup")
async def startup_discovery():
    """Run provider discovery on startup as a background task."""
    from server.core.config import settings
    from server.services.discovery import discovery_service
    from server.services.provider_manager import provider_manager

    air_logger.info("AIR startup complete. Realtime log file: %s", LOG_FILE_PATH)

    async def run_refresh():
        """Discover new providers and refresh the shared model cache."""
        try:
            logging.info("🔍 Running auto-discovery of AI providers...")
            discovered = await discovery_service.scan()
            new_providers = discovery_service.filter_new(discovered, settings.PROVIDERS)

            if new_providers:
                logging.info(f"✨ Discovered {len(new_providers)} new provider(s):")
                for dp in new_providers:
                    types_str = ", ".join(dp.detected_types)
                    logging.info(
                        f"   • {dp.name} at {dp.base_url} [{types_str}] — {len(dp.models)} model(s)"
                    )
                logging.info("   Open the dashboard to add them.")
            else:
                logging.info("No new providers discovered beyond what is already configured.")

            # Also refresh the provider manager's model cache
            await _refresh_with_retry(provider_manager, logging)
        except Exception as e:
            logging.warning(f"Background auto-discovery/refresh failed: {e}")

    if not settings.DISCOVERY_ENABLED:
        logging.info("Auto-discovery is disabled (DISCOVERY_ENABLED=false)")

        # Even if discovery is off, we still want to refresh configured models in background
        async def refresh_only():
            """Refresh configured provider models when discovery is disabled."""
            await _refresh_with_retry(provider_manager, logging)

        asyncio.create_task(refresh_only())
        return

    # Run everything in background
    asyncio.create_task(run_refresh())


@app.get("/")
async def root(request: Request):
    """Render the main AIR dashboard."""
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"request": request})


@app.get("/status")
async def api_status_page(request: Request):
    """Render the service status dashboard page."""
    from server.services.provider_manager import provider_manager

    status = provider_manager.get_service_status()
    return templates.TemplateResponse(
        request=request,
        name="api_status.html",
        context={"request": request, "status": status},
    )


if __name__ == "__main__":
    import uvicorn

    # Use SERVER_PORT now
    port = int(os.getenv("SERVER_PORT", 5512))
    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=True)
