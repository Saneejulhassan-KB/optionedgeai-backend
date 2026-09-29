"""
OptionEdgeAI Backend — Application Entry Point
==============================================

Why this file exists
--------------------
Every FastAPI project needs one place that:
  1. Creates the FastAPI application object
  2. Registers middleware (CORS) and routes
  3. Initializes the database on startup
  4. Is pointed at by Uvicorn:  uvicorn app.main:app

Architecture note
-----------------
Keep main.py thin. Business logic belongs in services/, not here.
Phase 5.1 mounts market_router (/market/quote).
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.routes import auth_router, market_router, market_v1_router
from app.services.upstox_http import shutdown_upstox_http, startup_upstox_http
from app.websocket import market_ws_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    Runs once when Uvicorn starts (before requests) and on shutdown.

    Phase 3: create SQLite tables if they do not exist.
    Shared Upstox HTTP client + concurrency cap keeps /health responsive
    when Flutter opens many candle/quote requests.
    """
    init_db()
    await startup_upstox_http(max_concurrent=6)
    try:
        yield
    finally:
        await shutdown_upstox_http()


app: FastAPI = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.app_debug,
    lifespan=lifespan,
    description=(
        "Production backend for the OptionEdgeAI Flutter trading app. "
        "This API is the only surface Flutter talks to; Upstox secrets stay here."
    ),
)

_origins = settings.cors_origin_list
_allow_credentials = _origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Feature routers
app.include_router(auth_router)
app.include_router(market_router)
app.include_router(market_v1_router)
app.include_router(market_ws_router)


@app.get(
    "/health",
    tags=["System"],
    summary="Liveness check",
)
def health_check() -> dict[str, str]:
    """Liveness check for Flutter / Docker / load balancers."""
    return {"status": "ok"}


@app.get(
    "/version",
    tags=["System"],
    summary="API version",
)
def get_version() -> dict[str, str]:
    """Returns name, version, and environment from Settings."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
    }
