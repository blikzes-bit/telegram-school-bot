import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from database.db import get_or_create_chat, set_onboarded, save_lesson_slots, save_schedule_day
from keyboards.reply import get_main_menu
from keyboards.inline import DAYS_RU

router = Router()

# Standard school lesson time presets (can be adjusted)
STANDARD_TIMES = [
    ("08:00 - 08:45", "08:45 - 09:30"),
    ("08:30 - 09:15", "09:25 - 10:10"),
    ("09:00 - 09:45", "09:55 - 10:40"),
]

class OnboardingStates(StatesGroup):
    waiting_for_lessons_count = State()
    waiting_for_lesson_times = State()
    waiting_for_schedule_subjects = State()
    waiting_for_saturday_decision = State()

def get_cancel_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Сбросить настройку")]],
        resize_keyboard=True
    )

def get_yes_no_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
        resize_keyboard=True
    )

def get_time_preset_keyboard(lesson_num: int) -> InlineKeyboardMarkup:
    """Returns quick-pick time buttons for a lesson slot."""
    buttons = []
    for start, end in STANDARD_TIMES:
        btn_text = f"⏰ {start}" if lesson_num == 1 else f"⏰ {end}"
        # Show start for first lesson, then shift through ends for subsequent
        display = f"{start}" if lesson_num == 1 else f"{end}"
        buttons.append([InlineKeyboardButton(
            text=f"⏰ {display}",
            callback_data=f"ob_time:{lesson_num}:{display} - end_placeholder"
        )])
    # Actually let's show the full pair as a full suggestion
    buttons = []
    if lesson_num == 1:
        for start, _ in STANDARD_TIMES:
            # Format: HH:MM - HH:MM
            # Calculate end: start + 45 min
            h, m = map(int, start.split(":"))
            total = h * 60 + m + 45
            end = f"{total // 60:02d}:{total % 60:02d}"
            slot_str = f"{start} - {end}"
            buttons.append([InlineKeyboardButton(text=f"⏰ {slot_str}", callback_data=f"ob_time:{slot_str}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

TIME_PATTERN = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]\s*-\s*([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")

def build_time_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick buttons for lesson times."""
    presets = [
        "08:00 - 08:45",
        "08:30 - 09:15",
        "09:00 - 09:45",
        "09:25 - 10:10",
        "09:55 - 10:40",
        "10:20 - 11:05",
        "11:15 - 12:00",
        "12:10 - 12:55",
        "13:05 - 13:50",
        "14:00 - 14:45",
    ]
    buttons = []
    row = []
    for p in presets:
        row.append(InlineKeyboardButton(text=p, callback_data=f"ob_time:{p}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def ask_for_lesson_time(message_or_callback, lesson_num: int, parse_mode="Markdown"):
    text = (
        f"Введи время для **Урока №{lesson_num}** в формате `ЧЧ:ММ - ЧЧ:ММ`\n"
        f"или выбери готовый вариант:"
    )
    kb = build_time_keyboard()
    if hasattr(message_or_callback, "edit_text"):
        await message_or_callback.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    else:
        await message_or_callback.answer(text, reply_markup=kb, parse_mode=parse_mode)

@router.callback_query(F.data == "ob_start")
async def start_onboarding_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OnboardingStates.waiting_for_lessons_count)
    await callback.message.answer(
        "1️⃣ **Шаг 1 из 3: Количество уроков**\n\n"
        "Сколько максимум уроков в день у тебя бывает?\n"
        "Отправь мне число от 1 до 10 (например, `6`):",
        reply_markup=get_cancel_markup(),
        parse_mode="Markdown"
    )

# Cancel any onboarding state
@router.message(OnboardingStates.waiting_for_lessons_count, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_lesson_times, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_schedule_subjects, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_saturday_decision, F.text == "❌ Сбросить настройку")
async def cancel_onboarding(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Настройка отменена. Отправь /start чтобы начать заново.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🚀 Начать настройку")]],
            resize_keyboard=True
        )
    )

@router.message(OnboardingStates.waiting_for_lessons_count)
async def process_lessons_count(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await message.answer("Пожалуйста, введи число от 1 до 10.")
        return

    lessons_count = int(text)
    await state.update_data(lessons_count=lessons_count, current_lesson_idx=1, lesson_slots=[])
    await state.set_state(OnboardingStates.waiting_for_lesson_times)

    await message.answer(
        "2️⃣ **Шаг 2 из 3: Время звонков**\n\n"
        "Давай настроим время для каждого урока.",
        reply_markup=get_cancel_markup(),
        parse_mode="Markdown"
    )
    # Ask for lesson 1 with quick-pick keyboard
    await message.answer(
        f"Введи время для **Урока №1** в формате `ЧЧ:ММ - ЧЧ:ММ`\nили выбери готовый вариант:",
        reply_markup=build_time_keyboard(),
        parse_mode="Markdown"
    )

async def _save_time_slot(state: FSMContext, chat_id: int, slot_str: str) -> bool:
    """
    Parses 'HH:MM - HH:MM', saves to state, returns True when all slots are done.
    Returns False if more slots remain, None on format error.
    """
    if not TIME_PATTERN.match(slot_str.strip()):
        return None  # signal error
    parts = slot_str.split("-", 1)
    start = parts[0].strip()
    end = parts[1].strip()

    data = await state.get_data()
    lesson_slots = data.get("lesson_slots", [])
    current_lesson_idx = data.get("current_lesson_idx", 1)
    lessons_count = data.get("lessons_count")

    lesson_slots.append((current_lesson_idx, start, end))
    await state.update_data(lesson_slots=lesson_slots)

    if current_lesson_idx < lessons_count:
        await state.update_data(current_lesson_idx=current_lesson_idx + 1)
        return False  # more slots remain
    else:
        await save_lesson_slots(chat_id, lesson_slots)
        return True   # done

async def _start_schedule_setup(message: Message, state: FSMContext, lesson_slots):
    """Start day-by-day schedule collection after lesson times are set."""
    await state.update_data(
        target_days=[0, 1, 2, 3, 4],
        current_day_list_idx=0,
        current_lesson_idx=1,
        current_day_lessons=[],
        all_schedule_data={}
    )
    await state.set_state(OnboardingStates.waiting_for_schedule_subjects)

    day_name = DAYS_RU[0]
    slot_time = f"{lesson_slots[0][1]} - {lesson_slots[0][2]}"
    await message.answer(
        "3️⃣ **Шаг 3 из 3: Расписание предметов**\n\n"
        f"📅 **{day_name}**\n"
        f"Урок №1 ({slot_time}): Какой предмет?\n"
        "Напиши название (например, `Математика`) или напиши `пропустить` / `skip`:",
        reply_markup=get_cancel_markup(),
        parse_mode="Markdown"
    )

# Quick-pick time button handler
@router.callback_query(OnboardingStates.waiting_for_lesson_times, F.data.startswith("ob_time:"))
async def process_time_preset(callback: CallbackQuery, state: FSMContext):
    slot_str = callback.data[len("ob_time:"):]
    done = await _save_time_slot(state, callback.message.chat.id, slot_str)

    if done is None:
        await callback.answer("Ошибка формата времени!", show_alert=True)
        return

    await callback.answer()

    if not done:
        data = await state.get_data()
        next_idx = data["current_lesson_idx"]
        await callback.message.edit_text(
            f"Введи время для **Урока №{next_idx}** в формате `ЧЧ:ММ - ЧЧ:ММ`\nили выбери готовый вариант:",
            reply_markup=build_time_keyboard(),
            parse_mode="Markdown"
        )
    else:
        data = await state.get_data()
        await callback.message.delete()
        await _start_schedule_setup(callback.message, state, data["lesson_slots"])

# Text time entry handler
@router.message(OnboardingStates.waiting_for_lesson_times)
async def process_lesson_times_text(message: Message, state: FSMContext):
    done = await _save_time_slot(state, message.chat.id, message.text.strip())

    if done is None:
        await message.answer(
            "Неверный формат! Укажи время как `ЧЧ:ММ - ЧЧ:ММ` (например, `08:30 - 09:15`):",
            parse_mode="Markdown"
        )
        return

    if not done:
        data = await state.get_data()
        next_idx = data["current_lesson_idx"]
        await message.answer(
            f"Введи время для **Урока №{next_idx}** в формате `ЧЧ:ММ - ЧЧ:ММ`\nили выбери готовый вариант:",
            reply_markup=build_time_keyboard(),
            parse_mode="Markdown"
        )
    else:
        data = await state.get_data()
        await _start_schedule_setup(message, state, data["lesson_slots"])

def _get_subject_keyboard(day_idx: int, lesson_num: int, prev_day_lessons=None) -> ReplyKeyboardMarkup:
    """Build reply keyboard for subject entry with Back/Skip/Copy options."""
    buttons = []
    buttons.append([KeyboardButton(text="⏭️ Пропустить")])
    if prev_day_lessons:
        buttons.append([KeyboardButton(text="📋 Скопировать вчерашний день")])
    if lesson_num > 1 or day_idx > 0:
        buttons.append([KeyboardButton(text="⬅️ Назад")])
    buttons.append([KeyboardButton(text="❌ Сбросить настройку")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

@router.message(OnboardingStates.waiting_for_schedule_subjects)
async def process_schedule_subjects(message: Message, state: FSMContext):
    subject_input = message.text.strip()
    data = await state.get_data()

    target_days = data["target_days"]
    current_day_list_idx = data["current_day_list_idx"]
    day_idx = target_days[current_day_list_idx]
    current_lesson_idx = data["current_lesson_idx"]
    lessons_count = data["lessons_count"]
    lesson_slots = data["lesson_slots"]
    current_day_lessons = data.get("current_day_lessons", [])
    all_schedule_data = data.get("all_schedule_data", {})

    # --- Handle special actions ---
    if subject_input == "⬅️ Назад":
        if current_lesson_idx > 1:
            # Go back one lesson in current day
            current_day_lessons = [l for l in current_day_lessons if l[0] != current_lesson_idx - 1]
            current_lesson_idx -= 1
            await state.update_data(current_lesson_idx=current_lesson_idx, current_day_lessons=current_day_lessons)
            slot = lesson_slots[current_lesson_idx - 1]
            slot_time = f"{slot[1]} - {slot[2]}"
            prev_day = all_schedule_data.get(target_days[current_day_list_idx - 1]) if current_day_list_idx > 0 else None
            await message.answer(
                f"📅 **{DAYS_RU[day_idx]}**\n"
                f"Урок №{current_lesson_idx} ({slot_time}): Какой предмет?",
                reply_markup=_get_subject_keyboard(day_idx, current_lesson_idx, prev_day),
                parse_mode="Markdown"
            )
        elif current_day_list_idx > 0:
            # Go back to previous day's last lesson
            current_day_list_idx -= 1
            prev_day_idx = target_days[current_day_list_idx]
            all_schedule_data.pop(day_idx, None)
            prev_lessons = list(all_schedule_data.get(prev_day_idx, []))
            # Remove last lesson of previous day
            if prev_lessons:
                prev_lessons = prev_lessons[:-1]
            all_schedule_data[prev_day_idx] = prev_lessons
            new_lesson_idx = len(prev_lessons) + 1
            await state.update_data(
                current_day_list_idx=current_day_list_idx,
                current_lesson_idx=new_lesson_idx,
                current_day_lessons=prev_lessons,
                all_schedule_data=all_schedule_data
            )
            slot = lesson_slots[new_lesson_idx - 1]
            slot_time = f"{slot[1]} - {slot[2]}"
            prev_prev_day = all_schedule_data.get(target_days[current_day_list_idx - 1]) if current_day_list_idx > 0 else None
            await message.answer(
                f"📅 **{DAYS_RU[prev_day_idx]}**\n"
                f"Урок №{new_lesson_idx} ({slot_time}): Какой предмет?\n"
                f"(Повторный ввод)",
                reply_markup=_get_subject_keyboard(prev_day_idx, new_lesson_idx, prev_prev_day),
                parse_mode="Markdown"
            )
        else:
            await message.answer("Вернуться дальше не получится — это первый урок!")
        return

    if subject_input == "📋 Скопировать вчерашний день":
        if current_day_list_idx == 0:
            await message.answer("Нет предыдущего дня для копирования!")
            return
        prev_day_idx = target_days[current_day_list_idx - 1]
        prev_lessons = all_schedule_data.get(prev_day_idx, [])
        if not prev_lessons:
            await message.answer("В прошлом дне нет уроков для копирования!")
            return
        # Save current day with copy of previous
        all_schedule_data[day_idx] = list(prev_lessons)
        await state.update_data(all_schedule_data=all_schedule_data)
        # Move to next day or saturday question
        await _advance_to_next_day(message, state, target_days, current_day_list_idx, day_idx,
                                   all_schedule_data, lesson_slots, lessons_count)
        return

    if subject_input == "⏭️ Пропустить":
        normalized_sub = "skip"
    else:
        normalized_sub = subject_input if subject_input.lower() not in ["пропустить", "skip", "-"] else "skip"

    current_day_lessons.append((current_lesson_idx, normalized_sub))
    await state.update_data(current_day_lessons=current_day_lessons)

    if current_lesson_idx < lessons_count:
        # Next lesson in current day
        next_idx = current_lesson_idx + 1
        await state.update_data(current_lesson_idx=next_idx)
        slot = lesson_slots[next_idx - 1]
        slot_time = f"{slot[1]} - {slot[2]}"
        prev_day = all_schedule_data.get(target_days[current_day_list_idx - 1]) if current_day_list_idx > 0 else None
        await message.answer(
            f"📅 **{DAYS_RU[day_idx]}**\n"
            f"Урок №{next_idx} ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши `пропустить`:",
            reply_markup=_get_subject_keyboard(day_idx, next_idx, prev_day),
            parse_mode="Markdown"
        )
    else:
        # Day done
        all_schedule_data[day_idx] = current_day_lessons
        await state.update_data(all_schedule_data=all_schedule_data)
        await _advance_to_next_day(message, state, target_days, current_day_list_idx, day_idx,
                                   all_schedule_data, lesson_slots, lessons_count)

async def _advance_to_next_day(message, state, target_days, current_day_list_idx, day_idx,
                                all_schedule_data, lesson_slots, lessons_count):
    """Move to the next day in onboarding, or finish."""
    data = await state.get_data()

    if current_day_list_idx < len(target_days) - 1:
        # There are more days to fill
        next_day_list_idx = current_day_list_idx + 1
        next_day_idx = target_days[next_day_list_idx]
        await state.update_data(
            current_day_list_idx=next_day_list_idx,
            current_lesson_idx=1,
            current_day_lessons=[]
        )
        slot = lesson_slots[0]
        slot_time = f"{slot[1]} - {slot[2]}"
        # Previous day's lessons for copy button
        prev_lessons = all_schedule_data.get(day_idx)
        await message.answer(
            f"📅 **{DAYS_RU[next_day_idx]}**\n"
            f"Урок №1 ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши `пропустить`:",
            reply_markup=_get_subject_keyboard(next_day_idx, 1, prev_lessons),
            parse_mode="Markdown"
        )
    else:
        # We finished all current target_days
        if day_idx == 4:
            # Just finished Friday, ask about Saturday
            await state.set_state(OnboardingStates.waiting_for_saturday_decision)
            await message.answer(
                "📅 Хочешь настроить расписание на **Субботу**?",
                reply_markup=get_yes_no_markup(),
                parse_mode="Markdown"
            )
        else:
            # Finished Saturday — finalize onboarding
            await _finalize_onboarding(message, state, all_schedule_data)

async def _finalize_onboarding(message, state, all_schedule_data):
    chat_id = message.chat.id
    for d_idx, lessons in all_schedule_data.items():
        await save_schedule_day(chat_id, d_idx, lessons)
    await set_onboarded(chat_id, True)
    await state.clear()
    await message.answer(
        "🎉 **Настройка завершена!**\n\n"
        "Все данные успешно сохранены. Теперь ты можешь полноценно пользоваться ботом!\n"
        "Вот твоё главное меню ниже 👇",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

@router.message(OnboardingStates.waiting_for_saturday_decision)
async def process_saturday_decision(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    data = await state.get_data()
    lesson_slots = data["lesson_slots"]
    all_schedule_data = data["all_schedule_data"]

    if "да" in text or "yes" in text:
        # Add Saturday (day_idx = 5) to target_days
        new_target_days = data["target_days"] + [5]
        await state.update_data(
            target_days=new_target_days,
            current_day_list_idx=len(new_target_days) - 1,
            current_lesson_idx=1,
            current_day_lessons=[]
        )
        await state.set_state(OnboardingStates.waiting_for_schedule_subjects)
        slot = lesson_slots[0]
        slot_time = f"{slot[1]} - {slot[2]}"
        prev_lessons = all_schedule_data.get(4)  # Friday's lessons
        await message.answer(
            "📅 **Суббота**\n"
            f"Урок №1 ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши `пропустить`:",
            reply_markup=_get_subject_keyboard(5, 1, prev_lessons),
            parse_mode="Markdown"
        )
    elif "нет" in text or "no" in text:
        await _finalize_onboarding(message, state, all_schedule_data)
    else:
        await message.answer("Пожалуйста, ответь Да или Нет.")
