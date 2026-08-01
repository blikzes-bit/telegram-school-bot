"""Shared helpers for the web API tests.

Provides:
  * ``make_init_data`` — build a *correctly signed* Telegram Mini App initData
    query string (or a tampered one) using the dummy BOT_TOKEN from conftest;
  * ``build_test_settings`` — a fixed ``WebSettings`` (known session secret,
    development env) injected via the ``get_web_settings`` dependency override;
  * ``web_client`` — an httpx ``AsyncClient`` bound to the FastAPI app through
    an in-process ASGI transport, sharing the same in-memory DB the ``db``
    fixture patches in.
"""
import contextlib
import datetime
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
from httpx import ASGITransport

import config
from web_api.deps import get_web_settings
from web_api.main import create_app
from web_api.settings import WebSettings

BOT_TOKEN = config.BOT_TOKEN
TEST_SESSION_SECRET = "test-fixed-session-secret"


def build_test_settings(**overrides) -> WebSettings:
    base = dict(
        app_env="development",
        web_app_url="http://localhost:5173",
        web_app_short_name="schoolapp",
        session_secret=TEST_SESSION_SECRET,
        session_ttl_seconds=3600,
        launch_token_ttl_seconds=600,
        initdata_max_age_seconds=24 * 3600,
        cookie_name="school_web_session",
        allowed_origins=[],
    )
    base.update(overrides)
    return WebSettings(**base)


def make_init_data(
    user_id: int,
    *,
    bot_token: str = BOT_TOKEN,
    first_name: str = "Test",
    last_name: str | None = None,
    auth_date: int | None = None,
    start_param: str | None = None,
    tamper: bool = False,
) -> str:
    """Return a urlencoded initData string signed with ``bot_token``.

    When ``tamper`` is set, the signature is left intact but a field is mutated
    afterwards so verification must fail.
    """
    if auth_date is None:
        auth_date = int(time.time())
    user: dict = {"id": user_id, "first_name": first_name}
    if last_name:
        user["last_name"] = last_name
    data = {
        "auth_date": str(int(auth_date)),
        "user": json.dumps(user, separators=(",", ":")),
    }
    if start_param is not None:
        data["start_param"] = start_param

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if tamper:
        # Change a signed field without re-signing -> hash no longer matches.
        data["auth_date"] = str(int(data["auth_date"]) + 1)

    return urlencode(data)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def iso_in(seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=seconds)
    ).isoformat()


@contextlib.asynccontextmanager
async def web_client(settings: WebSettings | None = None):
    """Yield ``(client, settings)`` for an ASGI-backed test client."""
    settings = settings or build_test_settings()
    app = create_app(settings=settings)
    app.dependency_overrides[get_web_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client, settings


async def authenticate(client: httpx.AsyncClient, user_id: int, **kwargs):
    """POST /auth/telegram with signed initData; returns the raw response."""
    init_data = make_init_data(user_id, **kwargs)
    return await client.post("/api/v1/auth/telegram", json={"init_data": init_data})
