import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from database.db import get_or_create_chat, set_onboarded, save_lesson_slots, save_schedule_day
from keyboards.reply import get_main_menu
from keyboards.inline import DAYS_RU

router = Router()

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
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]
        ],
        resize_keyboard=True
    )

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

@router.message(OnboardingStates.waiting_for_lessons_count, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_lesson_times, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_schedule_subjects, F.text == "❌ Сбросить настройку")
@router.message(OnboardingStates.waiting_for_saturday_decision, F.text == "❌ Сбросить настройку")
async def cancel_onboarding(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Настройка отменена. Бот не настроен. Отправь /start, чтобы начать заново.",
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
        "Давай настроим время для каждого урока.\n"
        "Введи время для **Урока №1** в формате `ЧЧ:ММ - ЧЧ:ММ` (например, `08:30 - 09:15`):",
        reply_markup=get_cancel_markup(),
        parse_mode="Markdown"
    )

TIME_PATTERN = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]\s*-\s*([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")

@router.message(OnboardingStates.waiting_for_lesson_times)
async def process_lesson_times(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_PATTERN.match(text):
        await message.answer(
            "Неверный формат времени! Пожалуйста, напиши в формате `ЧЧ:ММ - ЧЧ:ММ`.\n"
            "Пример: `08:30 - 09:15`",
            parse_mode="Markdown"
        )
        return
    
    # Standardize spaces around hyphen
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
        # Finished saving slots. Save to DB.
        await save_lesson_slots(message.chat.id, lesson_slots)
        
        # Start day schedule setup
        # Days to configure initially: Monday to Friday (indices 0 to 4)
        await state.update_data(
            target_days=[0, 1, 2, 3, 4],
            current_day_list_idx=0,
            current_lesson_idx=1,
            current_day_lessons=[],
            all_schedule_data={}  # Key: day_idx, Value: list of (num, subject)
        )
        await state.set_state(OnboardingStates.waiting_for_schedule_subjects)
        
        day_name = DAYS_RU[0]  # Понедельник
        slot_time = f"{lesson_slots[0][1]} - {lesson_slots[0][2]}"
        await message.answer(
            "3️⃣ **Шаг 3 из 3: Расписание предметов**\n\n"
            "Давай заполним предметы на учебные дни.\n"
            f"📅 **{day_name}**\n"
            f"Урок №1 ({slot_time}): Какая будет дисциплина?\n"
            "Напиши название (например, `Математика`) или напиши `пропустить` (или `skip`):",
            reply_markup=get_cancel_markup(),
            parse_mode="Markdown"
        )

@router.message(OnboardingStates.waiting_for_schedule_subjects)
async def process_schedule_subjects(message: Message, state: FSMContext):
    subject = message.text.strip()
    data = await state.get_data()
    
    target_days = data["target_days"]
    current_day_list_idx = data["current_day_list_idx"]
    day_idx = target_days[current_day_list_idx]
    
    current_lesson_idx = data["current_lesson_idx"]
    lessons_count = data["lessons_count"]
    lesson_slots = data["lesson_slots"]
    current_day_lessons = data["current_day_lessons"]
    all_schedule_data = data["all_schedule_data"]
    
    # Save the subject if not skipped
    normalized_sub = "skip" if subject.lower() in ["пропустить", "skip", "-"] else subject
    current_day_lessons.append((current_lesson_idx, normalized_sub))
    await state.update_data(current_day_lessons=current_day_lessons)
    
    if current_lesson_idx < lessons_count:
        # Next lesson in current day
        current_lesson_idx += 1
        await state.update_data(current_lesson_idx=current_lesson_idx)
        
        slot = lesson_slots[current_lesson_idx - 1]
        slot_time = f"{slot[1]} - {slot[2]}"
        day_name = DAYS_RU[day_idx]
        await message.answer(
            f"📅 **{day_name}**\n"
            f"Урок №{current_lesson_idx} ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши `пропустить`:",
            parse_mode="Markdown"
        )
    else:
        # Current day finished. Save to all_schedule_data.
        all_schedule_data[day_idx] = current_day_lessons
        await state.update_data(all_schedule_data=all_schedule_data)
        
        # Move to next day
        if current_day_list_idx < len(target_days) - 1:
            current_day_list_idx += 1
            await state.update_data(
                current_day_list_idx=current_day_list_idx,
                current_lesson_idx=1,
                current_day_lessons=[]
            )
            
            next_day_idx = target_days[current_day_list_idx]
            day_name = DAYS_RU[next_day_idx]
            slot = lesson_slots[0]
            slot_time = f"{slot[1]} - {slot[2]}"
            await message.answer(
                f"📅 **{day_name}**\n"
                f"Урок №1 ({slot_time}): Какой предмет?\n"
                "Напиши название или напиши `пропустить`:",
                parse_mode="Markdown"
            )
        else:
            # We finished Monday - Friday. Ask if they want to configure Saturday (index 5)
            await state.set_state(OnboardingStates.waiting_for_saturday_decision)
            await message.answer(
                "📅 Хочешь настроить расписание на **Субботу**?",
                reply_markup=get_yes_no_markup(),
                parse_mode="Markdown"
            )

@router.message(OnboardingStates.waiting_for_saturday_decision)
async def process_saturday_decision(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    data = await state.get_data()
    lesson_slots = data["lesson_slots"]
    all_schedule_data = data["all_schedule_data"]
    
    if "да" in text or "yes" in text:
        # Setup Saturday (day_idx = 5)
        await state.update_data(
            target_days=data["target_days"] + [5],
            current_day_list_idx=len(data["target_days"]),  # last element
            current_lesson_idx=1,
            current_day_lessons=[]
        )
        await state.set_state(OnboardingStates.waiting_for_schedule_subjects)
        
        slot = lesson_slots[0]
        slot_time = f"{slot[1]} - {slot[2]}"
        await message.answer(
            "📅 **Суббота**\n"
            f"Урок №1 ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши `пропустить`:",
            reply_markup=get_cancel_markup(),
            parse_mode="Markdown"
        )
    elif "нет" in text or "no" in text:
        # Finish onboarding!
        # Save all schedules to DB
        chat_id = message.chat.id
        for day_idx, lessons in all_schedule_data.items():
            await save_schedule_day(chat_id, day_idx, lessons)
            
        await set_onboarded(chat_id, True)
        await state.clear()
        
        await message.answer(
            "🎉 **Настройка завершена!**\n\n"
            "Все данные успешно сохранены. Теперь ты можешь полноценно пользоваться ботом!\n"
            "Вот твоё главное меню ниже 👇",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
    else:
        await message.answer("Пожалуйста, ответь Да или Нет.")
