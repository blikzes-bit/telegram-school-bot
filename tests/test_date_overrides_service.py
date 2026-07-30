"""
Effective-schedule service + DB-layer tests for per-date changes:
cancellation, substitution, time change, one-off lesson, free/holiday/remote
days, override-priority over the weekly template, chat_id isolation, cascade
delete and group→supergroup migration.
"""
import datetime

import pytest
from sqlalchemy import select

from database.db import (
    get_or_create_chat, delete_chat, migrate_chat,
    save_lesson_slots, save_schedule_day,
    set_lesson_override, get_lesson_overrides, delete_lesson_override,
    set_day_override, get_day_override, clear_day_override,
    clear_date_overrides, get_override_dates,
)
from database.models import LessonSlot, Schedule, DayOverride, LessonOverride
from services.effective_schedule import (
    compute_effective_day, get_effective_day,
)

CHAT_ID = 313131
OTHER_CHAT_ID = 313132
DATE = datetime.date(2026, 9, 14)  # a Monday (weekday 0)


def _slots():
    return [
        LessonSlot(chat_id=CHAT_ID, lesson_number=1, start_time="08:00", end_time="08:45"),
        LessonSlot(chat_id=CHAT_ID, lesson_number=2, start_time="08:55", end_time="09:40"),
    ]


def _schedule():
    return [
        Schedule(chat_id=CHAT_ID, day_of_week=0, lesson_number=1, subject_name="Математика"),
        Schedule(chat_id=CHAT_ID, day_of_week=0, lesson_number=2, subject_name="Физика"),
    ]


def _lesson_ovr(**kw):
    kw.setdefault("chat_id", CHAT_ID)
    kw.setdefault("date", DATE)
    return LessonOverride(**kw)


# --- Pure computation: template only ----------------------------------------

def test_template_only_no_overrides():
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, [])
    assert eff.day_type is None
    assert [(lesson.lesson_number, lesson.subject_name) for lesson in eff.lessons] == \
        [(1, "Математика"), (2, "Физика")]
    assert eff.has_lessons is True
    assert eff.has_changes is False


# --- Cancellation -----------------------------------------------------------

def test_cancel_lesson():
    ovr = [_lesson_ovr(lesson_number=1, action="cancel")]
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, ovr)
    first = eff.lessons[0]
    assert first.cancelled is True
    assert first.subject_name == "Математика"  # template subject kept for display
    assert first.start_time == "08:00"          # template time kept
    assert eff.has_changes is True
    # A cancelled lesson does not count as a lesson that "takes place".
    assert eff.has_lessons is True  # lesson 2 still happens


def test_cancel_all_lessons_means_no_lessons():
    ovr = [_lesson_ovr(lesson_number=1, action="cancel"), _lesson_ovr(lesson_number=2, action="cancel")]
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, ovr)
    assert eff.has_lessons is False
    assert all(lesson.cancelled for lesson in eff.lessons)


# --- Substitution (replace subject) -----------------------------------------

def test_replace_subject_overrides_template():
    ovr = [_lesson_ovr(lesson_number=1, action="set", subject_name="Химия")]
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, ovr)
    first = eff.lessons[0]
    assert first.subject_name == "Химия"       # not "Математика"
    assert first.subject_changed is True
    assert first.start_time == "08:00"          # time unchanged (from template)
    assert first.time_changed is False


# --- Time change ------------------------------------------------------------

def test_change_time_only():
    ovr = [_lesson_ovr(lesson_number=1, action="set", start_time="09:00", end_time="09:45")]
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, ovr)
    first = eff.lessons[0]
    assert (first.start_time, first.end_time) == ("09:00", "09:45")
    assert first.time_changed is True
    assert first.subject_name == "Математика"   # subject unchanged
    assert first.subject_changed is False


# --- One-off added lesson ---------------------------------------------------

def test_add_one_off_lesson():
    ovr = [_lesson_ovr(lesson_number=3, action="set", subject_name="Кружок", start_time="15:00", end_time="15:45")]
    eff = compute_effective_day(DATE, _slots(), _schedule(), None, ovr)
    added = [lesson for lesson in eff.lessons if lesson.lesson_number == 3][0]
    assert added.added is True
    assert added.subject_name == "Кружок"
    assert (added.start_time, added.end_time) == ("15:00", "15:45")
    assert eff.has_changes is True


# --- Whole-day types --------------------------------------------------------

def test_free_day_suppresses_lessons():
    day = DayOverride(chat_id=CHAT_ID, date=DATE, day_type="free", note="Семейный день")
    eff = compute_effective_day(DATE, _slots(), _schedule(), day, [])
    assert eff.is_special is True
    assert eff.lessons == []
    assert eff.has_lessons is False
    assert eff.day_note == "Семейный день"


def test_holiday_and_vacation_are_special():
    for day_type in ("holiday", "vacation"):
        day = DayOverride(chat_id=CHAT_ID, date=DATE, day_type=day_type)
        eff = compute_effective_day(DATE, _slots(), _schedule(), day, [])
        assert eff.is_special is True
        assert eff.lessons == []


def test_remote_day_keeps_lessons():
    day = DayOverride(chat_id=CHAT_ID, date=DATE, day_type="remote", note="Дистанционка")
    eff = compute_effective_day(DATE, _slots(), _schedule(), day, [])
    assert eff.is_remote is True
    assert eff.is_special is False
    assert eff.has_lessons is True  # lessons still happen, just remotely


# --- DB round-trip + get_effective_day --------------------------------------

async def _setup_template(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await save_lesson_slots(chat_id, [(1, "08:00", "08:45"), (2, "08:55", "09:40")])
    await save_schedule_day(chat_id, 0, [(1, "Математика"), (2, "Физика")])


async def test_get_effective_day_applies_saved_override(db):
    await _setup_template()
    await set_lesson_override(CHAT_ID, DATE, 1, "set", subject_name="Химия")
    eff = await get_effective_day(CHAT_ID, DATE)
    assert eff.lessons[0].subject_name == "Химия"
    assert eff.lessons[0].subject_changed is True


async def test_lesson_override_upsert(db):
    await _setup_template()
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    await set_lesson_override(CHAT_ID, DATE, 1, "set", subject_name="Химия")
    rows = await get_lesson_overrides(CHAT_ID, DATE)
    assert len(rows) == 1  # upserted, not duplicated
    assert rows[0].action == "set"
    assert rows[0].subject_name == "Химия"


async def test_day_override_upsert_and_clear(db):
    await _setup_template()
    await set_day_override(CHAT_ID, DATE, "holiday", note="x")
    await set_day_override(CHAT_ID, DATE, "free", note="y")
    row = await get_day_override(CHAT_ID, DATE)
    assert row.day_type == "free"
    assert row.note == "y"
    assert await clear_day_override(CHAT_ID, DATE) is True
    assert await get_day_override(CHAT_ID, DATE) is None
    assert await clear_day_override(CHAT_ID, DATE) is False


async def test_delete_lesson_override(db):
    await _setup_template()
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    assert await delete_lesson_override(CHAT_ID, DATE, 1) is True
    assert await get_lesson_overrides(CHAT_ID, DATE) == []
    assert await delete_lesson_override(CHAT_ID, DATE, 1) is False


async def test_clear_date_overrides_removes_both(db):
    await _setup_template()
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    await set_day_override(CHAT_ID, DATE, "remote")
    assert await clear_date_overrides(CHAT_ID, DATE) is True
    assert await get_lesson_overrides(CHAT_ID, DATE) == []
    assert await get_day_override(CHAT_ID, DATE) is None
    assert await clear_date_overrides(CHAT_ID, DATE) is False


async def test_get_override_dates(db):
    await _setup_template()
    earlier = DATE - datetime.timedelta(days=10)
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    await set_day_override(CHAT_ID, earlier, "holiday")
    all_dates = await get_override_dates(CHAT_ID)
    assert all_dates == sorted([earlier, DATE])
    only_future = await get_override_dates(CHAT_ID, since=DATE)
    assert only_future == [DATE]


# --- Chat isolation ---------------------------------------------------------

async def test_overrides_isolated_by_chat(db):
    await _setup_template(CHAT_ID)
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await set_lesson_override(CHAT_ID, DATE, 1, "set", subject_name="Химия")
    # Another chat must not see it.
    assert await get_lesson_overrides(OTHER_CHAT_ID, DATE) == []
    # ...and deleting from the other chat is a no-op.
    assert await delete_lesson_override(OTHER_CHAT_ID, DATE, 1) is False
    assert len(await get_lesson_overrides(CHAT_ID, DATE)) == 1


# --- Cascade delete + migration ---------------------------------------------

async def test_cascade_delete_removes_overrides(db):
    await _setup_template()
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    await set_day_override(CHAT_ID, DATE, "holiday")
    await delete_chat(CHAT_ID)
    async with db() as session:
        lessons = (await session.execute(
            select(LessonOverride).where(LessonOverride.chat_id == CHAT_ID)
        )).scalars().all()
        days = (await session.execute(
            select(DayOverride).where(DayOverride.chat_id == CHAT_ID)
        )).scalars().all()
        assert lessons == []
        assert days == []


async def test_migrate_chat_moves_overrides(db):
    old_id, new_id = -5001, -6002
    await get_or_create_chat(old_id, "group")
    await set_lesson_override(old_id, DATE, 1, "set", subject_name="Химия")
    await set_day_override(old_id, DATE, "remote")

    assert await migrate_chat(old_id, new_id) is True
    assert await get_lesson_overrides(old_id, DATE) == []
    assert await get_day_override(old_id, DATE) is None
    assert len(await get_lesson_overrides(new_id, DATE)) == 1
    assert (await get_day_override(new_id, DATE)).day_type == "remote"


# --- DB CHECK constraints ---------------------------------------------------

async def test_bad_day_type_rejected(db):
    await get_or_create_chat(CHAT_ID, "private")
    async with db() as session:
        session.add(DayOverride(chat_id=CHAT_ID, date=DATE, day_type="bogus"))
        with pytest.raises(Exception):
            await session.commit()


async def test_bad_action_rejected(db):
    await get_or_create_chat(CHAT_ID, "private")
    async with db() as session:
        session.add(LessonOverride(chat_id=CHAT_ID, date=DATE, lesson_number=1, action="bogus"))
        with pytest.raises(Exception):
            await session.commit()
