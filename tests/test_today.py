"""Tests for the "📚 Сегодня" (Today) screen."""
import datetime

import pytz

from config import TIMEZONE
from database.db import (
    get_or_create_chat, set_onboarded, add_homework,
    save_schedule_day, save_lesson_slots,
)
from handlers.today import get_today_data, format_today_message
from utils import split_message, MAX_MESSAGE_LENGTH

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 555


async def _onboarded_chat(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)


async def test_normal_school_day(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45"), (2, "08:55", "09:40")])
    await save_schedule_day(CHAT_ID, today.weekday(), [(1, "Математика"), (2, "Физика")])

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Математика" in text
    assert "Физика" in text
    assert "08:00 - 08:45" in text
    assert "Время уроков еще не настроено" not in text
    assert "нет уроков" not in text


async def test_day_without_lessons(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    other_day = (today.weekday() + 1) % 7
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    # Schedule exists, but only for a different day of the week.
    await save_schedule_day(CHAT_ID, other_day, [(1, "Математика")])

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Сегодня нет уроков" in text
    assert "Математика" not in text


async def test_homework_due_today(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    await add_homework(CHAT_ID, "Химия", today, "выучить формулы")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "ДЗ на сегодня" in text
    assert "Химия" in text
    assert "выучить формулы" in text
    assert len(data.homework_today) == 1
    assert data.overdue == []


async def test_overdue_homework(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    overdue_date = today - datetime.timedelta(days=3)
    await add_homework(CHAT_ID, "История", overdue_date, "реферат")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Просроченные задания" in text
    assert "История" in text
    assert len(data.overdue) == 1
    assert data.homework_today == []


async def test_upcoming_homework(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    upcoming_date = today + datetime.timedelta(days=2)
    await add_homework(CHAT_ID, "Биология", upcoming_date, "гербарий")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Ближайшие задания" in text
    assert "Биология" in text
    assert len(data.upcoming) == 1


async def test_no_data_at_all(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Время уроков еще не настроено" in text
    assert "Никаких активных заданий не найдено" in text


async def test_sunday_shows_sunday_not_monday(db):
    await _onboarded_chat()
    # Find the next Sunday (weekday() == 6) relative to today.
    today = datetime.datetime.now(tz).date()
    days_ahead = (6 - today.weekday()) % 7
    sunday = today + datetime.timedelta(days=days_ahead if days_ahead else 7)
    assert sunday.weekday() == 6

    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, 0, [(1, "Понедельничный урок")])  # Monday only
    await save_schedule_day(CHAT_ID, 6, [(1, "Воскресный урок")])

    data = await get_today_data(CHAT_ID, sunday)
    text = format_today_message(data, sunday)

    assert data.weekday == 6
    assert "Воскресенье" in text
    assert "Воскресный урок" in text
    assert "Понедельничный урок" not in text


async def test_markdown_is_escaped(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()
    await add_homework(CHAT_ID, "Матем*атика", today, "стр. 5, *важно* [срочно]")

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)

    assert "Матем\\*атика" in text
    assert "\\*важно\\*" in text
    assert "\\[срочно]" in text


async def test_long_text_respects_telegram_limit(db):
    await _onboarded_chat()
    today = datetime.datetime.now(tz).date()

    # Overdue items are not capped like "upcoming" is, so pile up enough of
    # them (with long descriptions) to blow past the 4096-char hard limit.
    long_desc = "детали задания " * 40
    for i in range(30):
        due = today - datetime.timedelta(days=i + 1)
        await add_homework(CHAT_ID, f"Предмет {i}", due, long_desc)

    data = await get_today_data(CHAT_ID, today)
    text = format_today_message(data, today)
    assert len(text) > MAX_MESSAGE_LENGTH

    chunks = split_message(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= MAX_MESSAGE_LENGTH
