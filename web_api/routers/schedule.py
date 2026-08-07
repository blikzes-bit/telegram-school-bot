"""Schedule: the effective schedule to read, and the template/overrides to edit.

Three distinct things live here, and keeping them apart is the point:

  * ``GET /schedule`` — the **effective** schedule for real dates: weekly
    template + A/B week + per-date overrides, already resolved. What you *see*.
  * ``/schedule/template`` — the **weekly template** and the bell times. What you
    *edit* for every week.
  * ``/overrides/{date}`` — changes for **one date only**: a free day, a
    cancelled lesson, a substitution, a new time. The template is untouched.

Reading is open to any member; every change requires ``can_edit_schedule``
(re-checked in ``application.queries``), which in role mode means the owner.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import services.timeservice as ts
from application.dto import (
    DateOverridesDTO, DayOverrideUpdateDTO, LessonOverrideUpdateDTO,
    LessonSlotsUpdateDTO, ScheduleDayUpdateDTO, ScheduleRangeDTO,
    ScheduleTemplateDTO,
)
from application.queries import (
    ScheduleAccessError, build_schedule_range, clear_all_date_overrides,
    clear_lesson_change, get_date_overrides, get_schedule_template,
    set_day_type, set_lesson_change, update_lesson_slots, update_schedule_day,
)
from database.models import WebUser
from web_api.deps import ClassContext, get_current_user, require_class
from web_api.params import parse_date_param

router = APIRouter(prefix="/api/v1/classes", tags=["schedule"])

# Default window when the caller does not pass from/to: two weeks starting today.
_DEFAULT_SPAN_DAYS = 13

_FORBIDDEN = "only the owner may change the schedule of this class"


def _required_date(raw: str) -> datetime.date:
    parsed = parse_date_param(raw, "date")
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="date is required (YYYY-MM-DD)"
        )
    return parsed


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


# --- The weekly template (bell times + subjects) ------------------------------

@router.get("/{chat_id}/schedule/template", response_model=ScheduleTemplateDTO)
async def schedule_template(
    chat_id: int,
    week_type: Optional[str] = Query(default=None, description="all | A | B"),
    ctx: ClassContext = Depends(require_class),
) -> ScheduleTemplateDTO:
    return await get_schedule_template(chat_id, week_type, ctx.caps)


@router.put("/{chat_id}/schedule/slots", response_model=ScheduleTemplateDTO)
async def put_lesson_slots(
    chat_id: int,
    payload: LessonSlotsUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> ScheduleTemplateDTO:
    try:
        return await update_lesson_slots(
            chat_id, payload, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)


@router.put(
    "/{chat_id}/schedule/template/{weekday}", response_model=ScheduleTemplateDTO
)
async def put_schedule_day(
    chat_id: int,
    weekday: int,
    payload: ScheduleDayUpdateDTO,
    week_type: Optional[str] = Query(default=None, description="all | A | B"),
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> ScheduleTemplateDTO:
    try:
        return await update_schedule_day(
            chat_id, weekday, week_type, payload, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# --- Per-date changes ---------------------------------------------------------

@router.get("/{chat_id}/overrides/{date}", response_model=DateOverridesDTO)
async def date_overrides(
    chat_id: int,
    date: str,
    ctx: ClassContext = Depends(require_class),
) -> DateOverridesDTO:
    return await get_date_overrides(chat_id, _required_date(date), ctx.caps)


@router.put("/{chat_id}/overrides/{date}", response_model=DateOverridesDTO)
async def put_day_override(
    chat_id: int,
    date: str,
    payload: DayOverrideUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> DateOverridesDTO:
    try:
        return await set_day_type(
            chat_id, _required_date(date), payload, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/{chat_id}/overrides/{date}", response_model=DateOverridesDTO)
async def delete_all_overrides(
    chat_id: int,
    date: str,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> DateOverridesDTO:
    try:
        return await clear_all_date_overrides(
            chat_id, _required_date(date), ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)


@router.put(
    "/{chat_id}/overrides/{date}/lessons/{lesson_number}",
    response_model=DateOverridesDTO,
)
async def put_lesson_override(
    chat_id: int,
    date: str,
    lesson_number: int,
    payload: LessonOverrideUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> DateOverridesDTO:
    try:
        return await set_lesson_change(
            chat_id, _required_date(date), lesson_number, payload, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete(
    "/{chat_id}/overrides/{date}/lessons/{lesson_number}",
    response_model=DateOverridesDTO,
)
async def delete_lesson_override_endpoint(
    chat_id: int,
    date: str,
    lesson_number: int,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> DateOverridesDTO:
    try:
        return await clear_lesson_change(
            chat_id, _required_date(date), lesson_number, ctx.caps,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except ScheduleAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
