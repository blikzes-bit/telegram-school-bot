"""FastAPI dependencies: settings, DB session, current user, class access.

Access control is enforced here, server-side, *before* any tenant data is
touched (mirrors the bot's ``middleware.access`` rule that a hidden control
protects nothing):

  * ``get_current_user`` turns the opaque session cookie into a ``WebUser`` or
    raises 401;
  * ``require_class`` turns a ``chat_id`` path parameter into a verified
    ``ClassContext`` or raises 403 — an unknown / unverified chat_id is never
    served an empty body.
"""
import datetime
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import database.db as db
from application.dto import PermissionsDTO
from database.models import Chat, ChatMembership, WebUser
from web_api.security import hash_token
from web_api.settings import WebSettings, get_settings


def get_web_settings() -> WebSettings:
    return get_settings()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` bound to the shared engine.

    Provided for endpoints/mutations that want an explicit session and a single
    transaction. The read use-cases in ``application.queries`` manage their own
    sessions, so stage-1 read routers do not depend on this.
    """
    async with db.AsyncSessionLocal() as session:
        yield session


def _parse_iso(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _is_expired(expires_at: Optional[str], now: datetime.datetime) -> bool:
    parsed = _parse_iso(expires_at)
    return parsed is None or parsed <= now


async def get_current_user(
    request: Request,
    settings: WebSettings = Depends(get_web_settings),
) -> WebUser:
    """Resolve the session cookie to a WebUser, or 401.

    Trusts nothing but the HttpOnly cookie: no query params, no request body,
    no ``initDataUnsafe``.
    """
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    session_hash = hash_token(settings.session_secret, token)
    web_session = await db.get_web_session(session_hash)
    now = datetime.datetime.now(datetime.timezone.utc)
    if web_session is None or _is_expired(web_session.expires_at, now):
        if web_session is not None:
            await db.delete_web_session(session_hash)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")

    user = await db.get_web_user(web_session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")

    # Sliding expiration: once a session is past the halfway point of its
    # lifetime, extend it on activity so active users are not logged out mid-use.
    # Best-effort — a failure here must never break an authenticated request.
    try:
        expires = _parse_iso(web_session.expires_at)
        half_ttl = settings.session_ttl_seconds / 2
        if expires is not None and (expires - now).total_seconds() < half_ttl:
            new_expires = (
                now + datetime.timedelta(seconds=settings.session_ttl_seconds)
            ).isoformat()
            await db.refresh_web_session(session_hash, now.isoformat(), new_expires)
    except Exception:  # noqa: BLE001 — hygiene only, never fail the request
        pass

    return user


def build_permissions(chat: Optional[Chat], membership: ChatMembership) -> PermissionsDTO:
    """Server-computed capabilities for this user in this class.

    Read-only in stage 1, but computed the same way the bot enforces edits so a
    future mutation endpoint can reuse it. Private chats have a single user and
    therefore no admin restriction.
    """
    is_private = getattr(chat, "chat_type", None) == "private"
    is_admin = is_private or membership.role == "admin"
    policy = getattr(chat, "hw_edit_policy", "collaborative")

    if is_private:
        can_edit_homework = True
    elif policy == "admin_only":
        can_edit_homework = is_admin
    else:  # collaborative | creator_or_admin — members may edit (their own/all)
        can_edit_homework = True

    return PermissionsDTO(
        is_admin=is_admin,
        can_edit_homework=can_edit_homework,
        can_edit_schedule=is_admin,
    )


@dataclass
class ClassContext:
    chat_id: int
    chat: Optional[Chat]
    membership: ChatMembership
    permissions: PermissionsDTO


async def require_class(
    chat_id: int,
    user: WebUser = Depends(get_current_user),
) -> ClassContext:
    """Authorise access to a class by chat_id, or 403.

    Membership is the single gate: without a verified ``ChatMembership`` row the
    request is refused, so no tenant's data can be reached by guessing a chat_id.
    """
    membership = await db.get_membership(chat_id, user.telegram_user_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you do not have access to this class",
        )
    chat = await db.get_chat(chat_id)
    permissions = build_permissions(chat, membership)
    return ClassContext(
        chat_id=chat_id, chat=chat, membership=membership, permissions=permissions
    )
