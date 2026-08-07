"""Dashboard: the class's "Today" screen (mirror of the bot's /today)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

import services.timeservice as ts
from application.dto import TodayDTO
from application.queries import build_today
from web_api.deps import ClassContext, require_class
from web_api.params import parse_date_param

router = APIRouter(prefix="/api/v1/classes", tags=["dashboard"])


@router.get("/{chat_id}/today", response_model=TodayDTO)
async def today(
    chat_id: int,
    date: Optional[str] = Query(default=None, description="ISO date; defaults to the class's today"),
    ctx: ClassContext = Depends(require_class),
) -> TodayDTO:
    the_date = parse_date_param(date, "date")
    if the_date is None:
        the_date = await ts.today_for_chat_id(chat_id)
    return await build_today(
        chat_id, the_date, ctx.permissions, ctx.caps, user_id=ctx.membership.user_id
    )
