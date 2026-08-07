"""Homework: list (filtered by status), add, edit, delete, toggle completion.

Adding homework is unrestricted for any class member (mirrors the bot's
"collaborative list" rule). Every change to an *existing* entry — completing,
editing, deleting — is gated by the chat's ``hw_edit_policy``, enforced
server-side in ``application.queries`` (``_require_homework_edit``) immediately
before the write. A hidden button is only a UI nicety, never the actual guard.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import services.timeservice as ts
from application.dto import (
    HomeworkCompleteDTO, HomeworkCreateDTO, HomeworkDTO, HomeworkUpdateDTO,
)
from application.queries import (
    HomeworkAccessError, create_homework, edit_homework, list_homework,
    remove_homework, set_homework_completed,
)
from database.models import WebUser
from web_api.deps import ClassContext, get_current_user, require_class

router = APIRouter(prefix="/api/v1/classes", tags=["homework"])

_ALLOWED_STATUSES = {"active", "completed", "overdue"}


@router.get("/{chat_id}/homework", response_model=List[HomeworkDTO])
async def homework(
    chat_id: int,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    ctx: ClassContext = Depends(require_class),
) -> List[HomeworkDTO]:
    if status_filter is not None and status_filter not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be one of: active, completed, overdue",
        )
    today = await ts.today_for_chat_id(chat_id)
    return await list_homework(
        chat_id, status_filter, today,
        caps=ctx.caps, user_id=ctx.membership.user_id,
    )


@router.post(
    "/{chat_id}/homework", response_model=HomeworkDTO, status_code=status.HTTP_201_CREATED
)
async def add_homework_endpoint(
    chat_id: int,
    payload: HomeworkCreateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> HomeworkDTO:
    if not payload.subject_name.strip() or not payload.description.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject_name and description are required",
        )
    today = await ts.today_for_chat_id(chat_id)
    try:
        return await create_homework(
            chat_id, payload, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except HomeworkAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you may not add homework in this class",
        )


@router.patch("/{chat_id}/homework/{homework_id}", response_model=HomeworkDTO)
async def edit_homework_endpoint(
    chat_id: int,
    homework_id: int,
    payload: HomeworkUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> HomeworkDTO:
    today = await ts.today_for_chat_id(chat_id)
    try:
        result = await edit_homework(
            chat_id, homework_id, payload, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except HomeworkAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you may not change this homework entry",
        )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="homework not found")
    return result


@router.delete("/{chat_id}/homework/{homework_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_homework_endpoint(
    chat_id: int,
    homework_id: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> None:
    try:
        deleted = await remove_homework(
            chat_id, homework_id, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except HomeworkAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you may not delete this homework entry",
        )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="homework not found")


@router.patch("/{chat_id}/homework/{homework_id}/complete", response_model=HomeworkDTO)
async def complete_homework_endpoint(
    chat_id: int,
    homework_id: int,
    payload: HomeworkCompleteDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> HomeworkDTO:
    today = await ts.today_for_chat_id(chat_id)
    try:
        result = await set_homework_completed(
            chat_id, homework_id, payload.is_completed, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except HomeworkAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you may not change this homework entry",
        )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="homework not found")
    return result
