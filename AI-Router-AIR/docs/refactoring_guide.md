# Code Refactoring for AI Router AIR

1. Unified Dependency Management
Consolidate to a single `pyproject.toml` (similar to what we did in PG) at the root, replacing both `requirements.txt` files. This gives us version pinning, optional dependency groups, and tool configuration in one place.
[NEW]  `pyproject.toml`
- Define [project] with dependencies (fastapi, uvicorn, httpx, etc.)
- Add [project.optional-dependencies] with dev group (pytest, pytest-asyncio, httpx for testing)
- Add [tool.pytest.ini_options] for test configuration

2.  Pydantic-Based Settings with Validation
Replace the manual `Settings` class with `pydantic-settings (BaseSettings)`. This gives you automatic env-var loading, type validation, and cleaner code.
[MODIFY] config.py
- Convert `Settings` to inherit from pydantic_settings.BaseSettings
- Use model_validator for loading providers from env vars
- Extract `.env` file manipulation methods into a separate `EnvFileManager `class (Single Responsibility Principle)
- Keep `ProviderConfig` as a Pydantic `BaseModel `(already is)



# Proposed Refactoring: Highly Modular AI Router Architecture (only for the server folder/app)

## Overview

To support the full range of OpenAI APIs  and facilitate parallel development by multiple engineers, I propose transitioning from a monolithic "catch-all" proxy to a **Modular Router Architecture**.

This architecture follows the **Separation of Concerns** principle, ensuring that API routing, business logic, and infrastructure are decoupled.

## 1. Proposed Directory Structure

```text
server/
├── __init__.py
├── main.py                 # Application entry point and global middleware
├── api/                    # API Layer: Request handling & Routing
│   ├── __init__.py
│   ├── router.py           # Top-level router (includes /v1)
│   ├── health.py           # Health checks for server and APIs
│   └── v1/                 # Version 1 API grouping
│       ├── __init__.py
│       ├── router.py       # Includes all sub-routers (chat, audio, etc.)
│       ├── chat.py         # Routes for /v1/chat/*
│       ├── audio.py        # Routes for /v1/audio/*
│       ├── images.py       # Routes for /v1/images/*
│       ├── embeddings.py   # Routes for /v1/embeddings
│       ├── models.py       # Routes for /v1/models
│       ├── files.py        # Routes for /v1/files
│       └── ...             # (One file per major category in openaidoc.md)
├── core/                   # Core Logic & Infrastructure
│   ├── __init__.py
│   ├── config.py           # Application configuration (Settings)
│   ├── env_manager.py      # Logic for .env file read/write
│   ├── logging.py          # Centralized logging configuration
│   ├── proxy_engine.py      # Centralized logic for httpx proxying
│   ├── dependencies.py     # Reusable FastAPI dependencies (Auth, Providers)
│   └── exceptions.py       # Custom exception handlers
├── services/               # Service Layer: Business Logic
│   ├── __init__.py
│   ├── provider_manager.py # Handles backend AI provider state
│   ├── discovery_service.py # Logic for scanning and detecting providers
│   └── file_service.py     # Logic for handling file uploads/storage
├── schemas/                # Data Models (Pydantic models)
│   ├── __init__.py
│   ├── openai_chat.py      # Shared schemas for chat requests/responses
│   ├── openai_audio.py     # Shared schemas for audio
│   └── provider_schema.py   # Internal provider definitions
├── static/                 # Frontend assets (JS, CSS, Images)
└── templates/              # Jinja2 HTML templates
tests/                      # Automated tests folder
├── __init__.py
├── unit/                   # Unit tests for services and core logic
├── integration/            # Integration tests for API routes
└── conftest.py             # Shared test fixtures (FastAPI TestClient, etc.)
```

## 2. Key Refactoring Strategies

### A. Route Decoupling (The "Modular" Part)

Instead of a single `proxy.py`, we will use FastAPI's `APIRouter` to split endpoints. This allows developers to work on `chat.py` without touching `audio.py`.

**Example: `server/api/v1/router.py`**

```python
from fastapi import APIRouter
from . import chat, audio, images, models

router = APIRouter(prefix="/v1")

router.include_router(chat.router, tags=["Chat"])
router.include_router(audio.router, tags=["Audio"])
router.include_router(images.router, tags=["Images"])
router.include_router(models.router, tags=["Models"])
# Developers can easily add new routers here
```

### B. Environment & Configuration Management

We are moving from basic `dotenv` loading to a dedicated `env_manager.py`. This will:

- Safely read and update `.env` variables (critical for dynamic provider configuration).
- Standardize how environment secrets are handled during runtime.

### C. Monitoring & Resilience

- **`server/core/logging.py`**: A unified logger that handles structured logging (JSON or formatted text) to make debugging easier for both developers and production monitoring.
- **`server/api/health.py`**: Provides `/health` and `/v1/status` endpoints to monitor the application, its connection to backend providers, and overall system sanity.

### D. Centralized Proxy Engine

We will move the complex `httpx` proxying logic from the route handlers into `server/core/proxy_engine.py`. This ensures consistency in how headers, streaming, and error handling are managed.

**Benefits:**

- Endpoint handlers remain clean (only 5-10 lines of code).
- Changes to proxying logic (e.g., adding logging or retry logic) happen in one place.

### E. Dependency Injection for Providers

We will create a dependency to resolve which provider should handle a request.

**Example: `server/core/dependencies.py`**

```python
async def get_provider(request: Request, body: dict = None):
    # Logic currently in get_target_provider()
    return provider_manager.resolve(path=request.url.path, model=body.get("model"))
```

## 3. Implementation Workflow for Developers

When a developer wants to implement a new API (e.g., "Fine-Tuning"):

1. **Create** `server/api/v1/fine_tuning.py`.
2. **Define** the routes using `APIRouter`.
3. **Call** the `proxy_engine` for standard routing or implement custom logic.
4. **Register** the new router in `server/api/v1/router.py`.

## 4. Conflict Resolution

- **Standardized naming conventions**: Prevents naming collisions.
- **Isolated files**: Developers working on different OpenAI features will rarely touch the same file.
- **Centralized `main.py`**: Only updated when adding new global features or middleware.

## 5. Next Steps

1. **Initialize Directory Structure**: Create the `api/v1` and `core` folders.
2. **Extract `proxy_engine.py`**: Migrate existing logic from `routes/proxy.py`.
3. **Bootstrapping**: Create the initial `chat.py` and `models.py` as templates for other developers.
4. **Update `main.py`**: Point to the new `api/router.py`.
