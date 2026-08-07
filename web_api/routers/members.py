"""Members and invitations: who may see a class, and with what role.

Reading the member list requires a verified membership; every *change* requires
``can_manage_members`` (the owner, or an administrator while the chat is still
on Telegram-derived rights). Both are re-checked in ``application.queries``
immediately before the write — this router never decides policy itself.

Invitations are treated as credentials: the raw token is generated here, stored
only as a keyed hash, and returned exactly once in the response that creates it.
``POST /invites/accept`` deliberately does **not** require an existing
membership — that is the whole point, since an invited parent may not be in the
Telegram chat at all. Identity still comes from the verified session (and thus
from signed ``initData``); the token only says *which* class and *which* role.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from application.dto import (
    InviteAcceptDTO, InviteAcceptedDTO, InviteCreateDTO, InviteDTO,
    MemberRoleUpdateDTO, MembersPageDTO,
)
from application.queries import (
    InviteError, MemberAccessError, MemberNotFoundError, accept_invite,
    create_invite, list_invites, list_members, remove_member, revoke_invite,
    set_chat_access_mode, set_member_role,
)
from database.models import WebUser
from web_api.deps import (
    ClassContext, get_current_user, get_web_settings, require_class,
)
from web_api.security import generate_token, hash_token
from web_api.settings import WebSettings

router = APIRouter(prefix="/api/v1", tags=["members"])

_FORBIDDEN = "only the owner may manage members of this class"


def _invite_url(settings: WebSettings, token: str) -> str:
    """Deep link that opens the Mini App with the invitation attached.

    Mirrors ``handlers/web.build_launch_url``: the canonical
    ``t.me/<bot>/<app>?startapp=`` form when a Mini App short name is configured,
    and the raw frontend URL as a development fallback.
    """
    param = f"inv_{token}"
    if settings.web_app_short_name and settings.bot_username:
        return (
            f"https://t.me/{settings.bot_username}/"
            f"{settings.web_app_short_name}?startapp={param}"
        )
    sep = "&" if "?" in settings.web_app_url else "?"
    return f"{settings.web_app_url}{sep}tgWebAppStartParam={param}"


@router.get("/classes/{chat_id}/members", response_model=MembersPageDTO)
async def members(
    chat_id: int,
    ctx: ClassContext = Depends(require_class),
) -> MembersPageDTO:
    return await list_members(chat_id, ctx.caps, ctx.membership.user_id)


@router.patch("/classes/{chat_id}/members/{user_id}", response_model=MembersPageDTO)
async def update_member_role(
    chat_id: int,
    user_id: int,
    payload: MemberRoleUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> MembersPageDTO:
    try:
        return await set_member_role(
            chat_id, user_id, payload.app_role, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except MemberNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete(
    "/classes/{chat_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_member(
    chat_id: int,
    user_id: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> None:
    try:
        await remove_member(
            chat_id, user_id, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except MemberNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")


@router.put("/classes/{chat_id}/access-mode", response_model=MembersPageDTO)
async def update_access_mode(
    chat_id: int,
    mode: str,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> MembersPageDTO:
    try:
        return await set_chat_access_mode(
            chat_id, mode, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/classes/{chat_id}/invites", response_model=List[InviteDTO])
async def invites(
    chat_id: int,
    ctx: ClassContext = Depends(require_class),
) -> List[InviteDTO]:
    try:
        return await list_invites(chat_id, ctx.caps)
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)


@router.post(
    "/classes/{chat_id}/invites",
    response_model=InviteDTO,
    status_code=status.HTTP_201_CREATED,
)
async def add_invite(
    chat_id: int,
    payload: InviteCreateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
    settings: WebSettings = Depends(get_web_settings),
) -> InviteDTO:
    token = generate_token()
    try:
        invite = await create_invite(
            chat_id, payload, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
            token=token, token_hash=hash_token(settings.session_secret, token),
        )
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    # The only moment the link can ever be shown.
    invite.url = _invite_url(settings, token)
    return invite


@router.delete(
    "/classes/{chat_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_invite(
    chat_id: int,
    invite_id: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> None:
    try:
        revoked = await revoke_invite(
            chat_id, invite_id, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except MemberAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")


@router.post("/invites/accept", response_model=InviteAcceptedDTO)
async def accept(
    payload: InviteAcceptDTO,
    user: WebUser = Depends(get_current_user),
    settings: WebSettings = Depends(get_web_settings),
) -> InviteAcceptedDTO:
    """Redeem an invitation. Requires a session, not a membership."""
    token: Optional[str] = (payload.token or "").strip() or None
    if token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token is required")
    try:
        return await accept_invite(
            hash_token(settings.session_secret, token),
            user.telegram_user_id,
            user.display_name,
        )
    except InviteError as exc:
        # 400, not 403: the caller is authenticated, the *invitation* is no good.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
