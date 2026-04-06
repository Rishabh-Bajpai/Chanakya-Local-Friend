from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="AI Router (AIR)", description="Unified API for LLM, STT, and TTS", version="0.1.0")

# Templates
templates = Jinja2Templates(directory="server/templates")

# CORS configuration
origins = ["*"]  # Allow all origins for now, can be restricted later

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Placeholder for routes - will be imported later
from server.api import router as api_router
from server.core.exceptions import global_exception_handler

app.add_exception_handler(Exception, global_exception_handler)

app.include_router(api_router.router)

# Mount static files
app.mount("/static", StaticFiles(directory="server/static"), name="static")

@app.on_event("startup")
async def startup_discovery():
    """Run provider discovery on startup as a background task."""
    import logging
    import asyncio
    from server.core.config import settings
    from server.services.discovery import discovery_service
    from server.services.provider_manager import provider_manager

    async def run_refresh():
        try:
            logging.info("🔍 Running auto-discovery of AI providers...")
            discovered = await discovery_service.scan()
            new_providers = discovery_service.filter_new(discovered, settings.PROVIDERS)

            if new_providers:
                logging.info(f"✨ Discovered {len(new_providers)} new provider(s):")
                for dp in new_providers:
                    types_str = ", ".join(dp.detected_types)
                    logging.info(f"   • {dp.name} at {dp.base_url} [{types_str}] — {len(dp.models)} model(s)")
                logging.info("   Open the dashboard to add them.")
            else:
                logging.info("No new providers discovered beyond what is already configured.")

            # Also refresh the provider manager's model cache
            await provider_manager.refresh_models()
        except Exception as e:
            logging.warning(f"Background auto-discovery/refresh failed: {e}")

    if not settings.DISCOVERY_ENABLED:
        logging.info("Auto-discovery is disabled (DISCOVERY_ENABLED=false)")
        
        # Even if discovery is off, we still want to refresh configured models in background
        async def refresh_only():
            try:
                await provider_manager.refresh_models()
            except Exception as e:
                logging.warning(f"Background model refresh failed: {e}")
        
        asyncio.create_task(refresh_only())
        return

    # Run everything in background
    asyncio.create_task(run_refresh())

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/status")
async def api_status_page(request: Request):
    from server.services.provider_manager import provider_manager
    status = provider_manager.get_service_status()
    return templates.TemplateResponse("api_status.html", {"request": request, "status": status})

if __name__ == "__main__":
    import uvicorn
    # Use SERVER_PORT now
    port = int(os.getenv("SERVER_PORT", 5012))
    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=True)
