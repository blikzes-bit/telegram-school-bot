"""Authentication: exchange Telegram initData for an opaque web session."""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

import config
import database.db as db
from application.dto import MeDTO
from web_api.deps import (
    _is_expired, get_current_user, get_web_settings,
)
from web_api.security import (
    InitDataError, generate_token, hash_token, verify_init_data,
)
from web_api.settings import WebSettings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    """The raw Telegram Mini App ``initData`` query string (never initDataUnsafe)."""

    init_data: str


def _set_session_cookie(response: Response, settings: WebSettings, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/telegram", response_model=MeDTO)
async def telegram_auth(
    payload: TelegramAuthRequest,
    request: Request,
    response: Response,
    settings: WebSettings = Depends(get_web_settings),
) -> MeDTO:
    """Verify initData, optionally consume a launch token, open a web session."""
    limiter = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is not None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many authentication attempts, try again later",
            )
    bot_token = config.require_bot_token()
    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat()
    now_ts = int(now.timestamp())

    try:
        verified = verify_init_data(
            payload.init_data,
            bot_token,
            max_age_seconds=settings.initdata_max_age_seconds,
            now_ts=now_ts,
        )
    except InitDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid initData: {exc}"
        )

    user = await db.upsert_web_user(verified.user_id, verified.display_name, now_iso)

    # A launch token (delivered via start_param) establishes / re-verifies the
    # membership that scopes this user to a class.
    if verified.start_param:
        token_hash = hash_token(settings.session_secret, verified.start_param)
        launch = await db.consume_launch_token(token_hash, now_iso)
        if launch is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="launch token is invalid or already used",
            )
        if launch.telegram_user_id != verified.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="launch token belongs to another user",
            )
        if _is_expired(launch.expires_at, now):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="launch token expired"
            )
        await db.touch_membership(launch.chat_id, verified.user_id, now_iso)

    # Opaque session: cookie carries a random token, DB stores only its hash.
    raw_token = generate_token()
    session_hash = hash_token(settings.session_secret, raw_token)
    expires_iso = (
        now + datetime.timedelta(seconds=settings.session_ttl_seconds)
    ).isoformat()
    await db.create_web_session(session_hash, verified.user_id, now_iso, expires_iso)
    _set_session_cookie(response, settings, raw_token)

    return MeDTO(
        telegram_user_id=user.telegram_user_id, display_name=user.display_name
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: WebSettings = Depends(get_web_settings),
) -> Response:
    """Invalidate the current session and clear the cookie."""
    token = request.cookies.get(settings.cookie_name)
    if token:
        await db.delete_web_session(hash_token(settings.session_secret, token))
    response.delete_cookie(key=settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    user=Depends(get_current_user),
    settings: WebSettings = Depends(get_web_settings),
) -> Response:
    """Invalidate every session of the current user ("log out everywhere")."""
    await db.delete_web_sessions_for_user(user.telegram_user_id)
    response.delete_cookie(key=settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
async def auth_me(user=Depends(get_current_user)) -> MeDTO:
    """Convenience mirror of GET /api/v1/me under the auth prefix."""
    return MeDTO(telegram_user_id=user.telegram_user_id, display_name=user.display_name)
