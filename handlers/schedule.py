import datetime
import pytz
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_schedule, get_lesson_slots, update_schedule_slot, save_lesson_slots
from keyboards.inline import get_schedule_days_keyboard, DAYS_RU, DAYS_SHORT_RU, get_cancel_keyboard
from keyboards.reply import get_main_menu
from config import TIMEZONE

router = Router()
tz = pytz.timezone(TIMEZONE)

class EditScheduleStates(StatesGroup):
    waiting_for_subject_name = State()
    waiting_for_lessons_count = State()
    waiting_for_lesson_times = State()

async def format_schedule_message(chat_id: int, day_idx: int) -> str:
    schedule_items = await get_schedule(chat_id, day_idx)
    slots = await get_lesson_slots(chat_id)
    
    day_name = DAYS_RU[day_idx]
    message_text = f"📅 **Расписание на {day_name}**\n\n"
    
    if not slots:
        return "⚠️ Время уроков еще не настроено. Напиши /start для настройки."
        
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
            # Choose a nice emoji based on keywords if we want, or just a book
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
        
        sub_text = f"**{subject}**" if subject else "_[Свободно]_"
        message_text += f"{num}️⃣ `{start} - {end}` | {emoji} {sub_text}\n"
        
    if not has_any:
        message_text += "\n🥱 В этот день нет уроков!"
        
    return message_text

@router.message(F.text == "📅 Расписание")
async def show_schedule(message: Message):
    # Determine current day of week (0=Mon, 6=Sun) based on configured timezone
    now = datetime.datetime.now(tz)
    current_day = now.weekday()
    # Default to Monday if Sunday (since typically there are no Sunday classes)
    if current_day == 6:
        current_day = 0
        
    text = await format_schedule_message(message.chat.id, current_day)
    kb = get_schedule_days_keyboard(current_day)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("sch_day:"))
async def process_day_select(callback: CallbackQuery):
    day_idx = int(callback.data.split(":")[1])
    text = await format_schedule_message(callback.message.chat.id, day_idx)
    kb = get_schedule_days_keyboard(day_idx)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("sch_edit:"))
async def edit_schedule_day(callback: CallbackQuery):
    day_idx = int(callback.data.split(":")[1])
    day_name = DAYS_RU[day_idx]
    chat_id = callback.message.chat.id
    
    schedule_items = await get_schedule(chat_id, day_idx)
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
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"se_slot:{day_idx}:{num}")])
        
    buttons.append([InlineKeyboardButton(text="🔙 Назад к расписанию", callback_data=f"sch_day:{day_idx}")])
    
    await callback.message.edit_text(
        f"✏️ **Редактирование: {day_name}**\nВыберите урок, который хотите изменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("se_slot:"))
async def initiate_slot_edit(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    day_idx = int(parts[1])
    lesson_num = int(parts[2])
    day_name = DAYS_RU[day_idx]
    
    await state.update_data(edit_day_idx=day_idx, edit_lesson_num=lesson_num)
    await state.set_state(EditScheduleStates.waiting_for_subject_name)
    
    await callback.message.edit_text(
        f"✏️ **Изменение урока**\n\n"
        f"Вы выбрали **Урок №{lesson_num}** ({day_name}).\n"
        f"Введите новое название предмета или напишите `skip`/`пропустить`, чтобы сделать его свободным:",
        reply_markup=get_cancel_keyboard(callback_data=f"sch_day:{day_idx}"),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(EditScheduleStates.waiting_for_subject_name)
async def process_new_subject_name(message: Message, state: FSMContext):
    subject = message.text.strip()
    data = await state.get_data()
    day_idx = data["edit_day_idx"]
    lesson_num = data["edit_lesson_num"]
    
    normalized_sub = "" if subject.lower() in ["skip", "пропустить", "-", "нет"] else subject
    
    await update_schedule_slot(message.chat.id, day_idx, lesson_num, normalized_sub)
    await state.clear()
    
    text = await format_schedule_message(message.chat.id, day_idx)
    kb = get_schedule_days_keyboard(day_idx)
    
    await message.answer(
        f"✅ Предмет для урока №{lesson_num} обновлен!",
        reply_markup=get_main_menu()
    )
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

# Edit Lesson Times
@router.callback_query(F.data == "sch_edit_times")
async def initiate_edit_times(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditScheduleStates.waiting_for_lessons_count)
    await callback.message.edit_text(
        "🕒 **Настройка звонков**\n\n"
        "Сколько максимум уроков в день у тебя бывает?\n"
        "Отправь мне число от 1 до 10 (или нажми Отмена):",
        reply_markup=get_cancel_keyboard(callback_data="sch_day:0"),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(EditScheduleStates.waiting_for_lessons_count)
async def process_edit_lessons_count(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await message.answer("Пожалуйста, введи число от 1 до 10.")
        return
        
    lessons_count = int(text)
    await state.update_data(lessons_count=lessons_count, current_lesson_idx=1, lesson_slots=[])
    await state.set_state(EditScheduleStates.waiting_for_lesson_times)
    
    await message.answer(
        f"Введи время для **Урока №1** в формате `ЧЧ:ММ - ЧЧ:ММ` (например, `08:30 - 09:15`):",
        parse_mode="Markdown"
    )

import re
TIME_PATTERN = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]\s*-\s*([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")

@router.message(EditScheduleStates.waiting_for_lesson_times)
async def process_edit_lesson_times(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_PATTERN.match(text):
        await message.answer(
            "Неверный формат времени! Пожалуйста, напиши в формате `ЧЧ:ММ - ЧЧ:ММ`.\n"
            "Пример: `08:30 - 09:15`",
            parse_mode="Markdown"
        )
        return
        
    parts = text.split("-")
    start = parts[0].strip()
    end = parts[1].strip()
    
    data = await state.get_data()
    lesson_slots = data.get("lesson_slots", [])
    current_lesson_idx = data.get("current_lesson_idx", 1)
    lessons_count = data.get("lessons_count")
    
    lesson_slots.append((current_lesson_idx, start, end))
    await state.update_data(lesson_slots=lesson_slots)
    
    if current_lesson_idx < lessons_count:
        current_lesson_idx += 1
        await state.update_data(current_lesson_idx=current_lesson_idx)
        await message.answer(
            f"Введи время для **Урока №{current_lesson_idx}** (например, `09:25 - 10:10`):",
            parse_mode="Markdown"
        )
    else:
        # Save to DB
        await save_lesson_slots(message.chat.id, lesson_slots)
        await state.clear()
        
        await message.answer(
            "✅ Время звонков успешно обновлено!",
            reply_markup=get_main_menu()
        )
        
        # Display schedule for Monday
        text = await format_schedule_message(message.chat.id, 0)
        kb = get_schedule_days_keyboard(0)
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
