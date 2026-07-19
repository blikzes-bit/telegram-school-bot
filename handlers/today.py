import datetime
from dataclasses import dataclass, field
from typing import List

import pytz
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.db import get_schedule, get_lesson_slots, get_homework
from database.models import Homework, LessonSlot, Schedule
from keyboards.inline import DAYS_RU
from config import TIMEZONE
from utils import escape_markdown, send_long_message

router = Router()
tz = pytz.timezone(TIMEZONE)

# Cap on how many "upcoming" homework items are shown, so the screen stays a
# quick glance rather than turning into a full homework list.
UPCOMING_LIMIT = 5


@dataclass
class TodayData:
    weekday: int
    slots: List[LessonSlot] = field(default_factory=list)
    schedule_items: List[Schedule] = field(default_factory=list)
    homework_today: List[Homework] = field(default_factory=list)
    overdue: List[Homework] = field(default_factory=list)
    upcoming: List[Homework] = field(default_factory=list)


async def get_today_data(chat_id: int, today: datetime.date) -> TodayData:
    """
    Gathers everything needed for the "Today" screen. All queries are scoped
    to ``chat_id``. ``today`` is passed in (rather than computed here) so the
    caller controls the timezone-aware "now".
    """
    weekday = today.weekday()  # Monday=0 ... Sunday=6, same indexing as DAYS_RU.

    slots = await get_lesson_slots(chat_id)
    schedule_items = await get_schedule(chat_id, weekday)
    incomplete = await get_homework(chat_id, is_completed=False)

    homework_today = [hw for hw in incomplete if hw.due_date == today]
    overdue = sorted((hw for hw in incomplete if hw.due_date < today), key=lambda hw: hw.due_date)
    upcoming = sorted(
        (hw for hw in incomplete if hw.due_date > today), key=lambda hw: hw.due_date
    )[:UPCOMING_LIMIT]

    return TodayData(
        weekday=weekday,
        slots=slots,
        schedule_items=schedule_items,
        homework_today=homework_today,
        overdue=overdue,
        upcoming=upcoming,
    )


def _format_hw_line(hw: Homework, prefix_emoji: str, date_label: str) -> str:
    safe_subject = escape_markdown(hw.subject_name)
    safe_desc = escape_markdown(hw.description)
    due_str = hw.due_date.strftime("%d.%m")
    return f"{prefix_emoji} **{safe_subject}** ({date_label} {due_str}): _{safe_desc}_"


def format_today_message(data: TodayData, today: datetime.date) -> str:
    """
    Pure formatting function: turns already-fetched ``TodayData`` into the
    final message text. No DB/network access, so it's trivially testable.
    """
    day_name = DAYS_RU[data.weekday]
    sections = [f"📚 **Сегодня — {day_name}, {today.strftime('%d.%m.%Y')}**"]

    # --- Schedule ---
    schedule_lines = []
    if not data.slots:
        schedule_lines.append("⚠️ Время уроков еще не настроено.")
    else:
        sched_map = {item.lesson_number: item.subject_name for item in data.schedule_items}
        any_lesson = False
        for slot in data.slots:
            subject = sched_map.get(slot.lesson_number)
            if subject:
                any_lesson = True
                safe_subject = escape_markdown(subject)
                schedule_lines.append(
                    f"{slot.lesson_number}️⃣ `{slot.start_time} - {slot.end_time}` | 📘 **{safe_subject}**"
                )
        if not any_lesson:
            schedule_lines.append("🥱 Сегодня нет уроков!")
    sections.append("🗓 **Расписание на сегодня:**\n" + "\n".join(schedule_lines))

    # --- Homework due today ---
    if data.homework_today:
        lines = [
            _format_hw_line(hw, "⏳", "до")
            for hw in data.homework_today
        ]
        sections.append("⏳ **ДЗ на сегодня:**\n" + "\n".join(lines))

    # --- Overdue homework ---
    if data.overdue:
        lines = [_format_hw_line(hw, "🔥", "было до") for hw in data.overdue]
        sections.append("🔥 **Просроченные задания:**\n" + "\n".join(lines))

    # --- Upcoming homework ---
    if data.upcoming:
        lines = [_format_hw_line(hw, "📌", "до") for hw in data.upcoming]
        sections.append("📅 **Ближайшие задания:**\n" + "\n".join(lines))

    if not data.homework_today and not data.overdue and not data.upcoming:
        sections.append("🎉 Никаких активных заданий не найдено!")

    return "\n\n".join(sections)


@router.message(F.text == "📚 Сегодня")
async def show_today(message: Message, state: FSMContext):
    await state.clear()
    today = datetime.datetime.now(tz).date()
    data = await get_today_data(message.chat.id, today)
    text = format_today_message(data, today)
    await send_long_message(message, text, parse_mode="Markdown")
