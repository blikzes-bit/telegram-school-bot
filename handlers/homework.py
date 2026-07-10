import datetime
import pytz
from typing import Tuple, List
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from database.db import get_homework, add_homework, mark_homework_completed, delete_homework, get_schedule
from keyboards.inline import get_homework_action_keyboard, get_cancel_keyboard, DAYS_RU
from keyboards.reply import get_main_menu
from config import TIMEZONE
from utils import escape_markdown

router = Router()
tz = pytz.timezone(TIMEZONE)

class AddHomeworkStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_description = State()
    waiting_for_due_date = State()

async def format_homework_list(chat_id: int, is_archive: bool = False) -> Tuple[str, InlineKeyboardMarkup]:
    homework_list = await get_homework(chat_id, is_completed=is_archive)

    title = "🗄️ **Архив выполненных заданий**" if is_archive else "📝 **Актуальные домашние задания**"

    if not homework_list:
        text = f"{title}\n\nНичего не найдено! 🎉"
        buttons = []
        if is_archive:
            buttons.append([InlineKeyboardButton(text="🔙 К активным заданиям", callback_data="hw_list_active")])
        else:
            buttons.append([InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add")])
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    text = f"{title}\n\n"
    buttons = []

    today = datetime.datetime.now(tz).date()

    for i, hw in enumerate(homework_list, 1):
        due_str = hw.due_date.strftime("%d.%m")
        days_left = (hw.due_date - today).days

        due_suffix = ""
        if days_left == 0:
            due_suffix = " (⏳ Сегодня!)"
        elif days_left == 1:
            due_suffix = " (⏳ Завтра)"
        elif days_left < 0:
            due_suffix = " (⚠️ Просрочено!)"

        safe_subject = escape_markdown(hw.subject_name)
        safe_desc = escape_markdown(hw.description)
        text += f"{i}️⃣ **{safe_subject}** (до {due_str}{due_suffix}):\n   _{safe_desc}_\n\n"

        buttons.append([
            InlineKeyboardButton(
                text=f"{'📁' if is_archive else '📌'} {hw.subject_name} ({due_str})",
                callback_data=f"hw_view_actions:{hw.id}:{1 if is_archive else 0}"
            )
        ])

    if is_archive:
        buttons.append([InlineKeyboardButton(text="🔙 К активным заданиям", callback_data="hw_list_active")])
    else:
        buttons.append([
            InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add"),
            InlineKeyboardButton(text="🗄️ Архив", callback_data="hw_archive")
        ])
        buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="hw_list_active")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

@router.message(F.text == "📝 Домашнее задание")
async def show_homework(message: Message):
    text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "hw_list_active")
async def process_hw_list_active(callback: CallbackQuery):
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "hw_archive")
async def process_hw_archive(callback: CallbackQuery):
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=True)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("hw_view_actions:"))
async def process_hw_view_actions(callback: CallbackQuery):
    parts = callback.data.split(":")
    hw_id = int(parts[1])
    is_archive = int(parts[2]) == 1

    kb = get_homework_action_keyboard(hw_id, is_archive)
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад к списку",
            callback_data="hw_archive" if is_archive else "hw_list_active"
        )
    ])

    await callback.message.edit_text(
        "⚙️ **Выберите действие для этого домашнего задания:**",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("hw_complete:"))
async def process_hw_complete(callback: CallbackQuery):
    hw_id = int(callback.data.split(":")[1])
    await mark_homework_completed(callback.message.chat.id, hw_id, is_completed=True)
    await callback.answer("Задание отмечено как выполненное! 🎉")
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("hw_restore:"))
async def process_hw_restore(callback: CallbackQuery):
    hw_id = int(callback.data.split(":")[1])
    await mark_homework_completed(callback.message.chat.id, hw_id, is_completed=False)
    await callback.answer("Задание возвращено в активный список.")
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=True)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("hw_delete:"))
async def process_hw_delete(callback: CallbackQuery):
    hw_id = int(callback.data.split(":")[1])
    await delete_homework(callback.message.chat.id, hw_id)
    await callback.answer("Задание успешно удалено.")
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# ----------- ADD HOMEWORK FSM -----------

@router.callback_query(F.data == "hw_add")
async def initiate_add_homework(callback: CallbackQuery, state: FSMContext):
    chat_id = callback.message.chat.id
    schedule = await get_schedule(chat_id)
    # Get unique subject names from schedule, preserve order
    seen = set()
    subjects: List[str] = []
    for s in schedule:
        if s.subject_name and s.subject_name not in seen:
            seen.add(s.subject_name)
            subjects.append(s.subject_name)

    # Store subjects in state to use safe index-based callbacks
    await state.update_data(hw_subjects=subjects)
    await state.set_state(AddHomeworkStates.waiting_for_subject)

    buttons = []
    row = []
    for idx, sub in enumerate(subjects):
        # Use index in callback to avoid 64-byte limit issues with long subject names
        row.append(InlineKeyboardButton(text=sub, callback_data=f"hwa_sub:{idx}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")])

    await callback.message.edit_text(
        "➕ **Добавление домашнего задания**\n\n"
        "Выберите предмет из списка ниже или введите название предмета вручную:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(AddHomeworkStates.waiting_for_subject, F.data.startswith("hwa_sub:"))
async def process_subject_callback(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subjects = data.get("hw_subjects", [])

    if idx >= len(subjects):
        await callback.answer("Предмет не найден.", show_alert=True)
        return

    subject = subjects[idx]
    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)

    await callback.message.edit_text(
        f"📝 Предмет: **{escape_markdown(subject)}**\n\nВведите текст домашнего задания:",
        reply_markup=get_cancel_keyboard(callback_data="hw_list_active"),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(AddHomeworkStates.waiting_for_subject)
async def process_subject_text(message: Message, state: FSMContext):
    subject = message.text.strip()
    if subject == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)

    await message.answer(
        f"📝 Предмет: **{escape_markdown(subject)}**\n\nВведите текст домашнего задания:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode="Markdown"
    )

@router.message(AddHomeworkStates.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if description == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    await state.update_data(hw_description=description)
    data = await state.get_data()
    subject = data["hw_subject"]

    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    day_after = today + datetime.timedelta(days=2)

    # Find next lesson date for this subject
    schedule = await get_schedule(message.chat.id)
    subject_days = [s.day_of_week for s in schedule if s.subject_name.lower() == subject.lower()]

    next_lesson_date = None
    if subject_days:
        current_weekday = today.weekday()
        diffs = [(day - current_weekday) % 7 for day in subject_days]
        diffs = [d if d != 0 else 7 for d in diffs]
        next_lesson_date = today + datetime.timedelta(days=min(diffs))

    buttons = [
        [
            InlineKeyboardButton(text=f"Завтра ({tomorrow.strftime('%d.%m')})", callback_data=f"hwa_date:{tomorrow.isoformat()}"),
            InlineKeyboardButton(text=f"Послезавтра ({day_after.strftime('%d.%m')})", callback_data=f"hwa_date:{day_after.isoformat()}")
        ]
    ]

    if next_lesson_date and next_lesson_date not in [tomorrow, day_after]:
        day_name = DAYS_RU[next_lesson_date.weekday()]
        buttons.append([
            InlineKeyboardButton(
                text=f"След. урок ({day_name} {next_lesson_date.strftime('%d.%m')})",
                callback_data=f"hwa_date:{next_lesson_date.isoformat()}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")])
    await state.set_state(AddHomeworkStates.waiting_for_due_date)

    safe_sub = escape_markdown(subject)
    safe_desc = escape_markdown(description)
    await message.answer(
        f"📝 Предмет: **{safe_sub}**\n"
        f"📋 Задание: _{safe_desc}_\n\n"
        "Выбери дату сдачи или введи вручную в формате `ДД.ММ` (например, `14.10`):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="Markdown"
    )

@router.callback_query(AddHomeworkStates.waiting_for_due_date, F.data.startswith("hwa_date:"))
async def process_due_date_callback(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split(":")[1]
    due_date = datetime.date.fromisoformat(date_str)

    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]

    await add_homework(callback.message.chat.id, subject, due_date, description)
    await state.clear()

    safe_sub = escape_markdown(subject)
    await callback.message.delete()
    await callback.message.answer(
        f"✅ Домашнее задание по предмету **{safe_sub}** на {due_date.strftime('%d.%m')} сохранено!",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.message(AddHomeworkStates.waiting_for_due_date)
async def process_due_date_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    try:
        day, month = map(int, text.split("."))
        today = datetime.datetime.now(tz).date()
        year = today.year
        due_date = datetime.date(year, month, day)
        # Fix #5: always push to next year if date is already in the past
        if due_date < today:
            due_date = datetime.date(year + 1, month, day)
    except Exception:
        await message.answer(
            "Неверный формат даты! Укажи дату в формате `ДД.ММ` (например, `14.10`):",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]

    await add_homework(message.chat.id, subject, due_date, description)
    await state.clear()

    safe_sub = escape_markdown(subject)
    await message.answer(
        f"✅ Домашнее задание по предмету **{safe_sub}** на {due_date.strftime('%d.%m')} сохранено!",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    hw_text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(hw_text, reply_markup=kb, parse_mode="Markdown")
