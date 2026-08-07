"""Payments: what has to be paid for and when (the tutor profile's money side).

Reading is open to any member — everybody who is being asked to pay should be
able to see what and when. Every change requires ``can_edit_payments`` (owner or
editor), re-checked in ``application.queries`` before the write.

Amounts are integers in minor units on the wire as well as in the database;
formatting happens once, server-side, so no client ever does money arithmetic.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

import services.timeservice as ts
from application.dto import (
    PaymentCreateDTO, PaymentDTO, PaymentPaidDTO, PaymentUpdateDTO,
)
from application.queries import (
    PaymentAccessError, create_payment, edit_payment, list_payments,
    mark_payment_paid, remove_payment,
)
from database.models import WebUser
from web_api.deps import ClassContext, get_current_user, require_class

router = APIRouter(prefix="/api/v1/classes", tags=["payments"])

_FORBIDDEN = "you may not change payments in this class"


@router.get("/{chat_id}/payments", response_model=List[PaymentDTO])
async def payments(
    chat_id: int,
    unpaid: bool = Query(default=False, description="only entries not yet paid"),
    ctx: ClassContext = Depends(require_class),
) -> List[PaymentDTO]:
    today = await ts.today_for_chat_id(chat_id)
    return await list_payments(chat_id, today, ctx.caps, only_unpaid=unpaid)


@router.post(
    "/{chat_id}/payments", response_model=PaymentDTO, status_code=status.HTTP_201_CREATED
)
async def add_payment_endpoint(
    chat_id: int,
    payload: PaymentCreateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> PaymentDTO:
    today = await ts.today_for_chat_id(chat_id)
    try:
        return await create_payment(
            chat_id, payload, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except PaymentAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)


@router.patch("/{chat_id}/payments/{payment_id}", response_model=PaymentDTO)
async def edit_payment_endpoint(
    chat_id: int,
    payment_id: int,
    payload: PaymentUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> PaymentDTO:
    today = await ts.today_for_chat_id(chat_id)
    try:
        result = await edit_payment(
            chat_id, payment_id, payload, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except PaymentAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="payment not found")
    return result


@router.patch("/{chat_id}/payments/{payment_id}/paid", response_model=PaymentDTO)
async def mark_paid_endpoint(
    chat_id: int,
    payment_id: int,
    payload: PaymentPaidDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> PaymentDTO:
    today = await ts.today_for_chat_id(chat_id)
    try:
        result = await mark_payment_paid(
            chat_id, payment_id, payload.is_paid, today, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except PaymentAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="payment not found")
    return result


@router.delete(
    "/{chat_id}/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_payment_endpoint(
    chat_id: int,
    payment_id: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> None:
    try:
        deleted = await remove_payment(
            chat_id, payment_id, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except PaymentAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="payment not found")
