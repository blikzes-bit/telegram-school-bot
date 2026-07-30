"""
Display integration for per-date changes: the effective schedule on the
"Сегодня" screen and in the tomorrow ("portfolio") reminder — cancellations
(struck-through), substitutions, free/holiday days (with reason), and HTML
escaping of override text.
"""
import datetime

import pytz

from config import TIMEZONE
from database.db import (
    get_or_create_chat, set_onboarded, save_lesson_slots, save_schedule_day,
    set_lesson_override, set_day_override, add_extra_activity,
)
from handlers.today import get_today_data, format_today_message
from services.scheduler import send_schedule_reminder

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 989898


async def _setup(chat_id=CHAT_ID, day_of_week=0):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)
    await save_lesson_slots(chat_id, [(1, "08:00", "08:45"), (2, "08:55", "09:40")])
    await save_schedule_day(chat_id, day_of_week, [(1, "Математика"), (2, "Физика")])


# --- Today screen -----------------------------------------------------------

async def test_today_shows_cancelled_struck_through(db):
    today = datetime.datetime.now(tz).date()
    await _setup(day_of_week=today.weekday())
    await set_lesson_override(CHAT_ID, today, 1, "cancel")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "<s>Математика</s>" in text
    assert "(отменён)" in text
    assert "Физика" in text  # the other lesson still shown


async def test_today_substitution_shows_new_subject(db):
    today = datetime.datetime.now(tz).date()
    await _setup(day_of_week=today.weekday())
    await set_lesson_override(CHAT_ID, today, 1, "set", subject_name="Химия")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Химия" in text
    # The template subject for that slot must be gone.
    assert "Математика" not in text


async def test_today_free_day_shows_reason(db):
    today = datetime.datetime.now(tz).date()
    await _setup(day_of_week=today.weekday())
    await set_day_override(CHAT_ID, today, "free", note="Семейный день")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Свободный день" in text
    assert "Семейный день" in text
    # No lessons should be listed on a free day.
    assert "Математика" not in text
    assert "Физика" not in text


async def test_today_holiday_without_template(db):
    """A holiday must show even for a chat that has no schedule for that weekday."""
    await get_or_create_chat(CHAT_ID, "private")
    await set_onboarded(CHAT_ID, True)
    today = datetime.datetime.now(tz).date()
    await set_day_override(CHAT_ID, today, "holiday", note="8 Марта")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Праздник" in text
    assert "8 Марта" in text


async def test_today_override_html_escaped(db):
    today = datetime.datetime.now(tz).date()
    await _setup(day_of_week=today.weekday())
    await set_lesson_override(CHAT_ID, today, 1, "set", subject_name="<b>Хак</b>", note="a & b")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "&lt;b&gt;Хак&lt;/b&gt;" in text
    assert "a &amp; b" in text
    assert "<b>Хак</b>" not in text


async def test_today_remote_day_keeps_lessons(db):
    today = datetime.datetime.now(tz).date()
    await _setup(day_of_week=today.weekday())
    await set_day_override(CHAT_ID, today, "remote", note="Zoom")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Дистанционный день" in text
    assert "Математика" in text  # lessons still shown on a remote day


# --- Tomorrow ("portfolio") reminder ----------------------------------------

async def test_reminder_reflects_cancellation(db, fake_bot):
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await _setup(day_of_week=tomorrow.weekday())
    # Cancel BOTH lessons; the reminder must still be sent to flag the changes.
    await set_lesson_override(CHAT_ID, tomorrow, 1, "cancel")
    await set_lesson_override(CHAT_ID, tomorrow, 2, "cancel")

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    assert "(отменён)" in fake_bot.sent[0][1]


async def test_reminder_reflects_holiday(db, fake_bot):
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await _setup(day_of_week=tomorrow.weekday())
    await set_day_override(CHAT_ID, tomorrow, "holiday", note="Праздничный день")

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0][1]
    assert "Праздник" in text
    assert "Математика" not in text  # lessons suppressed on a holiday


async def test_reminder_reflects_substitution(db, fake_bot):
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await _setup(day_of_week=tomorrow.weekday())
    await set_lesson_override(CHAT_ID, tomorrow, 1, "set", subject_name="Химия")

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    text = fake_bot.sent[0][1]
    assert "Химия" in text


async def test_reminder_free_day_does_not_delete_extra(db, fake_bot):
    """A free day suppresses lessons but the extra-activities block stays."""
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await _setup(day_of_week=tomorrow.weekday())
    await set_day_override(CHAT_ID, tomorrow, "free", note="Отдых")
    await add_extra_activity(
        CHAT_ID, title="Английский", kind="weekly", start_time="18:00",
        day_of_week=tomorrow.weekday(),
    )

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    text = fake_bot.sent[0][1]
    assert "Свободный день" in text
    assert "Английский" in text  # extra activity is a separate, untouched block
