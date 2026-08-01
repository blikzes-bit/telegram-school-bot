"""Schedule: effective schedule over a date range."""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

import services.timeservice as ts
from application.dto import ScheduleRangeDTO
from application.queries import build_schedule_range
from web_api.deps import ClassContext, require_class
from web_api.params import parse_date_param

router = APIRouter(prefix="/api/v1/classes", tags=["schedule"])

# Default window when the caller does not pass from/to: two weeks starting today.
_DEFAULT_SPAN_DAYS = 13


@router.get("/{chat_id}/schedule", response_model=ScheduleRangeDTO)
async def schedule(
    chat_id: int,
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    ctx: ClassContext = Depends(require_class),
) -> ScheduleRangeDTO:
    start = parse_date_param(from_, "from")
    end = parse_date_param(to, "to")
    if start is None:
        start = await ts.today_for_chat_id(chat_id)
    if end is None:
        end = start + datetime.timedelta(days=_DEFAULT_SPAN_DAYS)
    return await build_schedule_range(chat_id, start, end)
