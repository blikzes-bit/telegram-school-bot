"""
Database flow tests (the original happy-path coverage, ported to pytest).
Runs entirely against the isolated in-memory DB from the `db` fixture.
"""
import datetime

from sqlalchemy import select

from database.db import (
    get_or_create_chat, set_onboarded,
    save_lesson_slots, get_lesson_slots,
    save_schedule_day, get_schedule,
    add_homework, get_homework, get_homework_due_on,
    mark_homework_completed, delete_chat,
    update_last_hw_reminder_date, update_last_sch_reminder_date,
)
from database.models import Chat, LessonSlot, Schedule, Homework

CHAT_ID = 999999


async def test_create_chat_defaults(db):
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.chat_id == CHAT_ID
    assert chat.is_onboarded is False
    assert chat.hw_reminder_time == "18:00"
    assert chat.schedule_reminder_time == "20:00"


async def test_onboarding_flag(db):
    await get_or_create_chat(CHAT_ID, "private")
    await set_onboarded(CHAT_ID, True)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.is_onboarded is True


async def test_lesson_slots_roundtrip(db):
    await get_or_create_chat(CHAT_ID, "private")
    slots_to_save = [(1, "08:30", "09:15"), (2, "09:25", "10:10"), (3, "10:20", "11:05")]
    await save_lesson_slots(CHAT_ID, slots_to_save)
    slots = await get_lesson_slots(CHAT_ID)
    assert len(slots) == 3
    assert slots[0].start_time == "08:30"


async def test_schedule_skips_skip_entries(db):
    await get_or_create_chat(CHAT_ID, "private")
    await save_schedule_day(CHAT_ID, 0, [(1, "Mathematics"), (2, "skip"), (3, "History")])
    schedule = await get_schedule(CHAT_ID, 0)
    assert len(schedule) == 2
    assert schedule[0].subject_name == "Mathematics"


async def test_homework_lifecycle(db):
    await get_or_create_chat(CHAT_ID, "private")
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    hw = await add_homework(CHAT_ID, "Mathematics", tomorrow, "Solve quadratic equations p.42")
    assert hw.subject_name == "Mathematics"

    assert len(await get_homework(CHAT_ID, is_completed=False)) == 1
    assert len(await get_homework_due_on(CHAT_ID, tomorrow)) == 1

    await mark_homework_completed(CHAT_ID, hw.id, True)
    assert len(await get_homework(CHAT_ID, is_completed=False)) == 0
    assert len(await get_homework(CHAT_ID, is_completed=True)) == 1


async def test_reminder_dates(db):
    await get_or_create_chat(CHAT_ID, "private")
    today = datetime.date.today()
    await update_last_hw_reminder_date(CHAT_ID, today)
    await update_last_sch_reminder_date(CHAT_ID, today)
    async with db() as session:
        chat = (await session.execute(select(Chat).where(Chat.chat_id == CHAT_ID))).scalar_one()
        assert chat.last_hw_reminder_date == today
        assert chat.last_sch_reminder_date == today


async def test_cascade_delete(db):
    await get_or_create_chat(CHAT_ID, "private")
    await save_lesson_slots(CHAT_ID, [(1, "08:30", "09:15")])
    await save_schedule_day(CHAT_ID, 0, [(1, "Mathematics")])
    today = datetime.date.today()
    await add_homework(CHAT_ID, "Mathematics", today, "desc")

    await delete_chat(CHAT_ID)

    async with db() as session:
        assert (await session.execute(select(Chat).where(Chat.chat_id == CHAT_ID))).scalars().all() == []
        assert (await session.execute(select(LessonSlot).where(LessonSlot.chat_id == CHAT_ID))).scalars().all() == []
        assert (await session.execute(select(Schedule).where(Schedule.chat_id == CHAT_ID))).scalars().all() == []
        assert (await session.execute(select(Homework).where(Homework.chat_id == CHAT_ID))).scalars().all() == []
