"""
DB-layer tests for extra activities (🎯 Доп. занятия): CRUD, chat_id
isolation, recurring vs one-off, cascade delete, chat migration, and the
guarantee that re-running the school onboarding never touches them.
"""
import datetime

import pytest
from sqlalchemy import select

from database.db import (
    get_or_create_chat, delete_chat, finalize_onboarding, migrate_chat,
    add_extra_activity, get_extra_activities, get_extra_activity_by_id,
    update_extra_activity, delete_extra_activity, get_extra_activities_for_chats,
    get_lesson_slots, get_schedule,
)
from database.models import ExtraActivity, Chat

CHAT_ID = 424242
OTHER_CHAT_ID = 424243


async def _weekly(chat_id=CHAT_ID, title="Английский", day=1, start="18:00", end="19:00"):
    await get_or_create_chat(chat_id, "private")
    return await add_extra_activity(
        chat_id, title=title, kind="weekly", start_time=start,
        day_of_week=day, end_time=end, location="Каб. 5", note="слова",
    )


async def _once(chat_id=CHAT_ID, title="Репетитор", date=None, start="16:00"):
    await get_or_create_chat(chat_id, "private")
    date = date or datetime.date(2026, 10, 14)
    return await add_extra_activity(
        chat_id, title=title, kind="once", start_time=start, activity_date=date,
    )


# --- CRUD -------------------------------------------------------------------

async def test_add_weekly_roundtrip(db):
    a = await _weekly()
    assert a.id is not None
    got = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert got.title == "Английский"
    assert got.kind == "weekly"
    assert got.day_of_week == 1
    assert got.activity_date is None
    assert got.start_time == "18:00"
    assert got.end_time == "19:00"
    assert got.location == "Каб. 5"
    assert got.note == "слова"


async def test_add_once_roundtrip(db):
    a = await _once()
    got = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert got.kind == "once"
    assert got.activity_date == datetime.date(2026, 10, 14)
    assert got.day_of_week is None
    assert got.end_time is None  # optional end time may be omitted


async def test_add_optional_fields_default_none(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="Плавание", kind="weekly", start_time="07:00", day_of_week=3)
    got = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert got.end_time is None
    assert got.location is None
    assert got.note is None


async def test_get_extra_activities_scoped_and_ordered(db):
    await get_or_create_chat(CHAT_ID, "private")
    await add_extra_activity(CHAT_ID, title="B", kind="weekly", start_time="19:00", day_of_week=1)
    await add_extra_activity(CHAT_ID, title="A", kind="weekly", start_time="08:00", day_of_week=1)
    activities = await get_extra_activities(CHAT_ID)
    assert [a.start_time for a in activities] == ["08:00", "19:00"]


async def test_update_extra_activity(db):
    a = await _weekly()
    ok = await update_extra_activity(CHAT_ID, a.id, title="Немецкий", start_time="17:00", end_time=None)
    assert ok is True
    got = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert got.title == "Немецкий"
    assert got.start_time == "17:00"
    assert got.end_time is None


async def test_update_no_values_is_noop(db):
    a = await _weekly()
    assert await update_extra_activity(CHAT_ID, a.id) is False


async def test_delete_extra_activity(db):
    a = await _weekly()
    assert await delete_extra_activity(CHAT_ID, a.id) is True
    assert await get_extra_activity_by_id(CHAT_ID, a.id) is None
    assert await delete_extra_activity(CHAT_ID, a.id) is False  # already gone


# --- Chat isolation ---------------------------------------------------------

async def test_get_by_id_isolated_by_chat(db):
    a = await _weekly()
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    assert await get_extra_activity_by_id(OTHER_CHAT_ID, a.id) is None


async def test_update_isolated_by_chat(db):
    a = await _weekly()
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    ok = await update_extra_activity(OTHER_CHAT_ID, a.id, title="Hijacked")
    assert ok is False
    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).title == "Английский"


async def test_delete_isolated_by_chat(db):
    a = await _weekly()
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    assert await delete_extra_activity(OTHER_CHAT_ID, a.id) is False
    assert await get_extra_activity_by_id(CHAT_ID, a.id) is not None


async def test_get_all_isolated_by_chat(db):
    await _weekly(CHAT_ID)
    await _weekly(OTHER_CHAT_ID, title="Only other")
    mine = await get_extra_activities(CHAT_ID)
    assert len(mine) == 1
    assert mine[0].title == "Английский"


async def test_batch_fetch_groups_by_chat(db):
    await _weekly(CHAT_ID)
    await _once(OTHER_CHAT_ID)
    grouped = await get_extra_activities_for_chats([CHAT_ID, OTHER_CHAT_ID])
    assert len(grouped[CHAT_ID]) == 1
    assert len(grouped[OTHER_CHAT_ID]) == 1
    assert await get_extra_activities_for_chats([]) == {}


# --- Cascade delete ---------------------------------------------------------

async def test_cascade_delete_removes_extra_activities(db):
    await _weekly()
    await _once()
    await delete_chat(CHAT_ID)
    async with db() as session:
        rows = (await session.execute(
            select(ExtraActivity).where(ExtraActivity.chat_id == CHAT_ID)
        )).scalars().all()
        assert rows == []


# --- Re-onboarding must NOT delete extra activities -------------------------

async def test_reonboarding_preserves_extra_activities(db):
    await _weekly()
    await _once()
    # A full re-onboarding rewrites lesson slots + schedule for the chat.
    await finalize_onboarding(
        CHAT_ID, "private",
        lesson_slots=[(1, "08:00", "08:45")],
        schedule_by_day={0: [(1, "Математика")]},
    )
    # School data replaced...
    assert len(await get_lesson_slots(CHAT_ID)) == 1
    assert len(await get_schedule(CHAT_ID, 0)) == 1
    # ...but the extra activities survive untouched.
    assert len(await get_extra_activities(CHAT_ID)) == 2


# --- group -> supergroup migration carries extra activities -----------------

async def test_migrate_chat_moves_extra_activities(db):
    old_id, new_id = -1001, -2002
    await get_or_create_chat(old_id, "group")
    await add_extra_activity(old_id, title="Хор", kind="weekly", start_time="15:00", day_of_week=2)

    moved = await migrate_chat(old_id, new_id)
    assert moved is True
    assert await get_extra_activities(old_id) == []
    new_list = await get_extra_activities(new_id)
    assert len(new_list) == 1
    assert new_list[0].title == "Хор"


# --- DB constraints enforce the recurrence invariant ------------------------

async def test_recurrence_check_constraint_rejects_bad_row(db):
    """A weekly row must not carry a date (and vice versa) — DB-enforced."""
    await get_or_create_chat(CHAT_ID, "private")
    async with db() as session:
        session.add(ExtraActivity(
            chat_id=CHAT_ID, title="bad", kind="weekly",
            start_time="10:00", day_of_week=1,
            activity_date=datetime.date(2026, 1, 1),  # illegal for weekly
        ))
        with pytest.raises(Exception):
            await session.commit()


async def test_chat_relationship_cascade_orm(db):
    """The Chat.extra_activities relationship is wired with delete-orphan."""
    await _weekly()
    async with db() as session:
        chat = (await session.execute(select(Chat).where(Chat.chat_id == CHAT_ID))).scalar_one()
        # Lazy relationship is accessible without raising.
        await session.refresh(chat, attribute_names=["extra_activities"])
        assert len(chat.extra_activities) == 1
