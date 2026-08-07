"""Authentication: exchange Telegram initData for an opaque web session.

``start_param`` may carry either a one-time **launch token** (minted by the bot's
``/web`` command for someone already in the chat) or an **invitation**
(``inv_<token>``, minted by an owner for someone who may not be in the chat at
all). Both ride inside the signed initData, so neither is ever read from
untrusted client state.
"""
import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

import config
import database.db as db
from application.dto import MeDTO
from application.queries import InviteError, accept_invite
from web_api.deps import (
    _is_expired, get_current_user, get_web_settings,
)
from web_api.security import (
    InitDataError, generate_token, hash_token, verify_init_data,
)
from web_api.settings import WebSettings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

logger = logging.getLogger(__name__)

# Distinguishes an invitation from a launch token in ``start_param``.
_INVITE_PREFIX = "inv_"


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

    # An invitation (``inv_<token>``) arrives the same way a launch token does —
    # inside the *signed* initData — so the token never has to be read from
    # untrusted client state. It is redeemed here, before the session exists,
    # because it is what gives this user access to the class in the first place.
    # A dead invite must not block signing in: the user still gets a session and
    # simply sees no class, which is a far better failure than a login loop.
    if verified.start_param and verified.start_param.startswith(_INVITE_PREFIX):
        raw_invite = verified.start_param[len(_INVITE_PREFIX):]
        try:
            await accept_invite(
                hash_token(settings.session_secret, raw_invite),
                verified.user_id,
                verified.display_name,
            )
        except InviteError:
            logger.info("Ignoring an unusable invitation during login")

    # A launch token (delivered via start_param) establishes / re-verifies the
    # membership that scopes this user to a class.
    elif verified.start_param:
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
