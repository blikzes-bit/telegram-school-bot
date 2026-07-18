"""Fix #1: reminder reliability and date-stamping semantics."""
import datetime

import pytz

import services.scheduler as scheduler
from services.scheduler import send_hw_reminder, send_schedule_reminder, check_and_send_reminders
from config import TIMEZONE
from database.db import (
    get_or_create_chat, set_onboarded, add_homework,
    save_schedule_day, save_lesson_slots, update_chat_reminder_times,
    get_all_chats,
)

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 777


async def _onboarded_chat(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)


async def test_hw_reminder_success(db, fake_bot):
    await _onboarded_chat()
    tomorrow = datetime.datetime.now(tz).date() + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1


async def test_hw_reminder_telegram_error_returns_false(db, failing_bot):
    await _onboarded_chat()
    tomorrow = datetime.datetime.now(tz).date() + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")

    # Must not raise; must report failure so the scheduler retries later.
    handled = await send_hw_reminder(failing_bot, CHAT_ID, tz)
    assert handled is False


async def test_hw_reminder_no_data_is_handled(db, fake_bot):
    await _onboarded_chat()
    # No homework and no schedule tomorrow → nothing to send, but "handled".
    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert fake_bot.sent == []


async def test_schedule_reminder_no_data_is_handled(db, fake_bot):
    await _onboarded_chat()
    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert fake_bot.sent == []


async def test_check_stamps_date_only_on_success(db, fake_bot, monkeypatch):
    await _onboarded_chat()
    # Force both reminders to trigger regardless of wall-clock time.
    await update_chat_reminder_times(CHAT_ID, hw_time="00:00", schedule_time="00:00")

    async def ok(*args, **kwargs):
        return True

    monkeypatch.setattr(scheduler, "send_hw_reminder", ok)
    monkeypatch.setattr(scheduler, "send_schedule_reminder", ok)

    await check_and_send_reminders(fake_bot)

    today = datetime.datetime.now(tz).date()
    chats = {c.chat_id: c for c in await get_all_chats()}
    assert chats[CHAT_ID].last_hw_reminder_date == today
    assert chats[CHAT_ID].last_sch_reminder_date == today


async def test_check_does_not_stamp_on_failure(db, failing_bot):
    await _onboarded_chat()
    await update_chat_reminder_times(CHAT_ID, hw_time="00:00", schedule_time="00:00")
    # Ensure there is content so the reminder actually attempts a send (and fails).
    tomorrow = datetime.datetime.now(tz).date() + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, tomorrow.weekday(), [(1, "Math")])

    await check_and_send_reminders(failing_bot)

    chats = {c.chat_id: c for c in await get_all_chats()}
    # Telegram failed → dates must remain unset so we retry next run.
    assert chats[CHAT_ID].last_hw_reminder_date is None
    assert chats[CHAT_ID].last_sch_reminder_date is None


async def test_check_isolates_per_chat_errors(db, fake_bot, monkeypatch):
    # Two chats; the first one blows up. The loop must still process the second.
    await _onboarded_chat(1001)
    await _onboarded_chat(1002)
    await update_chat_reminder_times(1001, hw_time="00:00", schedule_time="23:59")
    await update_chat_reminder_times(1002, hw_time="00:00", schedule_time="23:59")

    async def hw(bot, chat_id, tz_):
        if chat_id == 1001:
            raise RuntimeError("boom for 1001")
        return True

    monkeypatch.setattr(scheduler, "send_hw_reminder", hw)

    # Should not raise despite chat 1001 failing.
    await check_and_send_reminders(fake_bot)

    today = datetime.datetime.now(tz).date()
    chats = {c.chat_id: c for c in await get_all_chats()}
    assert chats[1001].last_hw_reminder_date is None      # errored → not stamped
    assert chats[1002].last_hw_reminder_date == today     # still processed
