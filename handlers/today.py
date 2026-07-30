import datetime
from dataclasses import dataclass, field
from typing import List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.db import get_lesson_slots, get_homework, get_extra_activities
from database.models import ExtraActivity, Homework
from handlers.extra import activities_on_date, format_extra_activities_block
from services.effective_schedule import (
    EffectiveDay, get_effective_day, format_effective_schedule_body,
)
from keyboards.inline import DAYS_RU
import services.timeservice as ts
from utils import html_escape, send_long_message

router = Router()

# Cap on how many "upcoming" homework items are shown, so the screen stays a
# quick glance rather than turning into a full homework list.
UPCOMING_LIMIT = 5


@dataclass
class TodayData:
    weekday: int
    effective: EffectiveDay
    has_slots: bool = False
    homework_today: List[Homework] = field(default_factory=list)
    overdue: List[Homework] = field(default_factory=list)
    upcoming: List[Homework] = field(default_factory=list)
    extra_activities: List[ExtraActivity] = field(default_factory=list)


async def get_today_data(chat_id: int, today: datetime.date) -> TodayData:
    """
    Gathers everything needed for the "Today" screen. All queries are scoped
    to ``chat_id``. ``today`` is passed in (rather than computed here) so the
    caller controls the timezone-aware "now".

    The schedule is the *effective* one — the weekly template with any per-date
    overrides (cancellations, substitutions, free/holiday days, one-off
    lessons) already applied (see services/effective_schedule.py).
    """
    weekday = today.weekday()  # Monday=0 ... Sunday=6, same indexing as DAYS_RU.

    slots = await get_lesson_slots(chat_id)
    effective = await get_effective_day(chat_id, today)
    incomplete = await get_homework(chat_id, is_completed=False)
    extra = activities_on_date(await get_extra_activities(chat_id), today)

    homework_today = [hw for hw in incomplete if hw.due_date == today]
    overdue = sorted((hw for hw in incomplete if hw.due_date < today), key=lambda hw: hw.due_date)
    upcoming = sorted(
        (hw for hw in incomplete if hw.due_date > today), key=lambda hw: hw.due_date
    )[:UPCOMING_LIMIT]

    return TodayData(
        weekday=weekday,
        effective=effective,
        has_slots=bool(slots),
        homework_today=homework_today,
        overdue=overdue,
        upcoming=upcoming,
        extra_activities=extra,
    )


def _format_hw_line(hw: Homework, prefix_emoji: str, date_label: str) -> str:
    safe_subject = html_escape(hw.subject_name)
    safe_desc = html_escape(hw.description)
    due_str = hw.due_date.strftime("%d.%m")
    return f"{prefix_emoji} <b>{safe_subject}</b> ({date_label} {due_str}): <i>{safe_desc}</i>"


def format_today_message(data: TodayData, today: datetime.date) -> str:
    """
    Pure formatting function: turns already-fetched ``TodayData`` into the
    final message text. No DB/network access, so it's trivially testable.
    """
    day_name = DAYS_RU[data.weekday]
    sections = [f"📚 <b>Сегодня — {day_name}, {today.strftime('%d.%m.%Y')}</b>"]

    # --- Schedule (effective: template + any per-date overrides) ---
    if not data.has_slots and data.effective.day_type is None:
        schedule_body = "⚠️ Время уроков еще не настроено."
    else:
        schedule_body = format_effective_schedule_body(
            data.effective, no_lessons_text="🥱 Сегодня нет уроков!"
        )
    sections.append("🗓 <b>Расписание на сегодня:</b>\n" + schedule_body)

    # --- Extra activities (clubs / tutors / sections) ---
    extra_block = format_extra_activities_block(data.extra_activities)
    if extra_block:
        sections.append(extra_block)

    # --- Homework due today ---
    if data.homework_today:
        lines = [
            _format_hw_line(hw, "⏳", "до")
            for hw in data.homework_today
        ]
        sections.append("⏳ <b>ДЗ на сегодня:</b>\n" + "\n".join(lines))

    # --- Overdue homework ---
    if data.overdue:
        lines = [_format_hw_line(hw, "🔥", "было до") for hw in data.overdue]
        sections.append("🔥 <b>Просроченные задания:</b>\n" + "\n".join(lines))

    # --- Upcoming homework ---
    if data.upcoming:
        lines = [_format_hw_line(hw, "📌", "до") for hw in data.upcoming]
        sections.append("📅 <b>Ближайшие задания:</b>\n" + "\n".join(lines))

    if not data.homework_today and not data.overdue and not data.upcoming:
        sections.append("🎉 Никаких активных заданий не найдено!")

    return "\n\n".join(sections)


@router.message(Command("today"))
@router.message(F.text == "📚 Сегодня")
async def show_today(message: Message, state: FSMContext):
    await state.clear()
    # "Today" is this chat's today: the date is resolved in the chat's own
    # timezone, never a single global one.
    today = await ts.today_for_chat_id(message.chat.id)
    data = await get_today_data(message.chat.id, today)
    text = format_today_message(data, today)
    await send_long_message(message, text, parse_mode="HTML")
