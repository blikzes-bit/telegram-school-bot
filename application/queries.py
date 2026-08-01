"""Query use-cases shared by the Telegram bot and the web API.

This module is the single place that turns domain objects into API DTOs. It
reuses the *same* schedule logic the bot uses — ``services.effective_schedule``
(weekly template + A/B weeks + per-date overrides) and
``services.extra_activities`` — so the web dashboard can never drift from the
bot's ``/today``. It imports services and ``database.db`` only; it must never
import anything from ``handlers/`` (the Telegram adapter).
"""
import datetime
from typing import Any, List, Optional, Tuple

from application.dto import (
    AuditEntryDTO, AuditPageDTO, DayScheduleDTO, ExtraActivityCreateDTO,
    ExtraActivityDTO, ExtraActivityUpdateDTO, HomeworkCreateDTO, HomeworkDTO,
    LessonDTO, PermissionsDTO, ReminderSettingsDTO, ReminderSettingsUpdateDTO,
    ScheduleRangeDTO, TodayDTO,
)
from database.db import (
    add_extra_activity, add_homework, count_audit_logs, delete_extra_activity,
    get_audit_logs, get_chat, get_extra_activities, get_extra_activity_by_id,
    get_homework, get_homework_by_id, mark_homework_completed,
    set_quiet_hours, set_reminder_category_enabled, update_chat_reminder_times,
    update_duetoday_time, update_extra_activity,
)
from database.models import Chat, ExtraActivity, Homework
from services import audit
from services.effective_schedule import (
    EffectiveDay, EffectiveLesson, get_effective_day, resolve_week_type_for_chat,
)
from services.extra_activities import activities_on_date
from services.permissions import can_edit_homework_sync

# Kept identical to handlers/today.UPCOMING_LIMIT so the web dashboard shows the
# same "quick glance" horizon as the bot's /today screen.
UPCOMING_LIMIT = 5

# Hard cap on a schedule range so a single request can't scan an unbounded span.
MAX_RANGE_DAYS = 62


# --- Mappers (ORM/domain -> DTO) --------------------------------------------

def _lesson_to_dto(lesson: EffectiveLesson) -> LessonDTO:
    return LessonDTO(
        lesson_number=lesson.lesson_number,
        start_time=lesson.start_time,
        end_time=lesson.end_time,
        subject_name=lesson.subject_name,
        cancelled=lesson.cancelled,
        added=lesson.added,
        time_changed=lesson.time_changed,
        subject_changed=lesson.subject_changed,
        note=lesson.note,
    )


def _extra_to_dto(a: ExtraActivity, *, is_admin: bool = False) -> ExtraActivityDTO:
    return ExtraActivityDTO(
        id=a.id,
        title=a.title,
        kind=a.kind,
        day_of_week=a.day_of_week,
        activity_date=a.activity_date,
        start_time=a.start_time,
        end_time=a.end_time,
        location=a.location,
        note=a.note,
        can_edit=is_admin,
    )


def _homework_status(hw: Homework, today: datetime.date) -> str:
    if hw.is_completed:
        return "completed"
    if hw.due_date < today:
        return "overdue"
    return "active"


def _homework_to_dto(
    hw: Homework,
    today: datetime.date,
    *,
    chat: Optional[Chat],
    is_admin: bool,
    user_id: Optional[int],
) -> HomeworkDTO:
    is_private = getattr(chat, "chat_type", None) == "private"
    policy = getattr(chat, "hw_edit_policy", None)
    return HomeworkDTO(
        id=hw.id,
        subject_name=hw.subject_name,
        due_date=hw.due_date,
        description=hw.description,
        is_completed=hw.is_completed,
        status=_homework_status(hw, today),
        can_edit=can_edit_homework_sync(
            is_private=is_private,
            is_admin=is_admin,
            policy=policy,
            author_id=hw.created_by_user_id,
            user_id=user_id,
        ),
    )


# --- Shared homework bucketing (also used by handlers/today.py) --------------

def bucket_homework(
    incomplete: List[Homework], today: datetime.date
) -> Tuple[List[Homework], List[Homework], List[Homework]]:
    """Split incomplete homework into (due-today, overdue, upcoming).

    ``upcoming`` is sorted by due date and capped at ``UPCOMING_LIMIT`` so the
    dashboard stays a glance rather than a full list — exactly the bot's rule.
    """
    homework_today = [hw for hw in incomplete if hw.due_date == today]
    overdue = sorted(
        (hw for hw in incomplete if hw.due_date < today), key=lambda hw: hw.due_date
    )
    upcoming = sorted(
        (hw for hw in incomplete if hw.due_date > today), key=lambda hw: hw.due_date
    )[:UPCOMING_LIMIT]
    return homework_today, overdue, upcoming


def _day_schedule_dto(
    eff: EffectiveDay, week_type: str, extra: List[ExtraActivity]
) -> DayScheduleDTO:
    return DayScheduleDTO(
        date=eff.date,
        weekday=eff.weekday,
        week_type=week_type,
        day_type=eff.day_type,
        day_note=eff.day_note,
        lessons=[_lesson_to_dto(lesson) for lesson in eff.lessons],
        extra=[_extra_to_dto(a) for a in extra],
    )


# --- Use-cases --------------------------------------------------------------

async def build_today(
    chat_id: int,
    date: datetime.date,
    permissions: PermissionsDTO,
    user_id: Optional[int] = None,
) -> TodayDTO:
    """The dashboard payload: effective schedule + homework buckets + extras.

    Mirrors handlers/today.get_today_data one-for-one (same effective schedule,
    same homework buckets, same upcoming cap), only shaped as DTOs.
    """
    chat = await get_chat(chat_id)
    tz_name = getattr(chat, "timezone", None) or ""

    effective = await get_effective_day(chat_id, date)
    week_type = await resolve_week_type_for_chat(chat_id, date)
    incomplete = await get_homework(chat_id, is_completed=False)
    extra = activities_on_date(await get_extra_activities(chat_id), date)

    homework_today, overdue, upcoming = bucket_homework(incomplete, date)

    def _dto(hw: Homework) -> HomeworkDTO:
        return _homework_to_dto(
            hw, date, chat=chat, is_admin=permissions.is_admin, user_id=user_id
        )

    return TodayDTO(
        date=date,
        timezone=tz_name,
        weekday=effective.weekday,
        week_type=week_type,
        day_type=effective.day_type,
        day_note=effective.day_note,
        lessons=[_lesson_to_dto(lesson) for lesson in effective.lessons],
        extra=[_extra_to_dto(a, is_admin=permissions.is_admin) for a in extra],
        homework_today=[_dto(hw) for hw in homework_today],
        overdue=[_dto(hw) for hw in overdue],
        upcoming=[_dto(hw) for hw in upcoming],
        permissions=permissions,
    )


async def build_schedule_range(
    chat_id: int, from_date: datetime.date, to_date: datetime.date
) -> ScheduleRangeDTO:
    """Effective schedule for each day in [from_date, to_date] (inclusive)."""
    if to_date < from_date:
        from_date, to_date = to_date, from_date
    span = (to_date - from_date).days + 1
    if span > MAX_RANGE_DAYS:
        to_date = from_date + datetime.timedelta(days=MAX_RANGE_DAYS - 1)

    chat = await get_chat(chat_id)
    tz_name = getattr(chat, "timezone", None) or ""
    all_extra = await get_extra_activities(chat_id)

    days: List[DayScheduleDTO] = []
    cursor = from_date
    while cursor <= to_date:
        effective = await get_effective_day(chat_id, cursor)
        week_type = await resolve_week_type_for_chat(chat_id, cursor)
        extra = activities_on_date(all_extra, cursor)
        days.append(_day_schedule_dto(effective, week_type, extra))
        cursor += datetime.timedelta(days=1)

    return ScheduleRangeDTO(
        from_date=from_date, to_date=to_date, timezone=tz_name, days=days
    )


async def list_homework(
    chat_id: int,
    status: Optional[str],
    today: datetime.date,
    is_admin: bool = False,
    user_id: Optional[int] = None,
) -> List[HomeworkDTO]:
    """Homework for a class filtered by status (active|completed|overdue)."""
    if status == "completed":
        rows = await get_homework(chat_id, is_completed=True)
    elif status in ("active", "overdue"):
        incomplete = await get_homework(chat_id, is_completed=False)
        if status == "overdue":
            rows = [hw for hw in incomplete if hw.due_date < today]
        else:
            rows = [hw for hw in incomplete if hw.due_date >= today]
    else:
        rows = await get_homework(chat_id)

    rows = sorted(rows, key=lambda hw: hw.due_date)
    chat = await get_chat(chat_id)
    return [
        _homework_to_dto(hw, today, chat=chat, is_admin=is_admin, user_id=user_id)
        for hw in rows
    ]


async def create_homework(
    chat_id: int,
    payload: HomeworkCreateDTO,
    today: datetime.date,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> HomeworkDTO:
    """Add homework on behalf of a web user. Unrestricted for any class member,
    exactly like the bot (only *editing* an existing entry is policy-gated)."""
    hw = await add_homework(
        chat_id,
        payload.subject_name,
        payload.due_date,
        payload.description,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=hw.id, summary=audit.summarize(hw.subject_name, str(hw.due_date)),
    )
    chat = await get_chat(chat_id)
    return _homework_to_dto(hw, today, chat=chat, is_admin=is_admin, user_id=actor_user_id)


class HomeworkAccessError(Exception):
    """Raised by :func:`set_homework_completed` when the actor may not edit this entry."""


async def set_homework_completed(
    chat_id: int,
    homework_id: int,
    is_completed: bool,
    today: datetime.date,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[HomeworkDTO]:
    """Toggle completion, enforcing the chat's ``hw_edit_policy`` server-side.

    Returns ``None`` if the entry does not belong to this chat (caller -> 404).
    Raises :class:`HomeworkAccessError` if the policy forbids this actor
    (caller -> 403).
    """
    chat = await get_chat(chat_id)
    hw = await get_homework_by_id(chat_id, homework_id)
    if hw is None:
        return None

    is_private = getattr(chat, "chat_type", None) == "private"
    policy = getattr(chat, "hw_edit_policy", None)
    if not can_edit_homework_sync(
        is_private=is_private, is_admin=is_admin, policy=policy,
        author_id=hw.created_by_user_id, user_id=actor_user_id,
    ):
        raise HomeworkAccessError()

    await mark_homework_completed(
        chat_id, homework_id, is_completed,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_HOMEWORK,
        audit.ACTION_COMPLETE if is_completed else audit.ACTION_RESTORE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=homework_id, summary=audit.summarize(hw.subject_name),
    )
    hw.is_completed = is_completed
    return _homework_to_dto(hw, today, chat=chat, is_admin=is_admin, user_id=actor_user_id)


async def list_extra(
    chat_id: int,
    from_date: datetime.date,
    to_date: datetime.date,
    is_admin: bool = False,
) -> List[ExtraActivityDTO]:
    """Extra activities that have at least one occurrence in the date window."""
    if to_date < from_date:
        from_date, to_date = to_date, from_date
    span = (to_date - from_date).days + 1
    if span > MAX_RANGE_DAYS:
        to_date = from_date + datetime.timedelta(days=MAX_RANGE_DAYS - 1)

    all_extra = await get_extra_activities(chat_id)
    seen_ids: set = set()
    result: List[ExtraActivityDTO] = []
    cursor = from_date
    while cursor <= to_date:
        for a in activities_on_date(all_extra, cursor):
            if a.id not in seen_ids:
                seen_ids.add(a.id)
                result.append(_extra_to_dto(a, is_admin=is_admin))
        cursor += datetime.timedelta(days=1)
    return result


class ExtraActivityAccessError(Exception):
    """Raised when a non-admin tries to add/edit/delete an extra activity in a
    group chat — mirrors the bot's ``require_admin`` guard in ``handlers/extra.py``."""


async def create_extra_activity(
    chat_id: int,
    payload: ExtraActivityCreateDTO,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ExtraActivityDTO:
    if not is_admin:
        raise ExtraActivityAccessError()
    activity = await add_extra_activity(
        chat_id,
        payload.title,
        payload.kind,
        payload.start_time,
        day_of_week=payload.day_of_week,
        activity_date=payload.activity_date,
        end_time=payload.end_time,
        location=payload.location,
        note=payload.note,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_EXTRA, audit.ACTION_CREATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=activity.id, summary=audit.summarize(activity.title, activity.start_time),
    )
    return _extra_to_dto(activity, is_admin=is_admin)


async def edit_extra_activity(
    chat_id: int,
    activity_id: int,
    payload: ExtraActivityUpdateDTO,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[ExtraActivityDTO]:
    """Returns ``None`` if the activity does not belong to this chat (-> 404).

    Raises :class:`ExtraActivityAccessError` for a non-admin in a group chat
    (-> 403), checked *before* touching the row.
    """
    if not is_admin:
        raise ExtraActivityAccessError()
    activity = await get_extra_activity_by_id(chat_id, activity_id)
    if activity is None:
        return None

    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "day_of_week" in values and activity.kind != "weekly":
        raise ValueError("day_of_week only applies to weekly activities")
    if "activity_date" in values and activity.kind != "once":
        raise ValueError("activity_date only applies to one-off activities")
    if not values:
        return _extra_to_dto(activity, is_admin=is_admin)

    await update_extra_activity(
        chat_id, activity_id,
        actor_user_id=actor_user_id, actor_name=actor_name, **values,
    )
    await audit.record(
        chat_id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=activity_id,
        summary=audit.fields_summary(list(values.keys())),
    )
    updated = await get_extra_activity_by_id(chat_id, activity_id)
    return _extra_to_dto(updated, is_admin=is_admin) if updated else None


async def remove_extra_activity(
    chat_id: int,
    activity_id: int,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    """Returns ``False`` if the activity does not belong to this chat (-> 404).

    Raises :class:`ExtraActivityAccessError` for a non-admin in a group chat
    (-> 403).
    """
    if not is_admin:
        raise ExtraActivityAccessError()
    activity = await get_extra_activity_by_id(chat_id, activity_id)
    if activity is None:
        return False
    deleted = await delete_extra_activity(chat_id, activity_id)
    if deleted:
        await audit.record(
            chat_id, audit.ENTITY_EXTRA, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            entity_id=activity_id, summary=audit.summarize(activity.title),
        )
    return deleted


# --- Reminder settings --------------------------------------------------------

_REMINDER_CATEGORY_FIELDS = {
    "hw_reminder_enabled": "hw",
    "schedule_reminder_enabled": "sched",
    "hw_duetoday_enabled": "duetoday",
    "changes_reminder_enabled": "changes",
    "extra_reminder_enabled": "extra",
}

# Human labels for audit summaries, matching handlers/settings.CATEGORY_AUDIT_LABELS.
_REMINDER_FIELD_LABELS = {
    "hw_reminder_enabled": "ДЗ на завтра",
    "hw_reminder_time": "время напоминания о ДЗ",
    "schedule_reminder_enabled": "портфель на завтра",
    "schedule_reminder_time": "время напоминания о расписании",
    "hw_duetoday_enabled": "ДЗ в день сдачи",
    "hw_duetoday_time": "время напоминания о ДЗ в день сдачи",
    "changes_reminder_enabled": "изменения расписания",
    "extra_reminder_enabled": "доп. занятия",
    "quiet_hours": "тихие часы",
}


async def get_reminder_settings(chat_id: int, is_admin: bool) -> ReminderSettingsDTO:
    chat = await get_chat(chat_id)
    return ReminderSettingsDTO(
        hw_reminder_enabled=bool(getattr(chat, "hw_reminder_enabled", True)),
        hw_reminder_time=getattr(chat, "hw_reminder_time", "18:00"),
        schedule_reminder_enabled=bool(getattr(chat, "schedule_reminder_enabled", True)),
        schedule_reminder_time=getattr(chat, "schedule_reminder_time", "20:00"),
        hw_duetoday_enabled=bool(getattr(chat, "hw_duetoday_enabled", True)),
        hw_duetoday_time=getattr(chat, "hw_duetoday_time", "07:30"),
        changes_reminder_enabled=bool(getattr(chat, "changes_reminder_enabled", True)),
        extra_reminder_enabled=bool(getattr(chat, "extra_reminder_enabled", True)),
        quiet_start=getattr(chat, "quiet_start", None),
        quiet_end=getattr(chat, "quiet_end", None),
        can_edit=is_admin,
    )


class SettingsAccessError(Exception):
    """Raised when a non-admin tries to change reminder settings in a group
    chat — mirrors the bot's ``require_admin`` guard in ``handlers/settings.py``."""


async def update_reminder_settings(
    chat_id: int,
    payload: ReminderSettingsUpdateDTO,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ReminderSettingsDTO:
    if not is_admin:
        raise SettingsAccessError()

    changed: List[str] = []

    if payload.hw_reminder_time is not None or payload.schedule_reminder_time is not None:
        await update_chat_reminder_times(
            chat_id, payload.hw_reminder_time, payload.schedule_reminder_time
        )
        for field in ("hw_reminder_time", "schedule_reminder_time"):
            if getattr(payload, field) is not None:
                changed.append(_REMINDER_FIELD_LABELS[field])

    for field, category in _REMINDER_CATEGORY_FIELDS.items():
        value = getattr(payload, field)
        if value is not None:
            await set_reminder_category_enabled(chat_id, category, value)
            changed.append(_REMINDER_FIELD_LABELS[field])

    if payload.hw_duetoday_time is not None:
        await update_duetoday_time(chat_id, payload.hw_duetoday_time)
        changed.append(_REMINDER_FIELD_LABELS["hw_duetoday_time"])

    if payload.clear_quiet_hours:
        await set_quiet_hours(chat_id, None, None)
        changed.append(_REMINDER_FIELD_LABELS["quiet_hours"])
    elif payload.quiet_start is not None and payload.quiet_end is not None:
        start, end = payload.quiet_start, payload.quiet_end
        if start == end:  # an empty/zero-length window means "no quiet hours"
            start = end = None
        await set_quiet_hours(chat_id, start, end)
        changed.append(_REMINDER_FIELD_LABELS["quiet_hours"])

    if changed:
        await audit.record(
            chat_id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            summary=audit.fields_summary(changed),
        )
    return await get_reminder_settings(chat_id, is_admin)


# --- Audit log ----------------------------------------------------------------

def _audit_to_dto(entry: Any) -> AuditEntryDTO:
    return AuditEntryDTO(
        id=entry.id,
        created_at=entry.created_at,
        actor_name=audit.actor_label(entry.actor_user_id, entry.actor_name),
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        action=entry.action,
        summary=entry.summary,
    )


async def list_audit_log(
    chat_id: int,
    entity_type: Optional[str],
    page: int,
    page_size: int,
) -> AuditPageDTO:
    offset = (page - 1) * page_size
    entries = await get_audit_logs(chat_id, entity_type, limit=page_size, offset=offset)
    total = await count_audit_logs(chat_id, entity_type)
    return AuditPageDTO(
        items=[_audit_to_dto(e) for e in entries],
        total=total, page=page, page_size=page_size,
    )
