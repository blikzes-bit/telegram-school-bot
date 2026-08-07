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
    AuditEntryDTO, AuditPageDTO, ClassSettingsDTO, ClassSettingsUpdateDTO,
    DayScheduleDTO, ExtraActivityCreateDTO, ExtraActivityDTO,
    ExtraActivityUpdateDTO, HomeworkCreateDTO, HomeworkDTO, HomeworkUpdateDTO,
    DateOverridesDTO, DayOverrideDTO, DayOverrideUpdateDTO, InviteAcceptedDTO,
    InviteCreateDTO, InviteDTO, LabeledOptionDTO, LessonDTO, LessonOverrideDTO,
    LessonOverrideUpdateDTO, LessonSlotDTO, LessonSlotsUpdateDTO, MemberDTO,
    MembersPageDTO, PaymentCreateDTO, PaymentDTO, PaymentUpdateDTO,
    PermissionsDTO, ProfileFeaturesDTO, ProfileOptionDTO,
    ReminderSettingsDTO, ReminderSettingsUpdateDTO, RoleOptionDTO,
    ScheduleDayLessonDTO, ScheduleDayUpdateDTO, ScheduleRangeDTO,
    ScheduleTemplateDayDTO, ScheduleTemplateDTO, TimezoneOptionDTO, TodayDTO,
)
from database.db import (
    add_extra_activity, add_homework, add_payment, clear_date_overrides,
    clear_day_override, delete_lesson_override, get_all_schedule,
    get_day_override, get_lesson_overrides, get_lesson_slots, save_lesson_slots,
    save_schedule_day, set_day_override, set_lesson_override,
    consume_chat_invite,
    count_audit_logs, delete_payment, get_payment_by_id, get_payments,
    set_payment_paid, update_payment,
    create_chat_invite, delete_chat_invite, delete_extra_activity,
    delete_homework, delete_membership, get_audit_logs, get_chat,
    get_chat_invites, get_extra_activities, get_extra_activity_by_id,
    get_homework, get_homework_by_id, get_membership, get_memberships_for_chat,
    get_homework_completions, get_web_users_by_ids, mark_homework_completed,
    set_access_mode, set_homework_done_by, set_per_student_homework,
    set_chat_profile, set_chat_timezone, set_chat_title, set_hw_edit_policy,
    set_member_app_role, set_quiet_hours, set_reminder_category_enabled,
    update_chat_reminder_times, update_duetoday_time, update_extra_activity,
    update_payment_reminder_time,
    update_homework, upsert_membership,
)
from database.models import Chat, ExtraActivity, Homework
import services.timeservice as ts
from services import audit, profiles
from services.effective_schedule import (
    DAY_TYPE_LABELS, EffectiveDay, EffectiveLesson, get_effective_day,
    resolve_week_type_for_chat,
)
from services.extra_activities import activities_on_date
from services import permissions as perms
from services.permissions import (
    Capabilities, can_edit_homework_sync, normalize_policy,
)

# Weekday names for audit summaries. Deliberately local: this module must not
# import from ``keyboards/`` or ``handlers/`` (the Telegram adapter) — see the
# module docstring.
_WEEKDAY_NAMES = (
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
)

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


def _extra_to_dto(a: ExtraActivity, *, can_edit: bool = False) -> ExtraActivityDTO:
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
        can_edit=can_edit,
    )


def _homework_status_for(
    is_completed: bool, due_date: datetime.date, today: datetime.date
) -> str:
    if is_completed:
        return "completed"
    if due_date < today:
        return "overdue"
    return "active"


def _homework_status(hw: Homework, today: datetime.date) -> str:
    """Class-level status, used where there is no viewer to be personal about."""
    return _homework_status_for(bool(hw.is_completed), hw.due_date, today)


def per_student_marks(chat: Optional[Chat]) -> bool:
    """Whether this chat gives every student their own "done" mark."""
    return bool(getattr(chat, "per_student_homework", False))


def _homework_to_dto(
    hw: Homework,
    today: datetime.date,
    *,
    chat: Optional[Chat],
    caps: Capabilities,
    user_id: Optional[int],
    done_by: Optional[List[int]] = None,
) -> HomeworkDTO:
    """Map one entry, telling the client what *this* user may do with it.

    Two independent gates decide ``can_edit``: the role must allow touching
    existing entries at all, and the chat's ``hw_edit_policy`` may still narrow
    that to the author's own entries. ``can_complete`` is separate because a
    student may tick homework off without being able to rewrite it.

    In a chat with personal marks, ``is_completed`` answers "have **I** done it"
    (from ``done_by``) rather than "is this task closed for the class", and
    ``completed_count`` says how many people are done — the number a teacher
    actually wants. Everywhere else both stay exactly as before.
    """
    policy_allows = can_edit_homework_sync(
        is_private=getattr(chat, "chat_type", None) == "private",
        is_admin=caps.is_admin,
        policy=getattr(chat, "hw_edit_policy", None),
        author_id=hw.created_by_user_id,
        user_id=user_id,
    )
    personal = per_student_marks(chat)
    marks = done_by or []
    is_completed = (user_id in marks) if personal else bool(hw.is_completed)
    return HomeworkDTO(
        id=hw.id,
        subject_name=hw.subject_name,
        due_date=hw.due_date,
        description=hw.description,
        is_completed=is_completed,
        status=_homework_status_for(is_completed, hw.due_date, today),
        can_edit=caps.can_edit_homework and policy_allows,
        # With personal marks, ticking your own box is the one thing any viewer
        # may always do: it records something about them, not about the class's
        # shared data, and it cannot affect what anybody else sees.
        can_complete=True if personal else (caps.can_complete_homework and policy_allows),
        per_student=personal,
        completed_count=len(marks) if personal else None,
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
    caps: Capabilities,
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

    marks = await get_homework_completions(chat_id) if per_student_marks(chat) else {}

    def _dto(hw: Homework) -> HomeworkDTO:
        return _homework_to_dto(
            hw, date, chat=chat, caps=caps, user_id=user_id,
            done_by=marks.get(hw.id, []),
        )

    return TodayDTO(
        date=date,
        timezone=tz_name,
        weekday=effective.weekday,
        week_type=week_type,
        day_type=effective.day_type,
        day_note=effective.day_note,
        lessons=[_lesson_to_dto(lesson) for lesson in effective.lessons],
        extra=[_extra_to_dto(a, can_edit=caps.can_edit_extra) for a in extra],
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


# Audit summaries for a homework edit, worded exactly like the bot's
# handlers/homework.EDIT_FIELD_LABELS so one journal reads consistently no
# matter which surface made the change.
_HOMEWORK_FIELD_LABELS = {
    "subject_name": "предмет",
    "description": "описание",
    "due_date": "дата сдачи",
}


async def list_homework(
    chat_id: int,
    status: Optional[str],
    today: datetime.date,
    caps: Capabilities,
    user_id: Optional[int] = None,
) -> List[HomeworkDTO]:
    """Homework for a class filtered by status (active|completed|overdue).

    Where marks are personal, "completed" is a question about *this viewer*, so
    the rows cannot be pre-selected by the class-level flag — everything is
    fetched and the filter is applied to the personal answer further down.
    """
    chat = await get_chat(chat_id)
    personal = per_student_marks(chat)

    if personal or status not in ("completed", "active", "overdue"):
        rows = await get_homework(chat_id)
    elif status == "completed":
        rows = await get_homework(chat_id, is_completed=True)
    else:
        incomplete = await get_homework(chat_id, is_completed=False)
        if status == "overdue":
            rows = [hw for hw in incomplete if hw.due_date < today]
        else:
            rows = [hw for hw in incomplete if hw.due_date >= today]

    rows = sorted(rows, key=lambda hw: hw.due_date)
    marks = await get_homework_completions(chat_id) if personal else {}
    items = [
        _homework_to_dto(
            hw, today, chat=chat, caps=caps, user_id=user_id,
            done_by=marks.get(hw.id, []),
        )
        for hw in rows
    ]
    if not personal:
        return items
    # With personal marks the requested status is a personal question, so it has
    # to be re-applied to the personal answer: the rows above were selected by the
    # class-level flag, which is not what this viewer asked about.
    if status in ("completed", "active", "overdue"):
        wanted = "completed" if status == "completed" else status
        return [item for item in items if item.status == wanted]
    return items


async def create_homework(
    chat_id: int,
    payload: HomeworkCreateDTO,
    today: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> HomeworkDTO:
    """Add homework on behalf of a web user.

    Open to anybody the chat's rights model lets write — which in the default
    access mode is every member, exactly like the bot. In role mode a student or
    viewer may not add (``can_add_homework``), and the refusal happens here,
    before the row is written.
    """
    if not caps.can_add_homework:
        raise HomeworkAccessError()
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
    return _homework_to_dto(hw, today, chat=chat, caps=caps, user_id=actor_user_id)


class HomeworkAccessError(Exception):
    """Raised when the chat's ``hw_edit_policy`` forbids this actor from
    changing an existing homework entry (caller -> 403)."""


def _require_homework_edit(
    chat: Optional[Chat],
    hw: Homework,
    caps: Capabilities,
    actor_user_id: int,
    *,
    completing: bool = False,
) -> None:
    """Authorise a change to an existing entry, or raise :class:`HomeworkAccessError`.

    Called by *every* mutation of an existing entry (complete, edit, delete)
    immediately before the write, so a stale client that still shows an edit
    button — or a hand-crafted request — is rejected here rather than trusted.

    Both gates must pass: the role (``completing`` picks the looser
    ``can_complete_homework``, since a student may tick homework off without
    being able to rewrite it) and the chat's ``hw_edit_policy``.
    """
    role_allows = caps.can_complete_homework if completing else caps.can_edit_homework
    if not role_allows:
        raise HomeworkAccessError()
    if not can_edit_homework_sync(
        is_private=getattr(chat, "chat_type", None) == "private",
        is_admin=caps.is_admin,
        policy=getattr(chat, "hw_edit_policy", None),
        author_id=hw.created_by_user_id,
        user_id=actor_user_id,
    ):
        raise HomeworkAccessError()


async def set_homework_completed(
    chat_id: int,
    homework_id: int,
    is_completed: bool,
    today: datetime.date,
    caps: Capabilities,
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

    if per_student_marks(chat):
        # A personal mark needs no permission beyond being here: it records what
        # *this* person did, and cannot change anybody else's view or silence a
        # chat-wide reminder.
        if not await set_homework_done_by(
            chat_id, homework_id, actor_user_id, is_completed, ts.now_iso_utc()
        ):
            return None
        marks = (await get_homework_completions(chat_id)).get(homework_id, [])
        return _homework_to_dto(
            hw, today, chat=chat, caps=caps, user_id=actor_user_id, done_by=marks,
        )

    _require_homework_edit(chat, hw, caps, actor_user_id, completing=True)

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
    return _homework_to_dto(hw, today, chat=chat, caps=caps, user_id=actor_user_id)


async def edit_homework(
    chat_id: int,
    homework_id: int,
    payload: HomeworkUpdateDTO,
    today: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[HomeworkDTO]:
    """Edit subject / due date / description of an existing entry.

    Returns ``None`` if the entry does not belong to this chat (caller -> 404).
    Raises :class:`HomeworkAccessError` if the chat's policy forbids this actor
    (caller -> 403). An empty patch is a no-op that returns the entry unchanged.
    """
    chat = await get_chat(chat_id)
    hw = await get_homework_by_id(chat_id, homework_id)
    if hw is None:
        return None
    _require_homework_edit(chat, hw, caps, actor_user_id)

    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        return _homework_to_dto(hw, today, chat=chat, caps=caps, user_id=actor_user_id)

    await update_homework(
        chat_id, homework_id,
        actor_user_id=actor_user_id, actor_name=actor_name, **values,
    )
    await audit.record(
        chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=homework_id,
        summary=audit.fields_summary(
            [_HOMEWORK_FIELD_LABELS.get(key, key) for key in values]
        ),
    )
    updated = await get_homework_by_id(chat_id, homework_id)
    if updated is None:  # deleted concurrently between the write and the re-read
        return None
    return _homework_to_dto(updated, today, chat=chat, caps=caps, user_id=actor_user_id)


async def remove_homework(
    chat_id: int,
    homework_id: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    """Delete an entry permanently.

    Returns ``False`` if it does not belong to this chat (caller -> 404). Raises
    :class:`HomeworkAccessError` if the chat's policy forbids this actor (-> 403).
    Attachments are removed by the ``ON DELETE CASCADE`` on
    ``homework_attachments``; the audit row survives the entry it describes.
    """
    chat = await get_chat(chat_id)
    hw = await get_homework_by_id(chat_id, homework_id)
    if hw is None:
        return False
    _require_homework_edit(chat, hw, caps, actor_user_id)

    subject_name, due_date = hw.subject_name, str(hw.due_date)
    deleted = await delete_homework(chat_id, homework_id)
    if deleted:
        await audit.record(
            chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            entity_id=homework_id, summary=audit.summarize(subject_name, due_date),
        )
    return deleted


async def list_extra(
    chat_id: int,
    from_date: datetime.date,
    to_date: datetime.date,
    caps: Capabilities,
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
                result.append(_extra_to_dto(a, can_edit=caps.can_edit_extra))
        cursor += datetime.timedelta(days=1)
    return result


class ExtraActivityAccessError(Exception):
    """Raised when a non-admin tries to add/edit/delete an extra activity in a
    group chat — mirrors the bot's ``require_admin`` guard in ``handlers/extra.py``."""


async def create_extra_activity(
    chat_id: int,
    payload: ExtraActivityCreateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ExtraActivityDTO:
    if not caps.can_edit_extra:
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
    return _extra_to_dto(activity, can_edit=caps.can_edit_extra)


async def edit_extra_activity(
    chat_id: int,
    activity_id: int,
    payload: ExtraActivityUpdateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[ExtraActivityDTO]:
    """Returns ``None`` if the activity does not belong to this chat (-> 404).

    Raises :class:`ExtraActivityAccessError` for a non-admin in a group chat
    (-> 403), checked *before* touching the row.
    """
    if not caps.can_edit_extra:
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
        return _extra_to_dto(activity, can_edit=caps.can_edit_extra)

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
    return _extra_to_dto(updated, can_edit=caps.can_edit_extra) if updated else None


async def remove_extra_activity(
    chat_id: int,
    activity_id: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    """Returns ``False`` if the activity does not belong to this chat (-> 404).

    Raises :class:`ExtraActivityAccessError` for a non-admin in a group chat
    (-> 403).
    """
    if not caps.can_edit_extra:
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
    "payment_reminder_enabled": "payment",
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
    "payment_reminder_enabled": "напоминания об оплате",
    "payment_reminder_time": "время напоминания об оплате",
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
        payment_reminder_enabled=bool(getattr(chat, "payment_reminder_enabled", True)),
        payment_reminder_time=getattr(chat, "payment_reminder_time", "10:00"),
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

    if payload.payment_reminder_time is not None:
        await update_payment_reminder_time(chat_id, payload.payment_reminder_time)
        changed.append(_REMINDER_FIELD_LABELS["payment_reminder_time"])

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


# --- Class settings (name, timezone, homework-edit policy) --------------------

_CLASS_FIELD_LABELS = {
    "title": "название класса",
    "timezone": "часовой пояс",
    "hw_edit_policy": "права на изменение ДЗ",
    "profile": "режим чата",
    "per_student_homework": "личные отметки о выполнении",
}


async def get_class_settings(chat_id: int, is_admin: bool) -> ClassSettingsDTO:
    """The chat's non-reminder settings, with the timezone already rendered.

    ``timezone_label`` / ``local_time`` come from ``services.timeservice`` — the
    same source the bot and the scheduler use — so the web app never computes a
    date or an offset itself.
    """
    chat = await get_chat(chat_id)
    tz = ts.chat_tz(chat)
    profile = profiles.resolve(chat)
    f = profiles.features(profile)
    return ClassSettingsDTO(
        chat_id=chat_id,
        chat_type=getattr(chat, "chat_type", "private"),
        title=getattr(chat, "title", None),
        profile=profile,
        profile_label=profiles.PROFILE_LABELS[profile],
        profile_options=[
            ProfileOptionDTO(
                name=name,
                label=profiles.PROFILE_LABELS[name],
                description=profiles.PROFILE_DESCRIPTIONS[name],
            )
            for name in profiles.PROFILES
        ],
        features=ProfileFeaturesDTO(
            school_schedule=f.school_schedule,
            homework=f.homework,
            extra_activities=f.extra_activities,
            homework_policy=f.homework_policy,
        ),
        timezone=str(tz),
        timezone_label=ts.tz_label(tz),
        local_time=ts.local_time_label(tz),
        hw_edit_policy=normalize_policy(getattr(chat, "hw_edit_policy", None)),
        per_student_homework=per_student_marks(chat),
        can_edit=is_admin,
        timezone_options=[
            TimezoneOptionDTO(name=name, label=label)
            for name, label in ts.POPULAR_TIMEZONES
        ],
    )


async def update_class_settings(
    chat_id: int,
    payload: ClassSettingsUpdateDTO,
    is_admin: bool,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ClassSettingsDTO:
    """Change the class name / timezone / homework-edit policy.

    Raises :class:`SettingsAccessError` for a non-admin in a group chat (-> 403)
    and ``ValueError`` for an unknown timezone or policy (-> 400). Both are
    checked before anything is written, and the timezone/policy validity checks
    live in ``database.db`` so a bad value can never reach the scheduler.
    """
    if not is_admin:
        raise SettingsAccessError()

    fields = payload.model_dump(exclude_unset=True)
    changed: List[str] = []

    if "timezone" in fields and fields["timezone"] is not None:
        if not await set_chat_timezone(chat_id, fields["timezone"]):
            raise ValueError("unknown timezone")
        changed.append(_CLASS_FIELD_LABELS["timezone"])

    if "profile" in fields and fields["profile"] is not None:
        if not await set_chat_profile(chat_id, fields["profile"]):
            raise ValueError("unknown chat profile")
        changed.append(_CLASS_FIELD_LABELS["profile"])

    if "hw_edit_policy" in fields and fields["hw_edit_policy"] is not None:
        if not await set_hw_edit_policy(chat_id, fields["hw_edit_policy"]):
            raise ValueError("unknown homework-edit policy")
        changed.append(_CLASS_FIELD_LABELS["hw_edit_policy"])

    if "per_student_homework" in fields and fields["per_student_homework"] is not None:
        await set_per_student_homework(chat_id, bool(fields["per_student_homework"]))
        changed.append(_CLASS_FIELD_LABELS["per_student_homework"])

    # An explicitly sent blank title clears the name; an omitted one changes nothing.
    if "title" in fields:
        await set_chat_title(chat_id, fields["title"])
        changed.append(_CLASS_FIELD_LABELS["title"])

    if changed:
        await audit.record(
            chat_id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            summary=audit.fields_summary(changed),
        )
    return await get_class_settings(chat_id, is_admin)


# --- Schedule editing (weekly template + per-date overrides) ------------------

class ScheduleAccessError(Exception):
    """Raised when the actor may not change this chat's schedule (-> 403)."""


def _normalize_week_type(chat: Optional[Chat], week_type: Optional[str]) -> str:
    """The week template to read/write.

    A chat with alternating weeks off has exactly one template (``all``), so any
    request for A/B there is answered with ``all`` rather than silently editing a
    template nobody can see.
    """
    if not getattr(chat, "week_mode", False):
        return "all"
    return week_type if week_type in ("A", "B") else "all"


async def get_schedule_template(
    chat_id: int, week_type: Optional[str], caps: Capabilities
) -> ScheduleTemplateDTO:
    """The editable weekly template: bell times plus subjects for each weekday.

    Readable by any member (the schedule is the class's shared information);
    ``can_edit`` says whether the controls will do anything, and the mutation
    endpoints re-check it.
    """
    chat = await get_chat(chat_id)
    resolved = _normalize_week_type(chat, week_type)
    week_mode = bool(getattr(chat, "week_mode", False))

    slots = await get_lesson_slots(chat_id)
    rows = await get_all_schedule(chat_id)
    by_day: dict = {}
    for row in rows:
        if row.week_type != resolved:
            continue
        by_day.setdefault(row.day_of_week, {})[row.lesson_number] = row.subject_name

    days = []
    for weekday in range(7):
        subjects = by_day.get(weekday, {})
        days.append(ScheduleTemplateDayDTO(
            weekday=weekday,
            lessons=[
                ScheduleDayLessonDTO(
                    lesson_number=slot.lesson_number,
                    subject_name=subjects.get(slot.lesson_number),
                )
                for slot in slots
            ],
        ))

    return ScheduleTemplateDTO(
        week_type=resolved,
        week_mode=week_mode,
        week_types=["A", "B"] if week_mode else ["all"],
        slots=[
            LessonSlotDTO(
                lesson_number=slot.lesson_number,
                start_time=slot.start_time,
                end_time=slot.end_time,
            )
            for slot in slots
        ],
        days=days,
        can_edit=caps.can_edit_schedule,
    )


async def update_lesson_slots(
    chat_id: int,
    payload: LessonSlotsUpdateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ScheduleTemplateDTO:
    """Replace the bell times.

    ``db.save_lesson_slots`` also prunes subjects for lessons that no longer
    exist, so shortening the day cannot leave orphaned rows behind — the same
    behaviour the bot's editor has.
    """
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()
    await save_lesson_slots(
        chat_id, [(s.lesson_number, s.start_time, s.end_time) for s in payload.slots]
    )
    await audit.record(
        chat_id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize("время звонков", f"{len(payload.slots)} уроков"),
    )
    return await get_schedule_template(chat_id, None, caps)


async def update_schedule_day(
    chat_id: int,
    weekday: int,
    week_type: Optional[str],
    payload: ScheduleDayUpdateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> ScheduleTemplateDTO:
    """Replace the subjects of one weekday in one weekly template."""
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0..6")

    chat = await get_chat(chat_id)
    resolved = _normalize_week_type(chat, week_type)
    lessons = [
        (item.lesson_number, item.subject_name.strip())
        for item in payload.lessons
        if item.subject_name and item.subject_name.strip()
    ]
    await save_schedule_day(chat_id, weekday, lessons, week_type=resolved)
    await audit.record(
        chat_id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize(_WEEKDAY_NAMES[weekday], f"{len(lessons)} уроков"),
    )
    return await get_schedule_template(chat_id, resolved, caps)


_DAY_TYPE_DESCRIPTIONS = {
    "free": "уроков нет",
    "holiday": "праздник, уроков нет",
    "vacation": "каникулы",
    "remote": "занятия дистанционно",
}


def _day_type_options() -> List[LabeledOptionDTO]:
    return [
        LabeledOptionDTO(
            name=name,
            label=DAY_TYPE_LABELS.get(name, name),
            description=_DAY_TYPE_DESCRIPTIONS.get(name, ""),
        )
        for name in ("free", "holiday", "vacation", "remote")
    ]


async def get_date_overrides(
    chat_id: int, date: datetime.date, caps: Capabilities
) -> DateOverridesDTO:
    """What has been changed for one specific date (nothing = a normal day)."""
    day = await get_day_override(chat_id, date)
    lessons = await get_lesson_overrides(chat_id, date)
    return DateOverridesDTO(
        date=date,
        day=(
            DayOverrideDTO(
                day_type=day.day_type,
                day_type_label=DAY_TYPE_LABELS.get(day.day_type, day.day_type),
                note=day.note,
            )
            if day is not None else None
        ),
        lessons=[
            LessonOverrideDTO(
                lesson_number=row.lesson_number,
                action=row.action,
                subject_name=row.subject_name,
                start_time=row.start_time,
                end_time=row.end_time,
                note=row.note,
            )
            for row in lessons
        ],
        day_type_options=_day_type_options(),
        can_edit=caps.can_edit_schedule,
    )


async def set_day_type(
    chat_id: int,
    date: datetime.date,
    payload: DayOverrideUpdateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> DateOverridesDTO:
    """Set (or clear, with ``day_type=None``) the whole-day setting for a date."""
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()

    if payload.day_type is None:
        cleared = await clear_day_override(chat_id, date)
        if cleared:
            await audit.record(
                chat_id, audit.ENTITY_DAY_OVERRIDE, audit.ACTION_DELETE,
                actor_user_id=actor_user_id, actor_name=actor_name,
                summary=audit.summarize(str(date)),
            )
        return await get_date_overrides(chat_id, date, caps)

    if payload.day_type not in DAY_TYPE_LABELS:
        raise ValueError("unknown day type")
    await set_day_override(
        chat_id, date, payload.day_type, note=payload.note,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_DAY_OVERRIDE, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize(str(date), DAY_TYPE_LABELS[payload.day_type]),
    )
    return await get_date_overrides(chat_id, date, caps)


async def set_lesson_change(
    chat_id: int,
    date: datetime.date,
    lesson_number: int,
    payload: LessonOverrideUpdateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> DateOverridesDTO:
    """Cancel, replace or re-time one lesson on one date."""
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()
    if lesson_number < 1:
        raise ValueError("lesson_number must be positive")

    await set_lesson_override(
        chat_id, date, lesson_number, payload.action,
        subject_name=(payload.subject_name.strip() if payload.subject_name else None),
        start_time=payload.start_time,
        end_time=payload.end_time,
        note=payload.note,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_LESSON_OVERRIDE, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize(
            str(date), f"урок {lesson_number}",
            "отмена" if payload.action == "cancel" else "замена/изменение",
        ),
    )
    return await get_date_overrides(chat_id, date, caps)


async def clear_lesson_change(
    chat_id: int,
    date: datetime.date,
    lesson_number: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> DateOverridesDTO:
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()
    if await delete_lesson_override(chat_id, date, lesson_number):
        await audit.record(
            chat_id, audit.ENTITY_LESSON_OVERRIDE, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            summary=audit.summarize(str(date), f"урок {lesson_number}"),
        )
    return await get_date_overrides(chat_id, date, caps)


async def clear_all_date_overrides(
    chat_id: int,
    date: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> DateOverridesDTO:
    """Back to the plain weekly template for this date."""
    if not caps.can_edit_schedule:
        raise ScheduleAccessError()
    if await clear_date_overrides(chat_id, date):
        await audit.record(
            chat_id, audit.ENTITY_DAY_OVERRIDE, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            summary=audit.summarize(str(date), "все изменения отменены"),
        )
    return await get_date_overrides(chat_id, date, caps)


# --- Payments (tutor profile) -------------------------------------------------

class PaymentAccessError(Exception):
    """Raised when the actor may not manage this chat's payments (-> 403)."""


def _payment_to_dto(
    payment: Any, today: datetime.date, *, can_edit: bool
) -> PaymentDTO:
    return PaymentDTO(
        id=payment.id,
        title=payment.title,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        amount_text=profiles.format_amount(payment.amount_minor, payment.currency),
        due_date=payment.due_date,
        period=payment.period,
        period_label=profiles.PAYMENT_PERIOD_LABELS.get(payment.period, payment.period),
        is_paid=bool(payment.is_paid),
        paid_at=payment.paid_at,
        note=payment.note,
        remind_days_before=payment.remind_days_before,
        status=profiles.payment_status(
            payment.due_date, today, bool(payment.is_paid), payment.remind_days_before
        ),
        can_edit=can_edit,
    )


async def list_payments(
    chat_id: int, today: datetime.date, caps: Capabilities, only_unpaid: bool = False
) -> List[PaymentDTO]:
    """Payments of a class, soonest due first. Readable by any member — everyone
    who is being asked to pay should be able to see what and when."""
    rows = await get_payments(chat_id, is_paid=False if only_unpaid else None)
    return [_payment_to_dto(p, today, can_edit=caps.can_edit_payments) for p in rows]


async def create_payment(
    chat_id: int,
    payload: PaymentCreateDTO,
    today: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> PaymentDTO:
    if not caps.can_edit_payments:
        raise PaymentAccessError()
    payment = await add_payment(
        chat_id,
        payload.title,
        payload.amount_minor,
        payload.due_date,
        currency=payload.currency,
        period=payload.period,
        note=payload.note,
        remind_days_before=payload.remind_days_before,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_PAYMENT, audit.ACTION_CREATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=payment.id,
        summary=audit.summarize(
            payment.title,
            profiles.format_amount(payment.amount_minor, payment.currency),
            str(payment.due_date),
        ),
    )
    return _payment_to_dto(payment, today, can_edit=True)


async def edit_payment(
    chat_id: int,
    payment_id: int,
    payload: PaymentUpdateDTO,
    today: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[PaymentDTO]:
    """``None`` when the payment does not belong to this chat (caller -> 404)."""
    if not caps.can_edit_payments:
        raise PaymentAccessError()
    payment = await get_payment_by_id(chat_id, payment_id)
    if payment is None:
        return None

    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    if values:
        if not await update_payment(
            chat_id, payment_id,
            actor_user_id=actor_user_id, actor_name=actor_name, **values,
        ):
            raise ValueError("nothing to update")
        await audit.record(
            chat_id, audit.ENTITY_PAYMENT, audit.ACTION_UPDATE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            entity_id=payment_id,
            summary=audit.fields_summary(
                [_PAYMENT_FIELD_LABELS.get(key, key) for key in values]
            ),
        )
    updated = await get_payment_by_id(chat_id, payment_id)
    return _payment_to_dto(updated, today, can_edit=True) if updated else None


async def mark_payment_paid(
    chat_id: int,
    payment_id: int,
    is_paid: bool,
    today: datetime.date,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> Optional[PaymentDTO]:
    if not caps.can_edit_payments:
        raise PaymentAccessError()
    payment = await get_payment_by_id(chat_id, payment_id)
    if payment is None:
        return None
    await set_payment_paid(
        chat_id, payment_id, is_paid,
        paid_at=ts.now_iso_utc() if is_paid else None,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_PAYMENT,
        audit.ACTION_COMPLETE if is_paid else audit.ACTION_RESTORE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=payment_id, summary=audit.summarize(payment.title),
    )
    updated = await get_payment_by_id(chat_id, payment_id)
    return _payment_to_dto(updated, today, can_edit=True) if updated else None


async def remove_payment(
    chat_id: int,
    payment_id: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    if not caps.can_edit_payments:
        raise PaymentAccessError()
    payment = await get_payment_by_id(chat_id, payment_id)
    if payment is None:
        return False
    title = payment.title
    deleted = await delete_payment(chat_id, payment_id)
    if deleted:
        await audit.record(
            chat_id, audit.ENTITY_PAYMENT, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            entity_id=payment_id, summary=audit.summarize(title),
        )
    return deleted


_PAYMENT_FIELD_LABELS = {
    "title": "название",
    "amount_minor": "сумма",
    "currency": "валюта",
    "due_date": "дата оплаты",
    "period": "периодичность",
    "note": "примечание",
    "remind_days_before": "за сколько дней напоминать",
}


# --- Members and invitations ---------------------------------------------------

class MemberAccessError(Exception):
    """Raised when the actor may not manage this chat's members (-> 403)."""


class MemberNotFoundError(Exception):
    """Raised when the target membership does not exist in this chat (-> 404)."""


def _role_options(names) -> List[RoleOptionDTO]:
    return [
        RoleOptionDTO(
            name=name,
            label=perms.ROLE_LABELS.get(name, name),
            description=perms.ROLE_DESCRIPTIONS.get(name, ""),
        )
        for name in names
    ]


def _access_mode_options() -> List[RoleOptionDTO]:
    return [
        RoleOptionDTO(
            name=mode,
            label=perms.ACCESS_MODE_LABELS[mode],
            description=perms.ACCESS_MODE_DESCRIPTIONS[mode],
        )
        for mode in perms.ACCESS_MODES
    ]


async def list_members(chat_id: int, caps: Capabilities, viewer_user_id: int) -> MembersPageDTO:
    """Everyone with access to this class, plus what the viewer may change.

    Readable by any member — a class knowing who can see its data is not a
    secret, and the payload carries nothing beyond ids and display names. Only
    ``can_manage`` decides whether the management controls do anything, and the
    mutation endpoints re-check it.
    """
    chat = await get_chat(chat_id)
    memberships = await get_memberships_for_chat(chat_id)
    names = await get_web_users_by_ids([m.user_id for m in memberships])
    owner_id = getattr(chat, "owner_user_id", None)

    members: List[MemberDTO] = []
    for m in memberships:
        member_caps = perms.capabilities(
            chat,
            user_id=m.user_id,
            is_telegram_admin=m.role == "admin",
            app_role=m.app_role,
        )
        members.append(MemberDTO(
            user_id=m.user_id,
            display_name=getattr(names.get(m.user_id), "display_name", None),
            role=member_caps.role,
            role_label=perms.ROLE_LABELS.get(member_caps.role, member_caps.role),
            app_role=perms.normalize_app_role(m.app_role),
            is_owner=owner_id is not None and m.user_id == owner_id,
            is_self=m.user_id == viewer_user_id,
        ))

    mode = perms.normalize_access_mode(getattr(chat, "access_mode", None))
    return MembersPageDTO(
        members=members,
        access_mode=mode,
        access_mode_label=perms.ACCESS_MODE_LABELS[mode],
        access_mode_options=_access_mode_options(),
        assignable_roles=_role_options(perms.ASSIGNABLE_ROLES),
        can_manage=caps.can_manage_members,
    )


async def set_member_role(
    chat_id: int,
    target_user_id: int,
    app_role: Optional[str],
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> MembersPageDTO:
    """Assign or clear a member's app role.

    Refuses to touch the chat's owner: ownership is not a role that can be
    edited away, and allowing it would let an owner accidentally lock themselves
    out of their own class.
    """
    if not caps.can_manage_members:
        raise MemberAccessError()

    chat = await get_chat(chat_id)
    owner_id = getattr(chat, "owner_user_id", None)
    if owner_id is not None and target_user_id == owner_id:
        raise MemberAccessError()

    if not await set_member_app_role(chat_id, target_user_id, app_role):
        # Either the membership does not exist, or the role is not assignable.
        if await get_membership(chat_id, target_user_id) is None:
            raise MemberNotFoundError()
        raise ValueError("unknown role")

    await audit.record(
        chat_id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize(
            "роль участника",
            perms.ROLE_LABELS.get(app_role or "", "без роли"),
        ),
    )
    return await list_members(chat_id, caps, actor_user_id)


async def remove_member(
    chat_id: int,
    target_user_id: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    """Revoke someone's access. The owner cannot be removed, nor can you remove
    yourself — both would leave a class nobody can manage."""
    if not caps.can_manage_members:
        raise MemberAccessError()
    chat = await get_chat(chat_id)
    owner_id = getattr(chat, "owner_user_id", None)
    if target_user_id == actor_user_id or (owner_id is not None and target_user_id == owner_id):
        raise MemberAccessError()

    removed = await delete_membership(chat_id, target_user_id)
    if not removed:
        raise MemberNotFoundError()
    await audit.record(
        chat_id, audit.ENTITY_SETTINGS, audit.ACTION_DELETE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize("доступ участника отозван"),
    )
    return True


async def set_chat_access_mode(
    chat_id: int,
    mode: str,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> MembersPageDTO:
    """Switch the chat between Telegram-derived and role-based rights."""
    if not caps.can_manage_members:
        raise MemberAccessError()
    if not await set_access_mode(chat_id, mode):
        raise ValueError("unknown access mode")
    await audit.record(
        chat_id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize("кто вносит данные", perms.ACCESS_MODE_LABELS[mode]),
    )
    # Recomputed against the *new* mode, so the caller sees the real outcome.
    chat = await get_chat(chat_id)
    membership = await get_membership(chat_id, actor_user_id)
    fresh = perms.capabilities(
        chat,
        user_id=actor_user_id,
        is_telegram_admin=getattr(membership, "role", None) == "admin",
        app_role=getattr(membership, "app_role", None),
    )
    return await list_members(chat_id, fresh, actor_user_id)


class InviteError(Exception):
    """Raised when an invitation cannot be used (unknown, spent or expired)."""


def _invite_to_dto(invite: Any, *, token: Optional[str] = None) -> InviteDTO:
    return InviteDTO(
        id=invite.id,
        app_role=invite.app_role,
        role_label=perms.ROLE_LABELS.get(invite.app_role, invite.app_role),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        created_by_name=invite.created_by_name,
        token=token,
    )


async def create_invite(
    chat_id: int,
    payload: InviteCreateDTO,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
    *,
    token: str,
    token_hash: str,
) -> InviteDTO:
    """Mint an invitation granting ``payload.app_role``.

    The raw ``token`` is generated by the caller (the web layer owns the hashing
    pepper), stored only as ``token_hash``, and returned here **once** — it is
    unrecoverable afterwards, exactly like a launch token or a session.
    """
    if not caps.can_manage_members:
        raise MemberAccessError()
    if payload.app_role not in perms.ASSIGNABLE_ROLES:
        raise ValueError("unknown role")

    now = datetime.datetime.now(datetime.timezone.utc)
    invite = await create_chat_invite(
        token_hash,
        chat_id,
        payload.app_role,
        created_at=now.isoformat(),
        expires_at=(now + datetime.timedelta(hours=payload.ttl_hours)).isoformat(),
        created_by_user_id=actor_user_id,
        created_by_name=actor_name,
    )
    await audit.record(
        chat_id, audit.ENTITY_SETTINGS, audit.ACTION_CREATE,
        actor_user_id=actor_user_id, actor_name=actor_name,
        summary=audit.summarize(
            "приглашение", perms.ROLE_LABELS.get(payload.app_role, payload.app_role)
        ),
    )
    return _invite_to_dto(invite, token=token)


async def list_invites(chat_id: int, caps: Capabilities) -> List[InviteDTO]:
    """Unused invitations of this chat. Owner-only: an invite is a credential,
    and even its metadata tells you what is on offer."""
    if not caps.can_manage_members:
        raise MemberAccessError()
    return [_invite_to_dto(i) for i in await get_chat_invites(chat_id)]


async def revoke_invite(
    chat_id: int,
    invite_id: int,
    caps: Capabilities,
    actor_user_id: int,
    actor_name: Optional[str],
) -> bool:
    if not caps.can_manage_members:
        raise MemberAccessError()
    revoked = await delete_chat_invite(chat_id, invite_id)
    if revoked:
        await audit.record(
            chat_id, audit.ENTITY_SETTINGS, audit.ACTION_DELETE,
            actor_user_id=actor_user_id, actor_name=actor_name,
            summary=audit.summarize("приглашение отозвано"),
        )
    return revoked


async def accept_invite(
    token_hash: str, user_id: int, display_name: Optional[str]
) -> InviteAcceptedDTO:
    """Redeem an invitation for the authenticated user.

    Identity is already proven by the verified ``initData`` that created the
    session; the token supplies the *authorisation* — which chat, and with what
    role. Claiming is atomic and single-use, and an expired invite is refused
    even though the claim consumed it (a spent expired invite is harmless, and
    refusing to act on it is what matters).
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    invite = await consume_chat_invite(token_hash, now.isoformat(), user_id)
    if invite is None:
        raise InviteError("invitation is unknown or already used")

    expires = invite.expires_at
    try:
        expired = datetime.datetime.fromisoformat(expires) <= now
    except ValueError:
        expired = True
    if expired:
        raise InviteError("invitation has expired")

    await upsert_membership(invite.chat_id, user_id, "member", now.isoformat())
    await set_member_app_role(invite.chat_id, user_id, invite.app_role)
    await audit.record(
        invite.chat_id, audit.ENTITY_SETTINGS, audit.ACTION_CREATE,
        actor_user_id=user_id, actor_name=display_name,
        summary=audit.summarize(
            "принято приглашение",
            perms.ROLE_LABELS.get(invite.app_role, invite.app_role),
        ),
    )
    chat = await get_chat(invite.chat_id)
    return InviteAcceptedDTO(
        chat_id=invite.chat_id,
        title=getattr(chat, "title", None),
        app_role=invite.app_role,
        role_label=perms.ROLE_LABELS.get(invite.app_role, invite.app_role),
    )


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
