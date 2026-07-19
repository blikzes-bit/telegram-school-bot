"""Fix #1: reminder reliability and date-stamping semantics."""
import datetime

import pytz

import services.scheduler as scheduler
from services.scheduler import send_hw_reminder, send_schedule_reminder, check_and_send_reminders
from config import TIMEZONE
from database.db import (
    get_or_create_chat, set_onboarded, add_homework,
    save_schedule_day, save_lesson_slots, update_chat_reminder_times,
    update_chat_reminder_flags, get_all_chats, mark_homework_completed,
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


async def test_hw_reminder_only_overdue(db, fake_bot):
    """Only overdue homework exists (nothing due tomorrow) -> overdue block only."""
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", yesterday, "p.10")

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0][1]
    assert "Просроченные задания" in text
    assert "Math" in text
    assert "Домашнее задание на завтра" not in text


async def test_hw_reminder_tomorrow_and_overdue_blocks(db, fake_bot):
    """Both tomorrow's homework and overdue homework exist -> both blocks present."""
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")
    await add_homework(CHAT_ID, "History", yesterday, "essay")

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0][1]
    assert "Домашнее задание на завтра" in text
    assert "Math" in text
    assert "Просроченные задания" in text
    assert "History" in text
    # Tomorrow's block must appear before the overdue block.
    assert text.index("Math") < text.index("History")


async def test_hw_reminder_completed_overdue_excluded(db, fake_bot):
    """A completed homework past its due date must not appear as overdue."""
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    hw = await add_homework(CHAT_ID, "Math", yesterday, "p.10")
    await mark_homework_completed(CHAT_ID, hw.id, True)

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    # Nothing due tomorrow, no schedule tomorrow, completed overdue excluded
    # -> genuinely nothing to send.
    assert fake_bot.sent == []


async def test_hw_reminder_overdue_only_sent_even_without_tomorrow_schedule(db, fake_bot):
    """Overdue items must be reported even when there's no lesson schedule for tomorrow."""
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", yesterday, "p.10")

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1


async def test_hw_reminder_long_message_splits(db, fake_bot):
    """Many overdue items push the message over the 4096-char limit."""
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    for i in range(80):
        await add_homework(CHAT_ID, f"Subject{i}", yesterday, "x" * 60)

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) > 1
    for _, chunk, _ in fake_bot.sent:
        assert len(chunk) <= 4096


async def test_hw_reminder_overdue_telegram_error_returns_false(db, failing_bot):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    yesterday = today - datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", yesterday, "p.10")

    handled = await send_hw_reminder(failing_bot, CHAT_ID, tz)
    assert handled is False


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


async def test_check_skips_disabled_hw_reminder(db, fake_bot, monkeypatch):
    """A disabled HW reminder must not be sent, even when its time has passed."""
    await _onboarded_chat()
    await update_chat_reminder_times(CHAT_ID, hw_time="00:00", schedule_time="23:59")
    await update_chat_reminder_flags(CHAT_ID, hw_enabled=False)

    called = {"hw": False}

    async def hw(*args, **kwargs):
        called["hw"] = True
        return True

    monkeypatch.setattr(scheduler, "send_hw_reminder", hw)

    await check_and_send_reminders(fake_bot)

    assert called["hw"] is False
    chats = {c.chat_id: c for c in await get_all_chats()}
    assert chats[CHAT_ID].last_hw_reminder_date is None


async def test_check_skips_disabled_schedule_reminder(db, fake_bot, monkeypatch):
    """A disabled schedule ("portfolio") reminder must not be sent."""
    await _onboarded_chat()
    await update_chat_reminder_times(CHAT_ID, hw_time="23:59", schedule_time="00:00")
    await update_chat_reminder_flags(CHAT_ID, schedule_enabled=False)

    called = {"sch": False}

    async def sch(*args, **kwargs):
        called["sch"] = True
        return True

    monkeypatch.setattr(scheduler, "send_schedule_reminder", sch)

    await check_and_send_reminders(fake_bot)

    assert called["sch"] is False
    chats = {c.chat_id: c for c in await get_all_chats()}
    assert chats[CHAT_ID].last_sch_reminder_date is None


async def test_check_sends_enabled_reminders_independently(db, fake_bot, monkeypatch):
    """Disabling one reminder type must not prevent the other from being sent."""
    await _onboarded_chat()
    await update_chat_reminder_times(CHAT_ID, hw_time="00:00", schedule_time="00:00")
    await update_chat_reminder_flags(CHAT_ID, hw_enabled=False, schedule_enabled=True)

    called = {"hw": False, "sch": False}

    async def hw(*args, **kwargs):
        called["hw"] = True
        return True

    async def sch(*args, **kwargs):
        called["sch"] = True
        return True

    monkeypatch.setattr(scheduler, "send_hw_reminder", hw)
    monkeypatch.setattr(scheduler, "send_schedule_reminder", sch)

    await check_and_send_reminders(fake_bot)

    assert called["hw"] is False
    assert called["sch"] is True
    today = datetime.datetime.now(tz).date()
    chats = {c.chat_id: c for c in await get_all_chats()}
    assert chats[CHAT_ID].last_hw_reminder_date is None
    assert chats[CHAT_ID].last_sch_reminder_date == today


async def test_check_isolates_per_chat_errors(db, fake_bot, monkeypatch):
    # Two chats; the first one blows up. The loop must still process the second.
    await _onboarded_chat(1001)
    await _onboarded_chat(1002)
    await update_chat_reminder_times(1001, hw_time="00:00", schedule_time="23:59")
    await update_chat_reminder_times(1002, hw_time="00:00", schedule_time="23:59")

    async def hw(bot, chat_id, tz_, **kwargs):
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
