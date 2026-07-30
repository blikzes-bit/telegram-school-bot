import datetime
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import (
    get_schedule, get_lesson_slots, update_schedule_slot, save_lesson_slots,
    get_extra_activities, get_chat, set_week_mode, copy_schedule_week,
)
from handlers.extra import activities_for_weekday, format_extra_activities_block
from services.effective_schedule import resolve_week_type, WEEK_LABELS
from keyboards.inline import get_schedule_days_keyboard, DAYS_RU, get_cancel_keyboard
from keyboards.reply import get_main_menu
from middleware.access import require_admin
import services.audit as audit
import services.timeservice as ts
from utils import (
    html_escape, safe_edit_text, safe_callback_ints,
    parse_time_interval, validate_against_previous, MAX_SUBJECT_LEN,
)

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или нажми «❌ Отмена»)."
STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой расписание заново."


class EditScheduleStates(StatesGroup):
    waiting_for_subject_name = State()
    waiting_for_lessons_count = State()
    waiting_for_lesson_times = State()


async def _week_context(chat_id: int, requested: Optional[str] = None) -> Tuple[bool, str]:
    """
    Returns ``(week_mode, week_type)`` for the schedule editor: whether the chat
    uses alternating weeks and which week to show. When ``requested`` is a valid
    'A'/'B' it is honoured; otherwise the current week (from today) is used.
    """
    chat = await get_chat(chat_id)
    week_mode = bool(chat and chat.week_mode)
    if not week_mode:
        return False, "all"
    if requested in ("A", "B"):
        return True, requested
    # Which A/B week it is depends on the chat's own today, not the server's.
    today = ts.now(chat).date()
    return True, resolve_week_type(True, chat.week_anchor_monday, today)


async def format_schedule_message(chat_id: int, day_idx: int, week_type: str = "all") -> str:
    schedule_items = await get_schedule(chat_id, day_idx, week_type=week_type)
    slots = await get_lesson_slots(chat_id)

    today = await ts.today_for_chat_id(chat_id)
    extra = activities_for_weekday(await get_extra_activities(chat_id), day_idx, today)
    extra_block = format_extra_activities_block(extra, with_date=True)

    day_name = DAYS_RU[day_idx]
    message_text = f"📅 <b>Расписание на {day_name}</b>\n"
    if week_type != "all":
        message_text += f"🗓 <i>Редактируется: {WEEK_LABELS[week_type]}</i>\n"
    message_text += "\n"

    if not slots:
        base = "⚠️ Время уроков еще не настроено. Напиши /start для настройки."
        if extra_block:
            return base + "\n\n" + extra_block
        return base

    # Map schedule items by lesson number
    sched_map = {item.lesson_number: item.subject_name for item in schedule_items}

    has_any = False
    for slot in slots:
        num = slot.lesson_number
        start = slot.start_time
        end = slot.end_time
        subject = sched_map.get(num)

        emoji = "✏️"
        if subject:
            has_any = True
            sub_lower = subject.lower()
            if "мат" in sub_lower or "алг" in sub_lower or "геом" in sub_lower:
                emoji = "📐"
            elif "физ" in sub_lower:
                emoji = "⚡️"
            elif "хим" in sub_lower or "био" in sub_lower:
                emoji = "🧪"
            elif "укр" in sub_lower or "рус" in sub_lower or "яз" in sub_lower or "лит" in sub_lower:
                emoji = "📖"
            elif "англ" in sub_lower or "eng" in sub_lower or "ин" in sub_lower:
                emoji = "🇬🇧"
            elif "ист" in sub_lower or "геогр" in sub_lower:
                emoji = "🌍"
            else:
                emoji = "📘"

        sub_text = f"<b>{html_escape(subject)}</b>" if subject else "<i>Свободно</i>"
        message_text += f"{num}️⃣ <code>{start} - {end}</code> | {emoji} {sub_text}\n"

    if not has_any:
        message_text += "\n🥱 В этот день нет уроков!"

    if extra_block:
        message_text += "\n\n" + extra_block

    return message_text


def _valid_day(day_idx) -> bool:
    return day_idx is not None and 0 <= day_idx <= 6


@router.message(Command("schedule"))
@router.message(F.text == "📅 Расписание")
async def show_schedule(message: Message, state: FSMContext):
    await state.clear()
    # Current day of week (0=Mon, 6=Sun) in this chat's own timezone.
    current_day = (await ts.today_for_chat_id(message.chat.id)).weekday()
    # Default to Monday if Sunday (since typically there are no Sunday classes)
    if current_day == 6:
        current_day = 0

    week_mode, week_type = await _week_context(message.chat.id)
    text = await format_schedule_message(message.chat.id, current_day, week_type)
    kb = get_schedule_days_keyboard(current_day, week_mode, week_type)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _requested_week(data: str, idx: int) -> Optional[str]:
    parts = data.split(":")
    return parts[idx] if len(parts) > idx and parts[idx] in ("A", "B") else None


@router.callback_query(F.data.startswith("sch_day:"))
async def process_day_select(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1)
    if ints is None or not _valid_day(ints[0]):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    day_idx = ints[0]
    week_mode, week_type = await _week_context(callback.message.chat.id, _requested_week(callback.data, 2))
    text = await format_schedule_message(callback.message.chat.id, day_idx, week_type)
    kb = get_schedule_days_keyboard(day_idx, week_mode, week_type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("sch_edit:"))
async def edit_schedule_day(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return

    ints = safe_callback_ints(callback.data, 1)
    if ints is None or not _valid_day(ints[0]):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    day_idx = ints[0]
    day_name = DAYS_RU[day_idx]
    chat_id = callback.message.chat.id
    _, week_type = await _week_context(chat_id, _requested_week(callback.data, 2))

    schedule_items = await get_schedule(chat_id, day_idx, week_type=week_type)
    slots = await get_lesson_slots(chat_id)

    if not slots:
        await callback.answer("Время уроков не настроено!", show_alert=True)
        return

    sched_map = {item.lesson_number: item.subject_name for item in schedule_items}

    buttons = []
    for slot in slots:
        num = slot.lesson_number
        subject = sched_map.get(num, "—")
        btn_text = f"Урок {num}: {subject}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"se_slot:{day_idx}:{num}:{week_type}")])

    buttons.append([InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"sch_day:{day_idx}:{week_type}")])

    header = f"✏️ <b>Редактирование: {day_name}</b>"
    if week_type != "all":
        header += f"\n🗓 <i>{WEEK_LABELS[week_type]}</i>"
    await safe_edit_text(
        callback.message,
        f"{header}\nВыберите урок, который хотите изменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("se_slot:"))
async def initiate_slot_edit(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return

    ints = safe_callback_ints(callback.data, 1, 2)
    if ints is None or not _valid_day(ints[0]) or ints[1] <= 0:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    day_idx, lesson_num = ints
    day_name = DAYS_RU[day_idx]
    _, week_type = await _week_context(callback.message.chat.id, _requested_week(callback.data, 3))

    await state.update_data(edit_day_idx=day_idx, edit_lesson_num=lesson_num, edit_week=week_type)
    await state.set_state(EditScheduleStates.waiting_for_subject_name)

    week_note = f" · {WEEK_LABELS[week_type]}" if week_type != "all" else ""
    await safe_edit_text(
        callback.message,
        f"✏️ <b>Изменение урока</b>\n\n"
        f"Вы выбрали <b>Урок №{lesson_num}</b> ({day_name}{week_note}).\n"
        f"Введите новое название предмета или напишите <code>skip</code>/<code>пропустить</code>, чтобы сделать его свободным:",
        reply_markup=get_cancel_keyboard(callback_data=f"sch_day:{day_idx}:{week_type}"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EditScheduleStates.waiting_for_subject_name, F.text)
async def process_new_subject_name(message: Message, state: FSMContext):
    subject = message.text.strip()

    if len(subject) > MAX_SUBJECT_LEN:
        await message.answer(
            f"Слишком длинное название предмета (макс. {MAX_SUBJECT_LEN} символов). Введите короче:"
        )
        return

    data = await state.get_data()
    day_idx = data["edit_day_idx"]
    lesson_num = data["edit_lesson_num"]
    edit_week = data.get("edit_week", "all")

    normalized_sub = "" if subject.lower() in ["skip", "пропустить", "-", "нет"] else subject

    await update_schedule_slot(message.chat.id, day_idx, lesson_num, normalized_sub, week_type=edit_week)
    await audit.record_event(
        message, message.chat.id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        summary=audit.summarize(
            DAYS_RU[day_idx], f"урок {lesson_num}",
            WEEK_LABELS.get(edit_week) if edit_week != "all" else None,
            normalized_sub or "освобождён",
        ),
    )
    await state.clear()

    week_mode, week_type = await _week_context(message.chat.id, edit_week)
    text = await format_schedule_message(message.chat.id, day_idx, week_type)
    kb = get_schedule_days_keyboard(day_idx, week_mode, week_type)

    await message.answer(
        f"✅ Предмет для урока №{lesson_num} обновлен!",
        reply_markup=get_main_menu()
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# --- Alternating (A/B) weeks: enable / disable / copy ------------------------

@router.callback_query(F.data == "sch_wk_on")
async def enable_week_mode(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    today = await ts.today_for_chat_id(chat_id)
    anchor = today - datetime.timedelta(days=today.weekday())  # this week's Monday = start of A
    await set_week_mode(chat_id, True, anchor_monday=anchor)
    await audit.record_event(
        callback, chat_id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        summary=audit.summarize("чередование недель включено", f"неделя A с {anchor.strftime('%d.%m.%Y')}"),
    )
    text = (
        "🔀 <b>Чередование недель включено.</b>\n\n"
        "Эта неделя — <b>A (нечётная)</b>, следующая — <b>B (чётная)</b>, и так по очереди.\n\n"
        "Можешь скопировать текущее (обычное) расписание в обе недели, а затем "
        "отредактировать различия, либо заполнить недели A и B с нуля."
    )
    kb = get_schedule_days_keyboard(today.weekday() if today.weekday() != 6 else 0, True, "A")
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Включено!")


@router.callback_query(F.data == "sch_wk_off")
async def disable_week_mode(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    await set_week_mode(chat_id, False)
    await audit.record_event(
        callback, chat_id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        summary=audit.summarize("чередование недель выключено"),
    )
    text = (
        "🔀 <b>Чередование недель выключено.</b>\n\n"
        "Снова используется обычное недельное расписание (одно на все недели). "
        "Расписания недель A и B сохранены — их можно вернуть, снова включив чередование."
    )
    kb = get_schedule_days_keyboard(0, False, "all")
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Выключено.")


@router.callback_query(F.data == "sch_copy_ab")
async def copy_regular_into_ab(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    week_mode, _ = await _week_context(chat_id)
    if not week_mode:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    copied = await copy_schedule_week(chat_id, "all", "A")
    await copy_schedule_week(chat_id, "all", "B")
    await audit.record_event(
        callback, chat_id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
        summary=audit.summarize("обычное расписание скопировано в недели A и B", f"уроков: {copied}"),
    )
    text = await format_schedule_message(chat_id, 0, "A")
    kb = get_schedule_days_keyboard(0, True, "A")
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(
        f"Скопировано уроков: {copied} в каждую неделю." if copied
        else "Обычное расписание пустое — копировать нечего."
    )


# Edit Lesson Times
@router.callback_query(F.data == "sch_edit_times")
async def initiate_edit_times(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return

    await state.set_state(EditScheduleStates.waiting_for_lessons_count)
    await safe_edit_text(
        callback.message,
        "🕒 <b>Настройка звонков</b>\n\n"
        "Сколько максимум уроков в день у тебя бывает?\n"
        "Отправь мне число от 1 до 10 (или нажми Отмена):",
        reply_markup=get_cancel_keyboard(callback_data="sch_day:0"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EditScheduleStates.waiting_for_lessons_count, F.text)
async def process_edit_lessons_count(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        lessons_count = int(text)
    except ValueError:
        await message.answer("Пожалуйста, введи число от 1 до 10.")
        return
    if not (1 <= lessons_count <= 10):
        await message.answer("Пожалуйста, введи число от 1 до 10.")
        return

    await state.update_data(lessons_count=lessons_count, current_lesson_idx=1, lesson_slots=[])
    await state.set_state(EditScheduleStates.waiting_for_lesson_times)

    await message.answer(
        "Введи время для <b>Урока №1</b> в формате <code>ЧЧ:ММ - ЧЧ:ММ</code> (например, <code>08:30 - 09:15</code>):",
        parse_mode="HTML"
    )


@router.message(EditScheduleStates.waiting_for_lesson_times, F.text)
async def process_edit_lesson_times(message: Message, state: FSMContext):
    text = message.text.strip()

    data = await state.get_data()
    lesson_slots = data.get("lesson_slots", [])
    current_lesson_idx = data.get("current_lesson_idx", 1)
    lessons_count = data.get("lessons_count")

    prev_end = lesson_slots[-1][2] if lesson_slots else None
    try:
        start, end = parse_time_interval(text)
        validate_against_previous(start, prev_end)
    except ValueError as e:
        # Leave state/accumulated slots untouched on error.
        await message.answer(f"⚠️ {html_escape(str(e))}", parse_mode="HTML")
        return

    lesson_slots = lesson_slots + [(current_lesson_idx, start, end)]
    await state.update_data(lesson_slots=lesson_slots)

    if current_lesson_idx < lessons_count:
        current_lesson_idx += 1
        await state.update_data(current_lesson_idx=current_lesson_idx)
        await message.answer(
            f"Введи время для <b>Урока №{current_lesson_idx}</b> (например, <code>09:25 - 10:10</code>):",
            parse_mode="HTML"
        )
    else:
        # Save to DB — save_lesson_slots atomically replaces slots and prunes
        # any Schedule rows above the new max lesson number in one transaction.
        await save_lesson_slots(message.chat.id, lesson_slots)
        await audit.record_event(
            message, message.chat.id, audit.ENTITY_SCHEDULE, audit.ACTION_UPDATE,
            summary=audit.summarize("время звонков", f"уроков в дне: {len(lesson_slots)}"),
        )
        await state.clear()

        await message.answer(
            "✅ Время звонков успешно обновлено!",
            reply_markup=get_main_menu()
        )

        # Display schedule for Monday (current week)
        week_mode, week_type = await _week_context(message.chat.id)
        text = await format_schedule_message(message.chat.id, 0, week_type)
        kb = get_schedule_days_keyboard(0, week_mode, week_type)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


# --- Fallback: non-text content during a schedule-edit step ---
async def schedule_non_text(message: Message):
    await message.answer(NON_TEXT_HINT)


router.message.register(
    schedule_non_text,
    StateFilter(
        EditScheduleStates.waiting_for_subject_name,
        EditScheduleStates.waiting_for_lessons_count,
        EditScheduleStates.waiting_for_lesson_times,
    ),
)
