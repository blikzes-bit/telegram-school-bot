"""
Display tests: the dedicated "Доп. занятия" block on the Today screen, in the
selected-day schedule view and in the tomorrow ("portfolio") reminder, plus
the pure filter helpers and the activity-time parser.
"""
import datetime

import pytest
import pytz

from config import TIMEZONE
from database.db import (
    get_or_create_chat, set_onboarded, add_extra_activity,
    save_lesson_slots, save_schedule_day,
)
from handlers.today import get_today_data, format_today_message
from handlers.schedule import format_schedule_message
from handlers.extra import (
    activities_on_date, activities_for_weekday, format_extra_activities_block,
)
from services.scheduler import send_schedule_reminder
from utils import parse_activity_time

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 606060


async def _onboarded(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)


# --- Time parsing / validation ---------------------------------------------

def test_parse_single_time():
    assert parse_activity_time("18:00") == ("18:00", None)


def test_parse_single_time_zero_pads():
    assert parse_activity_time("8:05") == ("08:05", None)


def test_parse_interval():
    assert parse_activity_time("18:00 - 19:00") == ("18:00", "19:00")


def test_parse_interval_rejects_reversed():
    with pytest.raises(ValueError):
        parse_activity_time("19:00 - 18:00")


def test_parse_rejects_garbage():
    for bad in ["", "abc", "25:00", "18:60", "18-19", "18:00 -"]:
        with pytest.raises(ValueError):
            parse_activity_time(bad)


# --- Pure filter helpers ----------------------------------------------------

async def test_activities_on_date_weekly_and_once(db):
    from database.db import get_extra_activities
    await get_or_create_chat(CHAT_ID, "private")
    monday = datetime.date(2026, 7, 27)  # a Monday (weekday 0)
    a_weekly = await add_extra_activity(CHAT_ID, title="Weekly-Mon", kind="weekly", start_time="18:00", day_of_week=0)
    a_once = await add_extra_activity(CHAT_ID, title="Once-Mon", kind="once", start_time="09:00", activity_date=monday)
    await add_extra_activity(CHAT_ID, title="Weekly-Tue", kind="weekly", start_time="10:00", day_of_week=1)

    activities = await get_extra_activities(CHAT_ID)
    on_monday = activities_on_date(activities, monday)
    titles = [a.title for a in on_monday]
    assert "Weekly-Mon" in titles
    assert "Once-Mon" in titles
    assert "Weekly-Tue" not in titles
    # Sorted by start_time.
    assert titles.index("Once-Mon") < titles.index("Weekly-Mon")
    # ids present just to reference the fixtures.
    assert a_weekly.id and a_once.id


async def test_activities_for_weekday_includes_upcoming_once(db):
    from database.db import get_extra_activities
    await get_or_create_chat(CHAT_ID, "private")
    today = datetime.date(2026, 7, 20)  # Monday
    future_monday = datetime.date(2026, 7, 27)
    past_monday = datetime.date(2026, 7, 13)
    await add_extra_activity(CHAT_ID, title="Weekly-Mon", kind="weekly", start_time="18:00", day_of_week=0)
    await add_extra_activity(CHAT_ID, title="Future-Mon", kind="once", start_time="09:00", activity_date=future_monday)
    await add_extra_activity(CHAT_ID, title="Past-Mon", kind="once", start_time="09:00", activity_date=past_monday)

    activities = await get_extra_activities(CHAT_ID)
    result = activities_for_weekday(activities, 0, today)
    titles = [a.title for a in result]
    assert "Weekly-Mon" in titles
    assert "Future-Mon" in titles      # upcoming one-off on that weekday
    assert "Past-Mon" not in titles    # past one-off excluded


def test_format_block_empty():
    assert format_extra_activities_block([]) == ""


# --- Today screen -----------------------------------------------------------

async def test_today_shows_extra_block(db):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    await add_extra_activity(
        CHAT_ID, title="Английский", kind="weekly", start_time="18:00",
        day_of_week=today.weekday(), end_time="19:00", location="Каб. 5",
    )
    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Доп. занятия" in text
    assert "Английский" in text
    assert "18:00 - 19:00" in text
    assert "Каб. 5" in text


async def test_today_no_extra_block_when_none(db):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "Доп. занятия" not in text


async def test_today_extra_html_escaped(db):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    await add_extra_activity(
        CHAT_ID, title="<b>Хак</b>", kind="weekly", start_time="18:00",
        day_of_week=today.weekday(), note="a & b",
    )
    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert "&lt;b&gt;Хак&lt;/b&gt;" in text
    assert "a &amp; b" in text
    assert "<b>Хак</b>" not in text


# --- Selected-day schedule view ---------------------------------------------

async def test_schedule_day_view_shows_extra(db):
    await _onboarded()
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, 0, [(1, "Математика")])
    await add_extra_activity(CHAT_ID, title="Шахматы", kind="weekly", start_time="15:00", day_of_week=0)

    text = await format_schedule_message(CHAT_ID, 0)
    assert "Доп. занятия" in text
    assert "Шахматы" in text


async def test_schedule_day_view_extra_without_lessons(db):
    """Even with no lesson slots configured, extra activities still surface."""
    await _onboarded()
    await add_extra_activity(CHAT_ID, title="Танцы", kind="weekly", start_time="16:00", day_of_week=2)
    text = await format_schedule_message(CHAT_ID, 2)
    assert "Танцы" in text


# --- Tomorrow ("portfolio") reminder ----------------------------------------

async def test_schedule_reminder_includes_extra(db, fake_bot):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, tomorrow.weekday(), [(1, "Математика")])
    await add_extra_activity(
        CHAT_ID, title="Английский", kind="weekly", start_time="18:00", day_of_week=tomorrow.weekday()
    )

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0][1]
    assert "Математика" in text
    assert "Доп. занятия" in text
    assert "Английский" in text


async def test_schedule_reminder_extra_only_without_lessons(db, fake_bot):
    """No lessons tomorrow but an extra activity → still sent (extra block only)."""
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await add_extra_activity(
        CHAT_ID, title="Секция", kind="weekly", start_time="17:00", day_of_week=tomorrow.weekday()
    )
    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    assert "Секция" in fake_bot.sent[0][1]


async def test_schedule_reminder_nothing_when_no_data(db, fake_bot):
    await _onboarded()
    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert fake_bot.sent == []
