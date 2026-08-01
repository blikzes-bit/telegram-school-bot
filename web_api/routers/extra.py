"""Extra activities: list over a date range, add, edit, delete.

Adding/editing/deleting mirrors the bot's rule (``handlers/extra.py``): admins
only in a group/supergroup, anyone in a private chat. ``ClassContext.permissions
.is_admin`` already encodes exactly that (``True`` for private chats), so it is
the single gate reused here — enforced in ``application.queries``, not just by
hiding a button.
"""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import services.timeservice as ts
from application.dto import (
    ExtraActivityCreateDTO, ExtraActivityDTO, ExtraActivityUpdateDTO,
)
from application.queries import (
    ExtraActivityAccessError, create_extra_activity, edit_extra_activity,
    list_extra, remove_extra_activity,
)
from database.models import WebUser
from web_api.deps import ClassContext, get_current_user, require_class
from web_api.params import parse_date_param

router = APIRouter(prefix="/api/v1/classes", tags=["extra"])

_DEFAULT_SPAN_DAYS = 13


@router.get("/{chat_id}/extra", response_model=List[ExtraActivityDTO])
async def extra(
    chat_id: int,
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    ctx: ClassContext = Depends(require_class),
) -> List[ExtraActivityDTO]:
    start = parse_date_param(from_, "from")
    end = parse_date_param(to, "to")
    if start is None:
        start = await ts.today_for_chat_id(chat_id)
    if end is None:
        end = start + datetime.timedelta(days=_DEFAULT_SPAN_DAYS)
    return await list_extra(chat_id, start, end, is_admin=ctx.permissions.is_admin)


@router.post(
    "/{chat_id}/extra", response_model=ExtraActivityDTO, status_code=status.HTTP_201_CREATED
)
async def add_extra_endpoint(
    chat_id: int,
    payload: ExtraActivityCreateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> ExtraActivityDTO:
    try:
        return await create_extra_activity(
            chat_id, payload, ctx.permissions.is_admin,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ExtraActivityAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may add extra activities here",
        )


@router.patch("/{chat_id}/extra/{activity_id}", response_model=ExtraActivityDTO)
async def edit_extra_endpoint(
    chat_id: int,
    activity_id: int,
    payload: ExtraActivityUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> ExtraActivityDTO:
    try:
        result = await edit_extra_activity(
            chat_id, activity_id, payload, ctx.permissions.is_admin,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ExtraActivityAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may edit extra activities here",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="activity not found")
    return result


@router.delete("/{chat_id}/extra/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extra_endpoint(
    chat_id: int,
    activity_id: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> None:
    try:
        deleted = await remove_extra_activity(
            chat_id, activity_id, ctx.permissions.is_admin,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ExtraActivityAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may delete extra activities here",
        )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="activity not found")
