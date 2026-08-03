"""FastAPI application factory for the Telegram Mini App backend.

Wires the v1 routers, CORS (credentialed, since auth is cookie-based) and gates
the OpenAPI schema/docs so they are exposed only in development (or when
explicitly force-enabled). Importing this module never requires a bot token; the
token is only demanded when ``/api/v1/auth/telegram`` actually verifies initData.

In production the same app also serves the built frontend, so the Mini App is a
single origin: the session cookie is first-party and no CORS is involved.
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web_api.ratelimit import RateLimiter
from web_api.routers import (
    audit, auth, classes, dashboard, export, extra, health, homework, schedule,
)
from web_api.routers import settings as settings_router
from web_api.settings import WebSettings, get_settings


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA from ``dist`` alongside the API.

    Starlette's ``StaticFiles(html=True)`` is not enough on its own: it returns
    ``index.html`` only for directory URLs and answers anything else with a 404
    (or ``404.html``). React Router owns paths like ``/classes/-100/homework``,
    which must return the shell with a 200 on a hard refresh — hence the
    catch-all below.

    Registration order matters. This runs after ``include_router``, so every API
    route is matched first; the catch-all only sees what is left over.
    """
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        # An unknown API path is a bug or a probe; masking it with the SPA shell
        # would turn a clear 404 into a confusing 200 full of HTML.
        if spa_path.startswith("api/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        # Root-level build output (favicon, manifest, robots.txt) is served as
        # itself. resolve() collapses any ".." before the containment check, so
        # a crafted path cannot escape the directory.
        if spa_path:
            candidate = (dist / spa_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)

        return FileResponse(index)


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
        settings_router, audit, export,
    ):
        app.include_router(module.router)

    # Process-local brute-force guard for the auth endpoint (single-host stage 1).
    app.state.auth_rate_limiter = RateLimiter(
        settings.auth_rate_limit, settings.auth_rate_window_seconds
    )

    # Must come last: the catch-all would otherwise shadow the API routes.
    # A missing build is normal in development (Vite serves the frontend) and in
    # the tests, so the API has to come up without it.
    dist = Path(settings.web_dist_dir)
    if (dist / "index.html").is_file():
        _serve_frontend(app, dist)

    return app


# ASGI entry point: ``uvicorn web_api.main:app``.
app = create_app()
