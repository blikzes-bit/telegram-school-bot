import datetime
import pytz
from typing import Tuple, List
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from database.db import (
    get_homework, add_homework, mark_homework_completed, delete_homework, get_schedule,
    get_homework_by_id, update_homework,
)
from keyboards.inline import get_homework_action_keyboard, get_homework_edit_menu_keyboard, get_cancel_keyboard, DAYS_RU
from keyboards.reply import get_main_menu
from config import TIMEZONE
from utils import (
    escape_markdown, safe_edit_text,
    SAFE_PAGE_LIMIT, HW_MAX_PER_PAGE, MAX_SUBJECT_LEN, MAX_DESCRIPTION_LEN,
)

router = Router()
tz = pytz.timezone(TIMEZONE)

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или нажми «❌ Отмена»)."


class AddHomeworkStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_description = State()
    waiting_for_due_date = State()


class EditHomeworkStates(StatesGroup):
    waiting_for_new_value = State()


def _cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


async def format_homework_list(chat_id: int, is_archive: bool = False, page: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    homework_list = await get_homework(chat_id, is_completed=is_archive)

    title = "🗄️ **Архив выполненных заданий**" if is_archive else "📝 **Актуальные домашние задания**"
    scope = "arc" if is_archive else "act"

    def footer_buttons() -> List[List[InlineKeyboardButton]]:
        rows: List[List[InlineKeyboardButton]] = []
        if is_archive:
            rows.append([InlineKeyboardButton(text="🔙 К активным заданиям", callback_data="hw_list_active")])
        else:
            rows.append([
                InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add"),
                InlineKeyboardButton(text="🗄️ Архив", callback_data="hw_archive")
            ])
            rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="hw_list_active")])
        return rows

    if not homework_list:
        text = f"{title}\n\nНичего не найдено! 🎉"
        buttons = []
        if is_archive:
            buttons.append([InlineKeyboardButton(text="🔙 К активным заданиям", callback_data="hw_list_active")])
        else:
            buttons.append([InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add")])
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    today = datetime.datetime.now(tz).date()

    # --- Render each homework into a text block + button metadata ---
    rendered = []  # (hw, block_text, due_str)
    for hw in homework_list:
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
        block = f"**{safe_subject}** (до {due_str}{due_suffix}):\n   _{safe_desc}_"
        rendered.append((hw, block, due_str))

    # --- Greedy pagination so each page fits well within Telegram's limit ---
    header_budget = len(title) + 40  # title + page indicator + spacing
    pages: List[list] = []
    current: list = []
    current_len = header_budget
    for item in rendered:
        block_len = len(item[1]) + 8  # numbering + separators
        if current and (len(current) >= HW_MAX_PER_PAGE or current_len + block_len > SAFE_PAGE_LIMIT):
            pages.append(current)
            current, current_len = [], header_budget
        current.append(item)
        current_len += block_len
    if current:
        pages.append(current)

    total_pages = len(pages)
    page = max(0, min(page, total_pages - 1))
    page_items = pages[page]

    text = title
    if total_pages > 1:
        text += f"  (стр. {page + 1}/{total_pages})"
    text += "\n\n"
    for local_i, (hw, block, due_str) in enumerate(page_items, 1):
        text += f"{local_i}️⃣ {block}\n\n"

    buttons: List[List[InlineKeyboardButton]] = []
    for hw, block, due_str in page_items:
        buttons.append([
            InlineKeyboardButton(
                text=f"{'📁' if is_archive else '📌'} {hw.subject_name} ({due_str})",
                callback_data=f"hw_view_actions:{hw.id}:{1 if is_archive else 0}"
            )
        ])

    # Pagination navigation row
    if total_pages > 1:
        nav: List[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"hw_page:{scope}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="hw_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"hw_page:{scope}:{page + 1}"))
        buttons.append(nav)

    buttons.extend(footer_buttons())

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "📝 Домашнее задание")
async def show_homework(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "hw_list_active")
async def process_hw_list_active(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "hw_archive")
async def process_hw_archive(callback: CallbackQuery):
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=True)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("hw_page:"))
async def process_hw_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    scope = parts[1]
    page = int(parts[2])
    is_archive = scope == "arc"
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=is_archive, page=page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "hw_noop")
async def process_hw_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("hw_view_actions:"))
async def process_hw_view_actions(callback: CallbackQuery, state: FSMContext):
    # Also reachable as a "back"/cancel target from the edit-field menu, so
    # clear any leftover edit state.
    await state.clear()
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

    await safe_edit_text(
        callback.message,
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
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("hw_restore:"))
async def process_hw_restore(callback: CallbackQuery):
    hw_id = int(callback.data.split(":")[1])
    await mark_homework_completed(callback.message.chat.id, hw_id, is_completed=False)
    await callback.answer("Задание возвращено в активный список.")
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=True)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("hw_delete:"))
async def process_hw_delete(callback: CallbackQuery):
    hw_id = int(callback.data.split(":")[1])
    await delete_homework(callback.message.chat.id, hw_id)
    await callback.answer("Задание успешно удалено.")
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")


# ----------- EDIT HOMEWORK FSM -----------

EDIT_FIELD_PROMPTS = {
    "subject": "📚 Текущий предмет: **{value}**\n\nВведите новое название предмета:",
    "desc": "📝 Текущее описание: _{value}_\n\nВведите новый текст задания:",
    "date": "📅 Текущая дата сдачи: {value}\n\nВведите новую дату в формате `ДД.ММ` (например, `14.10`):",
}


async def _reject_missing_homework(callback: CallbackQuery, is_archive: bool):
    await callback.answer("⚠️ Это задание не найдено (возможно, уже удалено).", show_alert=True)
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=is_archive)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("hw_edit_menu:"))
async def show_edit_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    hw_id = int(parts[1])
    is_archive = int(parts[2]) == 1

    hw = await get_homework_by_id(callback.message.chat.id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive)
        return

    kb = get_homework_edit_menu_keyboard(hw_id, is_archive)
    await safe_edit_text(
        callback.message,
        f"✏️ **Редактирование задания**\n\n"
        f"**{escape_markdown(hw.subject_name)}** (до {hw.due_date.strftime('%d.%m')})\n\n"
        "Что вы хотите изменить?",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hw_edit_field:"))
async def initiate_edit_field(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    hw_id = int(parts[1])
    field = parts[2]
    is_archive = int(parts[3]) == 1

    hw = await get_homework_by_id(callback.message.chat.id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive)
        return

    current_value = {
        "subject": escape_markdown(hw.subject_name),
        "desc": escape_markdown(hw.description),
        "date": hw.due_date.strftime("%d.%m"),
    }[field]

    await state.update_data(edit_hw_id=hw_id, edit_field=field, edit_is_archive=is_archive)
    await state.set_state(EditHomeworkStates.waiting_for_new_value)

    await safe_edit_text(
        callback.message,
        EDIT_FIELD_PROMPTS[field].format(value=current_value),
        reply_markup=get_cancel_keyboard(callback_data=f"hw_edit_menu:{hw_id}:{1 if is_archive else 0}"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(EditHomeworkStates.waiting_for_new_value, F.text)
async def process_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    hw_id = data["edit_hw_id"]
    field = data["edit_field"]
    is_archive = data["edit_is_archive"]
    text = message.text.strip()

    update_kwargs = {}
    if field == "subject":
        if not text:
            await message.answer("Название предмета не может быть пустым. Введите название предмета:")
            return
        if len(text) > MAX_SUBJECT_LEN:
            await message.answer(f"Слишком длинное название (макс. {MAX_SUBJECT_LEN} символов). Введите короче:")
            return
        update_kwargs["subject_name"] = text
    elif field == "desc":
        if not text:
            await message.answer("Текст задания не может быть пустым. Введите текст домашнего задания:")
            return
        if len(text) > MAX_DESCRIPTION_LEN:
            await message.answer(f"Слишком длинный текст (макс. {MAX_DESCRIPTION_LEN} символов). Введите короче:")
            return
        update_kwargs["description"] = text
    else:  # field == "date"
        try:
            day, month = map(int, text.split("."))
            today = datetime.datetime.now(tz).date()
            due_date = datetime.date(today.year, month, day)
            if due_date < today:
                due_date = datetime.date(today.year + 1, month, day)
        except Exception:
            await message.answer(
                "Неверный формат даты! Укажи дату в формате `ДД.ММ` (например, `14.10`):",
                parse_mode="Markdown"
            )
            return
        update_kwargs["due_date"] = due_date

    updated = await update_homework(message.chat.id, hw_id, **update_kwargs)
    await state.clear()

    if not updated:
        await message.answer(
            "⚠️ Это задание уже не существует (возможно, было удалено).",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer("✅ Задание успешно обновлено!", reply_markup=get_main_menu())

    hw_text, kb = await format_homework_list(message.chat.id, is_archive=is_archive)
    await message.answer(hw_text, reply_markup=kb, parse_mode="Markdown")


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

    await safe_edit_text(
        callback.message,
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

    await safe_edit_text(
        callback.message,
        f"📝 Предмет: **{escape_markdown(subject)}**\n\nВведите текст домашнего задания:",
        reply_markup=get_cancel_keyboard(callback_data="hw_list_active"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(AddHomeworkStates.waiting_for_subject, F.text)
async def process_subject_text(message: Message, state: FSMContext):
    subject = message.text.strip()
    if subject == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    if not subject:
        await message.answer("Название предмета не может быть пустым. Введите название предмета:")
        return
    if len(subject) > MAX_SUBJECT_LEN:
        await message.answer(f"Слишком длинное название (макс. {MAX_SUBJECT_LEN} символов). Введите короче:")
        return

    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)

    await message.answer(
        f"📝 Предмет: **{escape_markdown(subject)}**\n\nВведите текст домашнего задания:",
        reply_markup=_cancel_reply_keyboard(),
        parse_mode="Markdown"
    )


@router.message(AddHomeworkStates.waiting_for_description, F.text)
async def process_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if description == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    if not description:
        await message.answer("Текст задания не может быть пустым. Введите текст домашнего задания:")
        return
    if len(description) > MAX_DESCRIPTION_LEN:
        await message.answer(f"Слишком длинный текст (макс. {MAX_DESCRIPTION_LEN} символов). Введите короче:")
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


@router.message(AddHomeworkStates.waiting_for_due_date, F.text)
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


# --- Fallback: non-text content while a homework FSM step expects text ---
async def homework_non_text(message: Message):
    await message.answer(NON_TEXT_HINT)


router.message.register(
    homework_non_text,
    StateFilter(
        AddHomeworkStates.waiting_for_subject,
        AddHomeworkStates.waiting_for_description,
        AddHomeworkStates.waiting_for_due_date,
        EditHomeworkStates.waiting_for_new_value,
    ),
)
