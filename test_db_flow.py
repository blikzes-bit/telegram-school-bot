"""
Isolated database flow tests.
Uses an in-memory SQLite database so the production DB is never touched.
"""
import asyncio
import datetime
import database.db as db_module
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from database.models import Base
from sqlalchemy import select, event
from database.models import Chat, LessonSlot, Schedule, Homework

# ---- Patch the module to use in-memory DB ----
_test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

@event.listens_for(_test_engine.sync_engine, "connect")
def _set_fk(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False, class_=AsyncSession)
db_module.engine = _test_engine
db_module.AsyncSessionLocal = _TestSession

# Now import DB functions (they will use the patched engine/session)
from database.db import (
    init_db, get_or_create_chat, set_onboarded,
    save_lesson_slots, get_lesson_slots,
    save_schedule_day, get_schedule,
    add_homework, get_homework, get_homework_due_on,
    mark_homework_completed, delete_chat,
    update_last_hw_reminder_date, update_last_sch_reminder_date
)

async def run_tests():
    print("Starting Database Flow Verification (in-memory)...")

    await init_db()
    print("Database initialized.")

    chat_id = 999999

    # 1. Create chat
    chat = await get_or_create_chat(chat_id, "private")
    assert chat.chat_id == chat_id
    assert chat.is_onboarded is False
    assert chat.hw_reminder_time == "18:00"
    print(f"Chat created. ID: {chat.chat_id}, HW time: {chat.hw_reminder_time}")

    # 2. Onboarding flag
    await set_onboarded(chat_id, True)
    chat = await get_or_create_chat(chat_id, "private")
    assert chat.is_onboarded is True
    print(f"Onboarding set to: {chat.is_onboarded}")

    # 3. Lesson slots
    slots_to_save = [(1, "08:30", "09:15"), (2, "09:25", "10:10"), (3, "10:20", "11:05")]
    await save_lesson_slots(chat_id, slots_to_save)
    slots = await get_lesson_slots(chat_id)
    assert len(slots) == 3
    assert slots[0].start_time == "08:30"
    print(f"Saved and retrieved {len(slots)} lesson slots.")

    # 4. Schedule (skip entry should be omitted)
    await save_schedule_day(chat_id, 0, [(1, "Mathematics"), (2, "skip"), (3, "History")])
    schedule = await get_schedule(chat_id, 0)
    assert len(schedule) == 2
    assert schedule[0].subject_name == "Mathematics"
    print(f"Saved schedule for Monday. Active lessons: {len(schedule)}")

    # 5. Homework lifecycle
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    hw = await add_homework(chat_id, "Mathematics", tomorrow, "Solve quadratic equations p.42")
    assert hw.subject_name == "Mathematics"
    active_hw = await get_homework(chat_id, is_completed=False)
    assert len(active_hw) == 1
    tomorrow_hw = await get_homework_due_on(chat_id, tomorrow)
    assert len(tomorrow_hw) == 1
    print(f"Homework added. Active: {len(active_hw)}, Due tomorrow: {len(tomorrow_hw)}")

    await mark_homework_completed(chat_id, hw.id, True)
    assert len(await get_homework(chat_id, is_completed=False)) == 0
    assert len(await get_homework(chat_id, is_completed=True)) == 1
    print("Homework marked completed.")

    # 6. Reminder date tracking
    await update_last_hw_reminder_date(chat_id, today)
    await update_last_sch_reminder_date(chat_id, today)
    async with _TestSession() as session:
        chat_obj = (await session.execute(select(Chat).where(Chat.chat_id == chat_id))).scalar_one()
        assert chat_obj.last_hw_reminder_date == today
        assert chat_obj.last_sch_reminder_date == today
    print("Reminder dates updated and verified.")

    # 7. Cascade delete
    await delete_chat(chat_id)
    async with _TestSession() as session:
        assert len((await session.execute(select(Chat).where(Chat.chat_id == chat_id))).scalars().all()) == 0
        assert len((await session.execute(select(LessonSlot).where(LessonSlot.chat_id == chat_id))).scalars().all()) == 0
        assert len((await session.execute(select(Schedule).where(Schedule.chat_id == chat_id))).scalars().all()) == 0
        assert len((await session.execute(select(Homework).where(Homework.chat_id == chat_id))).scalars().all()) == 0
    print("Cascade delete verified: all related rows removed.")

    print("\nALL DATABASE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
