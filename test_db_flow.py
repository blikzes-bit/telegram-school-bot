import asyncio
import datetime
from database.db import init_db, get_or_create_chat, set_onboarded, save_lesson_slots, get_lesson_slots
from database.db import save_schedule_day, get_schedule, add_homework, get_homework, get_homework_due_on, mark_homework_completed, delete_chat
from database.db import AsyncSessionLocal
from sqlalchemy import select
from database.models import Chat, LessonSlot, Schedule, Homework

async def run_tests():
    print("🚀 Starting Database Flow Verification...")
    
    # 1. Initialize database
    await init_db()
    print("✅ Database initialized.")
    
    chat_id = 999999
    
    # 2. Get or create chat
    chat = await get_or_create_chat(chat_id, "private")
    print(f"✅ Chat created. ID: {chat.chat_id}, Onboarded: {chat.is_onboarded}, HW time: {chat.hw_reminder_time}")
    assert chat.chat_id == chat_id
    assert chat.is_onboarded is False
    assert chat.hw_reminder_time == "18:00"
    
    # 3. Set onboarded
    await set_onboarded(chat_id, True)
    chat = await get_or_create_chat(chat_id, "private")
    print(f"✅ Chat onboarding status updated to: {chat.is_onboarded}")
    assert chat.is_onboarded is True
    
    # 4. Save and get lesson slots
    slots_to_save = [
        (1, "08:30", "09:15"),
        (2, "09:25", "10:10"),
        (3, "10:20", "11:05")
    ]
    await save_lesson_slots(chat_id, slots_to_save)
    slots = await get_lesson_slots(chat_id)
    print(f"✅ Saved and retrieved {len(slots)} lesson slots.")
    assert len(slots) == 3
    assert slots[0].start_time == "08:30"
    assert slots[1].end_time == "10:10"
    
    # 5. Save and get schedule
    lessons_to_save = [
        (1, "Mathematics"),
        (2, "skip"),  # should be skipped
        (3, "History")
    ]
    await save_schedule_day(chat_id, 0, lessons_to_save)  # Monday = 0
    schedule = await get_schedule(chat_id, 0)
    print(f"✅ Saved schedule for Monday. Retrieved {len(schedule)} active lessons.")
    assert len(schedule) == 2  # Mathematics and History, skip is omitted
    assert schedule[0].subject_name == "Mathematics"
    assert schedule[1].lesson_number == 3
    assert schedule[1].subject_name == "History"
    
    # 6. Homework lifecycle
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    
    hw = await add_homework(chat_id, "Mathematics", tomorrow, "Solve quadratic equations on page 42.")
    print(f"✅ Homework added. Subject: {hw.subject_name}, Due: {hw.due_date}")
    assert hw.subject_name == "Mathematics"
    assert hw.due_date == tomorrow
    
    # Get active homework
    active_hw = await get_homework(chat_id, is_completed=False)
    print(f"✅ Retrieved {len(active_hw)} active homework tasks.")
    assert len(active_hw) == 1
    assert active_hw[0].id == hw.id
    
    # Get HW due tomorrow
    tomorrow_hw = await get_homework_due_on(chat_id, tomorrow)
    print(f"✅ Retrieved {len(tomorrow_hw)} tasks due tomorrow.")
    assert len(tomorrow_hw) == 1
    
    # Mark completed
    await mark_homework_completed(chat_id, hw.id, True)
    active_hw_after = await get_homework(chat_id, is_completed=False)
    archive_hw = await get_homework(chat_id, is_completed=True)
    print(f"✅ Marked homework completed. Active tasks remaining: {len(active_hw_after)}, Archived: {len(archive_hw)}")
    assert len(active_hw_after) == 0
    assert len(archive_hw) == 1
    
    # 7. Test Cascade Delete
    await delete_chat(chat_id)
    print("✅ Chat deleted. Verifying cascade delete...")
    
    async with AsyncSessionLocal() as session:
        # Check if chat, slots, schedule, or homework remain
        chats_rem = (await session.execute(select(Chat).where(Chat.chat_id == chat_id))).scalars().all()
        slots_rem = (await session.execute(select(LessonSlot).where(LessonSlot.chat_id == chat_id))).scalars().all()
        sched_rem = (await session.execute(select(Schedule).where(Schedule.chat_id == chat_id))).scalars().all()
        hw_rem = (await session.execute(select(Homework).where(Homework.chat_id == chat_id))).scalars().all()
        
        print(f"   Chats remaining: {len(chats_rem)}")
        print(f"   Slots remaining: {len(slots_rem)}")
        print(f"   Schedule items remaining: {len(sched_rem)}")
        print(f"   Homework items remaining: {len(hw_rem)}")
        
        assert len(chats_rem) == 0
        assert len(slots_rem) == 0
        assert len(sched_rem) == 0
        assert len(hw_rem) == 0
        
    print("\n🎉 ALL DATABASE TESTS PASSED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    asyncio.run(run_tests())
