import datetime
from dataclasses import dataclass, field
from typing import List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.db import (
    get_extra_activities, get_homework, get_lesson_slots, get_or_create_chat,
    get_payments,
)
from database.models import ExtraActivity, Homework, Payment
from handlers.extra import activities_on_date, format_extra_activities_block
from services.effective_schedule import (
    EffectiveDay, get_effective_day, format_effective_schedule_body,
)
from keyboards.inline import DAYS_RU
from services import profiles
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
    # Money that needs attention today. Only ever populated in a profile that
    # has a money side (the tutor profile), so every other chat is unaffected.
    payments_due: List[Payment] = field(default_factory=list)
    # ``show_schedule`` follows the chat's profile: a tutor chat has no school
    # timetable, so printing "уроков нет" every morning would be noise.
    show_schedule: bool = True


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

    chat = await get_or_create_chat(chat_id, "private")
    features = profiles.features_for(chat)

    slots = await get_lesson_slots(chat_id)
    effective = await get_effective_day(chat_id, today)
    incomplete = await get_homework(chat_id, is_completed=False)
    extra = activities_on_date(await get_extra_activities(chat_id), today)

    homework_today = [hw for hw in incomplete if hw.due_date == today]
    overdue = sorted((hw for hw in incomplete if hw.due_date < today), key=lambda hw: hw.due_date)
    upcoming = sorted(
        (hw for hw in incomplete if hw.due_date > today), key=lambda hw: hw.due_date
    )[:UPCOMING_LIMIT]

    payments_due: List[Payment] = []
    if features.payments:
        payments_due = [
            p for p in await get_payments(chat_id, is_paid=False)
            if profiles.payment_status(p.due_date, today, False, p.remind_days_before)
            in ("overdue", "due_soon")
        ]

    return TodayData(
        weekday=weekday,
        effective=effective,
        has_slots=bool(slots),
        homework_today=homework_today,
        overdue=overdue,
        upcoming=upcoming,
        extra_activities=extra,
        payments_due=payments_due,
        show_schedule=features.school_schedule,
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
    # Skipped entirely where the chat's profile has no school timetable: a tutor
    # chat would otherwise be told "уроков нет" every single morning.
    if data.show_schedule:
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

    # --- Money that needs attention (tutor profile only) ---
    if data.payments_due:
        lines = []
        for payment in data.payments_due:
            when = "сегодня" if payment.due_date == today else payment.due_date.strftime("%d.%m")
            mark = "🔴" if payment.due_date < today else "🟡"
            amount = profiles.format_amount(payment.amount_minor, payment.currency)
            lines.append(
                f"{mark} <b>{html_escape(payment.title)}</b> — "
                f"{html_escape(amount)}, {when}"
            )
        sections.append("💳 <b>Об оплате:</b>\n" + "\n".join(lines))

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
