"""
main.py
=======
FastAPI application entry point with lifespan management, middleware, and router wiring.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import get_settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler — startup and shutdown hooks.

    Startup:
    1. Configure telemetry (OTEL + structlog).
    2. Initialise the database engine and session factory.
    3. Connect the queue manager.
    4. Connect the lock manager.

    Shutdown:
    1. Close the lock manager.
    2. Close the queue manager.
    3. Close the database engine.
    """
    settings = get_settings()

    import time
    app.state.startup_time = time.time()

    # --- Startup ---
    logger.info("app.startup", env=settings.app_env)

    # 1. Telemetry
    try:
        from src.infrastructure.telemetry import configure_telemetry

        configure_telemetry(settings)
    except Exception:
        logger.exception("app.startup.telemetry_failed")

    # 2. Database
    try:
        from src.infrastructure.database import create_engine

        create_engine(settings)
    except Exception:
        logger.exception("app.startup.database_failed")

    # 3. Queue (Redis Streams)
    queue_manager = None
    try:
        from src.infrastructure.queue import QueueManager

        queue_manager = QueueManager(
            redis_url=settings.redis.url,
            max_connections=settings.redis.max_connections,
        )
        await queue_manager.connect()
        app.state.queue_manager = queue_manager
    except Exception:
        logger.exception("app.startup.queue_failed")

    # 4. Lock manager
    lock_manager = None
    try:
        from src.infrastructure.locks import LockManager

        lock_manager = LockManager(redis_url=settings.redis.url)
        await lock_manager.connect()
        app.state.lock_manager = lock_manager
    except Exception:
        logger.exception("app.startup.lock_manager_failed")

    logger.info("app.startup.complete")

    yield

    # --- Shutdown ---
    logger.info("app.shutdown")

    if lock_manager:
        await lock_manager.close()
    if queue_manager:
        await queue_manager.close()

    try:
        from src.infrastructure.database import _engine

        if _engine:
            await _engine.dispose()
    except Exception:
        logger.exception("app.shutdown.database_close_failed")

    logger.info("app.shutdown.complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="OrchaDeck — Multi-Agent Tool Ecosystem",
        description=(
            "Production-oriented dynamic multi-agent tool ecosystem and orchestration platform. "
            "Accepts natural-language requests, decomposes them into task graphs, "
            "dynamically spawns specialised agents, and returns verified results."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
    )

    # --- CORS ---
    cors_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Correlation ID middleware ---
    try:
        from src.infrastructure.telemetry import CorrelationIdMiddleware

        app.add_middleware(CorrelationIdMiddleware)
    except ImportError:
        pass

    # --- RFC 9457 error handlers ---
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=str(request.url))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected error occurred.",
            },
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Any) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "type": "about:blank",
                "title": "Not Found",
                "status": 404,
                "detail": f"Path '{request.url.path}' not found.",
            },
        )

    # --- Routers ---
    from src.api.routes.health import router as health_router
    from src.api.routes.requests import router as requests_router

    app.include_router(health_router)
    app.include_router(requests_router)

    # Optional routers — fail gracefully if not yet implemented
    try:
        from src.api.routes.tools import router as tools_router

        app.include_router(tools_router)
    except (ImportError, Exception):
        pass

    try:
        from src.api.routes.agents import router as agents_router

        app.include_router(agents_router)
    except (ImportError, Exception):
        pass

    try:
        from src.api.routes.memory import router as memory_router

        app.include_router(memory_router)
    except (ImportError, Exception):
        pass

    try:
        from src.api.routes.github_tools import router as github_tools_router

        app.include_router(github_tools_router)
    except (ImportError, Exception) as e:
        logger.error(f"Failed to load github_tools_router: {e}")

    try:
        from src.api.routes.tasks import router as tasks_router

        app.include_router(tasks_router)
    except (ImportError, Exception) as e:
        logger.error(f"Failed to load tasks_router: {e}")

    try:
        from src.api.routes.platform import router as platform_router
        
        app.include_router(platform_router)
    except (ImportError, Exception) as e:
        logger.error(f"Failed to load platform_router: {e}")

    try:
        from src.api.routes.conversations import router as conversations_router

        app.include_router(conversations_router)
    except (ImportError, Exception) as e:
        logger.error(f"Failed to load conversations_router: {e}")

    # Dashboard UI — serves the web interface at / and /dashboard
    try:
        from src.api.routes.ui import router as ui_router

        app.include_router(ui_router)
    except (ImportError, Exception):
        pass

    return app


# Module-level app instance for uvicorn
app = create_app()
