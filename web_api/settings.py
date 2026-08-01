"""Typed configuration for the web API.

Reads the environment once into an immutable ``WebSettings``. Kept separate from
``config.py`` (the bot's config) so the two adapters can evolve independently;
both, however, share the same ``DATABASE_URL`` via ``config`` so the bot and API
operate on one database.
"""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer number of seconds")


def _split_origins(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class WebSettings:
    app_env: str
    web_app_url: str
    web_app_short_name: str
    session_secret: str
    session_ttl_seconds: int
    launch_token_ttl_seconds: int
    initdata_max_age_seconds: int
    cookie_name: str
    allowed_origins: List[str] = field(default_factory=list)
    auth_rate_limit: int = 20
    auth_rate_window_seconds: int = 60

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cookie_secure(self) -> bool:
        # HTTPS-only cookies in production; allowed over http on localhost in dev.
        return self.is_production

    @property
    def openapi_enabled(self) -> bool:
        # OpenAPI/Swagger is exposed only in development, unless explicitly
        # force-enabled — never left open by default in production.
        if os.getenv("WEB_ENABLE_OPENAPI", "").lower() in ("1", "true", "yes"):
            return True
        return not self.is_production


def load_settings() -> WebSettings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env not in ("development", "production"):
        raise ValueError("APP_ENV must be 'development' or 'production'")

    session_secret = os.getenv("SESSION_SECRET", "")
    if not session_secret:
        if app_env == "production":
            raise ValueError("SESSION_SECRET is required when APP_ENV=production")
        # Deterministic, clearly-non-secret default for local development only.
        session_secret = "dev-insecure-session-secret-change-me"

    return WebSettings(
        app_env=app_env,
        web_app_url=os.getenv("WEB_APP_URL", "http://localhost:5173"),
        web_app_short_name=os.getenv("WEB_APP_SHORT_NAME", ""),
        session_secret=session_secret,
        session_ttl_seconds=_get_int("SESSION_TTL", 7 * 24 * 3600),
        launch_token_ttl_seconds=_get_int("LAUNCH_TOKEN_TTL", 600),
        initdata_max_age_seconds=_get_int("INITDATA_MAX_AGE", 24 * 3600),
        cookie_name=os.getenv("SESSION_COOKIE_NAME", "school_web_session"),
        allowed_origins=_split_origins(
            os.getenv("WEB_ALLOWED_ORIGINS", "http://localhost:5173")
        ),
        auth_rate_limit=_get_int("AUTH_RATE_LIMIT", 20),
        auth_rate_window_seconds=_get_int("AUTH_RATE_WINDOW", 60),
    )


@lru_cache(maxsize=1)
def get_settings() -> WebSettings:
    return load_settings()
