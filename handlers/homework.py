import datetime
import pytz
from typing import Tuple
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_homework, add_homework, mark_homework_completed, delete_homework, get_schedule
from keyboards.inline import get_homework_menu_keyboard, get_homework_action_keyboard, get_cancel_keyboard, DAYS_RU
from keyboards.reply import get_main_menu
from config import TIMEZONE

router = Router()
tz = pytz.timezone(TIMEZONE)

class AddHomeworkStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_description = State()
    waiting_for_due_date = State()

async def format_homework_list(chat_id: int, is_archive: bool = False) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Returns formatted homework list and a keyboard with actions.
    """
    homework_list = await get_homework(chat_id, is_completed=is_archive)
    
    title = "🗄️ **Архив выполненных заданий**" if is_archive else "📝 **Актуальные домашние задания**"
    
    if not homework_list:
        text = f"{title}\n\nНичего не найдено! 🎉"
        # For archive, we want to go back. For active, we show add button.
        buttons = []
        if is_archive:
            buttons.append([InlineKeyboardButton(text="🔙 К активным заданиям", callback_data="hw_list_active")])
        else:
            buttons.append([InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add")])
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)
        
    text = f"{title}\n\n"
    buttons = []
    
    for i, hw in enumerate(homework_list, 1):
        due_str = hw.due_date.strftime("%d.%m")
        # Days left representation
        today = datetime.datetime.now(tz).date()
        days_left = (hw.due_date - today).days
        
        due_suffix = ""
        if days_left == 0:
            due_suffix = " (⏳ Сегодня!)"
        elif days_left == 1:
            due_suffix = " (⏳ Завтра)"
        elif days_left < 0:
            due_suffix = " (⚠️ Просрочено!)"
            
        text += f"{i}️⃣ **{hw.subject_name}** (до {due_str}{due_suffix}):\n   _{hw.description}_\n\n"
        
        # Action button for this item
        act_text = "✅" if not is_archive else "🔄"
        act_cb = f"hw_complete:{hw.id}" if not is_archive else f"hw_restore:{hw.id}"
        
        buttons.append([
            InlineKeyboardButton(text=f"{act_text} {hw.subject_name} ({due_str})", callback_data=f"hw_view_actions:{hw.id}:{1 if is_archive else 0}")
        ])
        
    # Bottom menu buttons
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
    # Add back button
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

# ADD HOMEWORK FSM FLOW
@router.callback_query(F.data == "hw_add")
async def initiate_add_homework(callback: CallbackQuery, state: FSMContext):
    chat_id = callback.message.chat.id
    # Get subjects from schedule to provide quick buttons
    schedule = await get_schedule(chat_id)
    subjects = sorted(list(set(s.subject_name for s in schedule if s.subject_name)))
    
    buttons = []
    # Display subjects as inline buttons (2 per row)
    row = []
    for sub in subjects:
        row.append(InlineKeyboardButton(text=sub, callback_data=f"hwa_sub:{sub}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")])
    
    await state.set_state(AddHomeworkStates.waiting_for_subject)
    await callback.message.edit_text(
        "➕ **Добавление домашнего задания**\n\n"
        "Выберите предмет из списка ниже или введите название предмета вручную текстовым сообщением:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(AddHomeworkStates.waiting_for_subject, F.data.startswith("hwa_sub:"))
async def process_subject_callback(callback: CallbackQuery, state: FSMContext):
    subject = callback.data.split(":")[1]
    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)
    await callback.message.edit_text(
        f"📝 Предмет: **{subject}**\n\nВведите текст домашнего задания:",
        reply_markup=get_cancel_keyboard(callback_data="hw_list_active"),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(AddHomeworkStates.waiting_for_subject)
async def process_subject_text(message: Message, state: FSMContext):
    subject = message.text.strip()
    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)
    await message.answer(
        f"📝 Предмет: **{subject}**\n\nВведите текст домашнего задания:",
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
        text, kb = await format_homework_list(message.chat.id, is_archive=False)
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        return
        
    await state.update_data(hw_description=description)
    data = await state.get_data()
    subject = data["hw_subject"]
    
    # Calculate dates helper
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    day_after = today + datetime.timedelta(days=2)
    
    # Try to find next lesson date
    schedule = await get_schedule(message.chat.id)
    subject_days = [s.day_of_week for s in schedule if s.subject_name.lower() == subject.lower()]
    
    next_lesson_date = None
    if subject_days:
        current_weekday = today.weekday()
        # Find minimum days to next occurrence
        diffs = [(day - current_weekday) % 7 for day in subject_days]
        # Filter out 0 (which is today) unless they specifically want today? 
        # Usually homework is for the next session, so if today is Math, homework is for next week's Math
        diffs = [d if d != 0 else 7 for d in diffs]
        min_diff = min(diffs)
        next_lesson_date = today + datetime.timedelta(days=min_diff)
        
    # Generate date buttons
    buttons = [
        [
            InlineKeyboardButton(text=f"Завтра ({tomorrow.strftime('%d.%m')})", callback_data=f"hwa_date:{tomorrow.isoformat()}"),
            InlineKeyboardButton(text=f"Послезавтра ({day_after.strftime('%d.%m')})", callback_data=f"hwa_date:{day_after.isoformat()}")
        ]
    ]
    
    if next_lesson_date and next_lesson_date not in [tomorrow, day_after]:
        day_name = DAYS_RU[next_lesson_date.weekday()]
        buttons.append([
            InlineKeyboardButton(text=f"След. урок ({day_name} {next_lesson_date.strftime('%d.%m')})", callback_data=f"hwa_date:{next_lesson_date.isoformat()}")
        ])
        
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")])
    
    await state.set_state(AddHomeworkStates.waiting_for_due_date)
    
    msg_text = (
        f"📝 Предмет: **{subject}**\n"
        f"📋 Задание: _{description}_\n\n"
        f"Выбери дату сдачи из вариантов ниже или введи её вручную в формате `ДД.ММ` (например, `14.10`):"
    )
    
    await message.answer(
        msg_text,
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
    
    await callback.message.delete()
    await callback.message.answer(
        f"✅ Домашнее задание по предмету **{subject}** на {due_date.strftime('%d.%m')} сохранено!",
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
        text, kb = await format_homework_list(message.chat.id, is_archive=False)
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
        return
        
    # Parse DD.MM
    try:
        day, month = map(int, text.split("."))
        current_year = datetime.datetime.now(tz).year
        due_date = datetime.date(current_year, month, day)
        
        # If due_date is in the past (e.g. today is Dec 31, and user writes 01.01, it should be next year)
        today = datetime.datetime.now(tz).date()
        if due_date < today and due_date.month < today.month:
            due_date = datetime.date(current_year + 1, month, day)
    except Exception:
        await message.answer(
            "Неверный формат даты! Пожалуйста, укажи дату в формате `ДД.ММ` (например, `14.10`):",
            parse_mode="Markdown"
        )
        return
        
    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]
    
    await add_homework(message.chat.id, subject, due_date, description)
    await state.clear()
    
    await message.answer(
        f"✅ Домашнее задание по предмету **{subject}** на {due_date.strftime('%d.%m')} сохранено!",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    
    text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")
