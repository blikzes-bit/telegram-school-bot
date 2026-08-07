"""FastAPI application factory for the Telegram Mini App backend.

Wires the v1 routers, CORS (credentialed, since auth is cookie-based) and gates
the OpenAPI schema/docs so they are exposed only in development (or when
explicitly force-enabled). Importing this module never requires a bot token; the
token is only demanded when ``/api/v1/auth/telegram`` actually verifies initData.
"""
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web_api.ratelimit import RateLimiter
from web_api.routers import (
    audit, auth, classes, dashboard, export, extra, health, homework, members,
    payments, schedule,
)
from web_api.routers import settings as settings_router
from web_api.settings import WebSettings, get_settings


def create_app(settings: Optional[WebSettings] = None) -> FastAPI:
    settings = settings or get_settings()

    openapi_url = "/api/v1/openapi.json" if settings.openapi_enabled else None
    app = FastAPI(
        title="School Mini App API",
        version="1",
        openapi_url=openapi_url,
        docs_url="/api/docs" if settings.openapi_enabled else None,
        redoc_url="/api/redoc" if settings.openapi_enabled else None,
    )

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    for module in (
        health, auth, classes, dashboard, schedule, homework, extra,
        settings_router, audit, export, members, payments,
    ):
        app.include_router(module.router)

    # Process-local brute-force guard for the auth endpoint (single-host stage 1).
    app.state.auth_rate_limiter = RateLimiter(
        settings.auth_rate_limit, settings.auth_rate_window_seconds
    )

    return app


# ASGI entry point: ``uvicorn web_api.main:app``.
app = create_app()
