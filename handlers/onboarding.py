from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from database.db import get_or_create_chat, finalize_onboarding, set_chat_owner
from keyboards.reply import main_menu_for
from keyboards.inline import DAYS_RU
from middleware.access import require_admin
from services import profiles
from utils import parse_time_interval, validate_against_previous, MAX_SUBJECT_LEN, html_escape

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или используй кнопки ниже)."

# Standard school lesson time presets (can be adjusted)
STANDARD_TIMES = [
    ("08:00 - 08:45", "08:45 - 09:30"),
    ("08:30 - 09:15", "09:25 - 10:10"),
    ("09:00 - 09:45", "09:55 - 10:40"),
]

YES_ANSWERS = {"да", "yes"}
NO_ANSWERS = {"нет", "no"}


class OnboardingStates(StatesGroup):
    waiting_for_profile = State()
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


async def _try_delete(message: Message) -> None:
    """Best-effort message deletion — never lets a delete failure (missing
    rights, too old, already gone) break the onboarding flow."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


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


@router.callback_query(F.data == "ob_start")
async def start_onboarding_callback(callback: CallbackQuery, state: FSMContext):
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)

    if not await require_admin(callback, callback.bot):
        return

    if chat.is_onboarded:
        await callback.answer()
        await callback.message.answer(
            "⚠️ Этот чат уже настроен.\n\n"
            "Если продолжить, <b>всё текущее расписание и время звонков будут заменены</b>. "
            "Домашние задания при этом не удаляются.\n\n"
            "Переконфигурировать?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, переконфигурировать", callback_data="ob_reconfigure_confirm"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="ob_reconfigure_cancel"),
                ]
            ]),
            parse_mode="HTML",
        )
        return

    await callback.answer()
    await _begin_onboarding(callback.message, state)


@router.callback_query(F.data == "ob_reconfigure_confirm")
async def reconfigure_confirm(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await callback.answer()
    await _begin_onboarding(callback.message, state)


@router.callback_query(F.data == "ob_reconfigure_cancel")
async def reconfigure_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отменено.")
    await callback.message.answer("Ок, ничего не меняем.", reply_markup=await main_menu_for(callback.message.chat.id, callback.message.chat.type))


def build_profile_keyboard() -> InlineKeyboardMarkup:
    """One button per profile, each with its plain-words explanation below."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=profiles.PROFILE_LABELS[name], callback_data=f"ob_profile:{name}"
        )]
        for name in profiles.PROFILES
    ])


async def _begin_onboarding(message: Message, state: FSMContext):
    """Step 1 is always "what is this chat for" — everything after it depends on
    the answer (a tutor chat has no school timetable to set up at all)."""
    await state.clear()
    await state.set_state(OnboardingStates.waiting_for_profile)
    lines = ["1️⃣ <b>Шаг 1: Как будешь пользоваться?</b>", ""]
    for name in profiles.PROFILES:
        lines.append(
            f"{profiles.PROFILE_LABELS[name]} — {profiles.PROFILE_DESCRIPTIONS[name]}"
        )
    lines += ["", "Выбери вариант кнопкой ниже. Потом это можно поменять в ⚙️ Настройках."]
    await message.answer(
        "\n".join(lines),
        reply_markup=build_profile_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(OnboardingStates.waiting_for_profile, F.data.startswith("ob_profile:"))
async def process_profile_choice(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    profile = profiles.normalize(callback.data.split(":", 1)[1])
    if profile is None:
        await callback.answer("Не понял вариант, попробуй ещё раз.", show_alert=True)
        return

    await callback.answer()
    # Remember who is setting this chat up: they become its owner, and the owner
    # can never be locked out of their own chat by a rights change later.
    await state.update_data(profile=profile, onboarding_actor_id=callback.from_user.id)
    label = profiles.PROFILE_LABELS[profile]

    if not profiles.needs_schedule_onboarding(profile):
        # A tutor chat has no bell times and no weekly school timetable: asking
        # for them would be pure friction. Finish here and let the tutor add the
        # actual sessions as extra activities.
        await _finalize_onboarding(callback.message, state, {}, lesson_slots=[])
        return

    await state.set_state(OnboardingStates.waiting_for_lessons_count)
    await callback.message.answer(
        f"Выбрано: <b>{label}</b>\n\n"
        "2️⃣ <b>Шаг 2: Количество уроков</b>\n\n"
        "Сколько максимум уроков в день бывает?\n"
        "Отправь число от 1 до 10 (например, <code>6</code>):",
        reply_markup=get_cancel_markup(),
        parse_mode="HTML",
    )


# Cancel any onboarding state
@router.message(OnboardingStates.waiting_for_profile, F.text == "❌ Сбросить настройку")
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


@router.message(OnboardingStates.waiting_for_profile, F.text)
async def profile_choice_needs_a_button(message: Message, state: FSMContext):
    """Typing at step 1 is not an error worth scolding — just point at the buttons."""
    await message.answer(
        "Выбери один из вариантов кнопкой выше 👆",
        reply_markup=build_profile_keyboard(),
    )


@router.message(OnboardingStates.waiting_for_lessons_count, F.text)
async def process_lessons_count(message: Message, state: FSMContext):
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
    await state.set_state(OnboardingStates.waiting_for_lesson_times)

    await message.answer(
        "3️⃣ <b>Шаг 3: Время звонков</b>\n\n"
        "Давай настроим время для каждого урока.",
        reply_markup=get_cancel_markup(),
        parse_mode="HTML",
    )
    # Ask for lesson 1 with quick-pick keyboard
    await message.answer(
        "Введи время для <b>Урока №1</b> в формате <code>ЧЧ:ММ - ЧЧ:ММ</code>\nили выбери готовый вариант:",
        reply_markup=build_time_keyboard(),
        parse_mode="HTML",
    )


async def _save_time_slot(state: FSMContext, slot_str: str):
    """
    Parses 'HH:MM - HH:MM', validates it and appends to FSM state (never
    written to the real lesson_slots table here — everything is persisted in
    one transaction at the very end of onboarding, see _finalize_onboarding).

    Returns a ``(done, error)`` tuple:
      * ``error`` is a user-facing message when the interval is invalid
        (bad format, start not before end, overlaps the previous lesson);
        in that case state is left completely untouched.
      * ``done`` is ``True`` once all lesson slots have been collected,
        ``False`` while more slots remain.
    """
    data = await state.get_data()
    lesson_slots = data.get("lesson_slots", [])
    current_lesson_idx = data.get("current_lesson_idx", 1)
    lessons_count = data.get("lessons_count")

    prev_end = lesson_slots[-1][2] if lesson_slots else None
    try:
        start, end = parse_time_interval(slot_str)
        validate_against_previous(start, prev_end)
    except ValueError as e:
        return False, str(e)

    new_slots = lesson_slots + [(current_lesson_idx, start, end)]
    await state.update_data(lesson_slots=new_slots)

    if current_lesson_idx < lessons_count:
        await state.update_data(current_lesson_idx=current_lesson_idx + 1)
        return False, None  # more slots remain
    else:
        return True, None   # done


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
        "4️⃣ <b>Шаг 4: Расписание предметов</b>\n\n"
        f"📅 <b>{day_name}</b>\n"
        f"Урок №1 ({slot_time}): Какой предмет?\n"
        "Напиши название (например, <code>Математика</code>) или напиши <code>пропустить</code> / <code>skip</code>:",
        reply_markup=get_cancel_markup(),
        parse_mode="HTML",
    )


# Quick-pick time button handler
@router.callback_query(OnboardingStates.waiting_for_lesson_times, F.data.startswith("ob_time:"))
async def process_time_preset(callback: CallbackQuery, state: FSMContext):
    slot_str = callback.data[len("ob_time:"):]
    done, error = await _save_time_slot(state, slot_str)

    if error:
        await callback.answer(error, show_alert=True)
        return

    await callback.answer()

    if not done:
        data = await state.get_data()
        next_idx = data["current_lesson_idx"]
        await callback.message.edit_text(
            f"Введи время для <b>Урока №{next_idx}</b> в формате <code>ЧЧ:ММ - ЧЧ:ММ</code>\nили выбери готовый вариант:",
            reply_markup=build_time_keyboard(),
            parse_mode="HTML",
        )
    else:
        data = await state.get_data()
        await _try_delete(callback.message)
        await _start_schedule_setup(callback.message, state, data["lesson_slots"])


# Text time entry handler
@router.message(OnboardingStates.waiting_for_lesson_times, F.text)
async def process_lesson_times_text(message: Message, state: FSMContext):
    done, error = await _save_time_slot(state, message.text.strip())

    if error:
        await message.answer(f"⚠️ {html_escape(error)}", parse_mode="HTML")
        return

    if not done:
        data = await state.get_data()
        next_idx = data["current_lesson_idx"]
        await message.answer(
            f"Введи время для <b>Урока №{next_idx}</b> в формате <code>ЧЧ:ММ - ЧЧ:ММ</code>\nили выбери готовый вариант:",
            reply_markup=build_time_keyboard(),
            parse_mode="HTML",
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


SCHEDULE_ACTION_BUTTONS = {"⬅️ Назад", "📋 Скопировать вчерашний день", "⏭️ Пропустить"}


@router.message(OnboardingStates.waiting_for_schedule_subjects, F.text)
async def process_schedule_subjects(message: Message, state: FSMContext):
    subject_input = message.text.strip()

    # Cap real subject names (action buttons are short and exempt).
    if subject_input not in SCHEDULE_ACTION_BUTTONS and len(subject_input) > MAX_SUBJECT_LEN:
        await message.answer(
            f"Слишком длинное название предмета (макс. {MAX_SUBJECT_LEN} символов). Введите короче:"
        )
        return

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
            current_day_lessons = [lesson for lesson in current_day_lessons if lesson[0] != current_lesson_idx - 1]
            current_lesson_idx -= 1
            await state.update_data(current_lesson_idx=current_lesson_idx, current_day_lessons=current_day_lessons)
            slot = lesson_slots[current_lesson_idx - 1]
            slot_time = f"{slot[1]} - {slot[2]}"
            prev_day = all_schedule_data.get(target_days[current_day_list_idx - 1]) if current_day_list_idx > 0 else None
            await message.answer(
                f"📅 <b>{DAYS_RU[day_idx]}</b>\n"
                f"Урок №{current_lesson_idx} ({slot_time}): Какой предмет?",
                reply_markup=_get_subject_keyboard(day_idx, current_lesson_idx, prev_day),
                parse_mode="HTML",
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
                f"📅 <b>{DAYS_RU[prev_day_idx]}</b>\n"
                f"Урок №{new_lesson_idx} ({slot_time}): Какой предмет?\n"
                f"(Повторный ввод)",
                reply_markup=_get_subject_keyboard(prev_day_idx, new_lesson_idx, prev_prev_day),
                parse_mode="HTML",
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
            f"📅 <b>{DAYS_RU[day_idx]}</b>\n"
            f"Урок №{next_idx} ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши <code>пропустить</code>:",
            reply_markup=_get_subject_keyboard(day_idx, next_idx, prev_day),
            parse_mode="HTML",
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
            f"📅 <b>{DAYS_RU[next_day_idx]}</b>\n"
            f"Урок №1 ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши <code>пропустить</code>:",
            reply_markup=_get_subject_keyboard(next_day_idx, 1, prev_lessons),
            parse_mode="HTML",
        )
    else:
        # We finished all current target_days
        if day_idx == 4:
            # Just finished Friday, ask about Saturday
            await state.set_state(OnboardingStates.waiting_for_saturday_decision)
            await message.answer(
                "📅 Хочешь настроить расписание на <b>Субботу</b>?",
                reply_markup=get_yes_no_markup(),
                parse_mode="HTML",
            )
        else:
            # Finished Saturday — finalize onboarding
            await _finalize_onboarding(message, state, all_schedule_data)


async def _finalize_onboarding(
    message: Message, state: FSMContext, all_schedule_data, lesson_slots=None
):
    """
    Persists the entire onboarding result — the chosen profile and the settings
    it starts from, lesson slots, schedule for every configured day, and the
    is_onboarded flag — in a single DB transaction (see
    database.db.finalize_onboarding). If anything fails, nothing is written and
    the chat's previous configuration (if any) is left intact.

    ``lesson_slots`` is normally collected in FSM state; the tutor profile
    passes an empty list explicitly because it never asks for bell times.
    """
    chat_id = message.chat.id
    data = await state.get_data()
    if lesson_slots is None:
        lesson_slots = data["lesson_slots"]
    profile = profiles.normalize(data.get("profile"))

    # A profile's starting settings apply only when the profile actually
    # changes. Re-running onboarding on the same profile must not quietly undo
    # settings the chat has tuned by hand since.
    chat = await get_or_create_chat(chat_id, message.chat.type)
    extra: dict = {}
    if profile is not None and profile != profiles.normalize(chat.profile):
        d = profiles.defaults(profile)
        extra = {
            "hw_edit_policy": d.hw_edit_policy,
            "schedule_reminder_enabled": d.schedule_reminder_enabled,
            "changes_reminder_enabled": d.changes_reminder_enabled,
        }

    await finalize_onboarding(
        chat_id, message.chat.type, lesson_slots, all_schedule_data,
        profile=profile, **extra,
    )
    # First person to set the chat up owns it; a later re-run by somebody else
    # does not take that away from them.
    actor_id = data.get("onboarding_actor_id")
    if actor_id is not None:
        await set_chat_owner(chat_id, int(actor_id), only_if_empty=True)
    await state.clear()

    if profile is not None and not profiles.needs_schedule_onboarding(profile):
        await message.answer(
            "🎉 <b>Готово!</b>\n\n"
            f"Режим: <b>{profiles.PROFILE_LABELS[profile]}</b>. Школьного расписания "
            "здесь нет — сами занятия добавляй в разделе «🎯 Доп. занятия»: "
            "название, день, время и место.\n"
            "Домашние задания и напоминания работают как обычно.",
            reply_markup=await main_menu_for(message.chat.id, message.chat.type),
            parse_mode="HTML",
        )
        return

    await message.answer(
        "🎉 <b>Настройка завершена!</b>\n\n"
        "Все данные успешно сохранены. Теперь ты можешь полноценно пользоваться ботом!\n"
        "Вот твоё главное меню ниже 👇",
        reply_markup=await main_menu_for(message.chat.id, message.chat.type),
        parse_mode="HTML",
    )


@router.message(OnboardingStates.waiting_for_saturday_decision, F.text)
async def process_saturday_decision(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    data = await state.get_data()
    lesson_slots = data["lesson_slots"]
    all_schedule_data = data["all_schedule_data"]

    if text in YES_ANSWERS:
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
            "📅 <b>Суббота</b>\n"
            f"Урок №1 ({slot_time}): Какой предмет?\n"
            "Напиши название или напиши <code>пропустить</code>:",
            reply_markup=_get_subject_keyboard(5, 1, prev_lessons),
            parse_mode="HTML",
        )
    elif text in NO_ANSWERS:
        await _finalize_onboarding(message, state, all_schedule_data)
    else:
        await message.answer("Пожалуйста, ответь Да или Нет.")


# --- Fallback: non-text content during any onboarding step ---
async def onboarding_non_text(message: Message):
    await message.answer(NON_TEXT_HINT)


router.message.register(
    onboarding_non_text,
    StateFilter(
        OnboardingStates.waiting_for_profile,
        OnboardingStates.waiting_for_lessons_count,
        OnboardingStates.waiting_for_lesson_times,
        OnboardingStates.waiting_for_schedule_subjects,
        OnboardingStates.waiting_for_saturday_decision,
    ),
)
