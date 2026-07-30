import datetime
from typing import Tuple, List
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from database.db import (
    get_homework, add_homework, mark_homework_completed, delete_homework, get_schedule,
    get_homework_by_id, update_homework, get_chat,
    add_homework_attachment, get_homework_attachments, count_homework_attachments,
    get_homework_attachment, delete_homework_attachment, get_attachment_counts,
)
from keyboards.inline import (
    get_homework_action_keyboard, get_homework_edit_menu_keyboard,
    get_homework_delete_confirm_keyboard, get_cancel_keyboard, DAYS_RU,
    get_attachment_collect_keyboard, get_attachment_menu_keyboard,
    get_attachment_delete_confirm_keyboard,
)
from keyboards.reply import get_main_menu
from keyboards.calendar import build_calendar, month_token, parse_month
import services.audit as audit
import services.timeservice as ts
from services.attachments import extract_attachment
from services.permissions import require_homework_access
from utils import (
    html_escape, safe_edit_text, safe_callback_ints, next_occurrence,
    format_file_size, SAFE_PAGE_LIMIT, HW_MAX_PER_PAGE, MAX_SUBJECT_LEN,
    MAX_DESCRIPTION_LEN, MAX_ATTACHMENTS_PER_HOMEWORK,
)

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или нажми «❌ Отмена»)."
STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой список заново."

# Human labels for the fields an edit can touch, used in audit summaries so the
# journal records *what* changed without ever storing the new value itself.
EDIT_FIELD_LABELS = {"subject": "предмет", "desc": "описание", "date": "дата сдачи"}


async def _guard(event, homework) -> bool:
    """
    Server-side homework-edit policy check for one entry.

    Every mutating handler calls this *before* touching the DB — hiding a button
    is not protection, a stale or hand-crafted callback must be rejected here.
    Reads the live Chat row so a policy change takes effect immediately.
    """
    chat_id = event.message.chat.id if hasattr(event, "data") else event.chat.id
    chat = await get_chat(chat_id)
    return await require_homework_access(event, chat, homework)


def _author_lines(hw, tz=None) -> str:
    """
    "Кто добавил / кто изменил" lines for a homework card.

    Both are optional: an entry created before authorship existed simply has no
    author, and is shown as such rather than being attributed to anyone. ``tz``
    is the chat's timezone, so the stored UTC timestamps are rendered in the
    chat's own local time.
    """
    lines = []
    created = audit.actor_label(hw.created_by_user_id, hw.created_by_name)
    if hw.created_by_user_id is not None or hw.created_by_name:
        lines.append(f"👤 Добавил(а): <b>{html_escape(created)}</b> · {audit.format_ts(hw.created_at, tz)}")
    else:
        lines.append("👤 Автор неизвестен <i>(запись создана до учёта авторства)</i>")
    changed_by_someone_else = (
        hw.updated_by_user_id is not None
        and hw.updated_by_user_id != hw.created_by_user_id
    )
    if changed_by_someone_else or (hw.updated_at and hw.updated_at != hw.created_at):
        updated = audit.actor_label(hw.updated_by_user_id, hw.updated_by_name)
        lines.append(f"✏️ Изменил(а): <b>{html_escape(updated)}</b> · {audit.format_ts(hw.updated_at, tz)}")
    return "\n".join(lines)


class AddHomeworkStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_description = State()
    # Attachments are collected right after the description, before the due
    # date. The homework row doesn't exist yet at this point, so the references
    # (small strings — never binaries) are buffered in the FSM state and written
    # together with the entry once the date is chosen.
    waiting_for_attachments = State()
    waiting_for_due_date = State()


class EditHomeworkStates(StatesGroup):
    waiting_for_new_value = State()
    # Adding a file to an entry that already exists.
    waiting_for_attachment = State()


def _cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


async def format_homework_list(chat_id: int, is_archive: bool = False, page: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    homework_list = await get_homework(chat_id, is_completed=is_archive)

    title = "🗄️ <b>Архив выполненных заданий</b>" if is_archive else "📝 <b>Актуальные домашние задания</b>"
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

    today = await ts.today_for_chat_id(chat_id)
    # One query for the whole list rather than one per entry.
    attachment_counts = await get_attachment_counts(chat_id)

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

        safe_subject = html_escape(hw.subject_name)
        safe_desc = html_escape(hw.description)
        files = attachment_counts.get(hw.id, 0)
        clip = f" 📎{files}" if files else ""
        block = f"<b>{safe_subject}</b> (до {due_str}{due_suffix}){clip}:\n   <i>{safe_desc}</i>"
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
                callback_data=f"hw_view_actions:{hw.id}:{1 if is_archive else 0}:{page}"
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


@router.message(Command("homework"))
@router.message(F.text == "📝 Домашнее задание")
async def show_homework(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "hw_list_active")
async def process_hw_list_active(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "hw_archive")
async def process_hw_archive(callback: CallbackQuery):
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=True)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("hw_page:"))
async def process_hw_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[1] not in ("act", "arc"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    page = None
    try:
        page = int(parts[2])
    except ValueError:
        pass
    if page is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    is_archive = parts[1] == "arc"
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=is_archive, page=page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "hw_noop")
async def process_hw_noop(callback: CallbackQuery):
    await callback.answer()


def format_homework_card(hw, attachment_count: int = 0, tz=None) -> str:
    """
    The detail card for one homework entry: subject, due date, text, who
    added/last changed it and how many files are attached. All user-provided
    text is HTML-escaped; ``tz`` renders the timestamps in the chat's own time.
    """
    text = (
        f"📌 <b>{html_escape(hw.subject_name)}</b> (до {hw.due_date.strftime('%d.%m')})\n"
        f"<i>{html_escape(hw.description)}</i>\n\n"
        f"{_author_lines(hw, tz)}\n"
    )
    if attachment_count:
        text += f"📎 Вложений: <b>{attachment_count}</b>\n"
    return text + "\n⚙️ <b>Выберите действие:</b>"


@router.callback_query(F.data.startswith("hw_view_actions:"))
async def process_hw_view_actions(callback: CallbackQuery, state: FSMContext):
    # Also reachable as a "back"/cancel target from the edit-field menu, so
    # clear any leftover edit state.
    await state.clear()
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return

    attachments = await get_homework_attachments(chat_id, hw_id)
    kb = get_homework_action_keyboard(hw_id, is_archive, page, len(attachments))

    await safe_edit_text(
        callback.message,
        format_homework_card(hw, len(attachments), await ts.tz_for_chat_id(chat_id)),
        reply_markup=kb,
        parse_mode="HTML"
    )
    # Opening a card also delivers its files, right below it.
    await send_attachments(callback.message, attachments)
    await callback.answer()


# ----------- ATTACHMENTS -----------

STALE_FILE_TEXT = (
    "⚠️ Не удалось отправить вложение{name}: Telegram больше не принимает эту ссылку "
    "на файл (так бывает со старыми вложениями). Открой «📎 Вложения», удали это "
    "вложение и приложи файл заново."
)


def _attachment_label(attachment, index: int = 0) -> str:
    """Short, escaped label for one attachment line/button."""
    if attachment.file_name:
        return html_escape(attachment.file_name)
    if attachment.file_type == "photo":
        return f"Фото {index}" if index else "Фото"
    return f"Файл {index}" if index else "Файл"


async def send_attachments(message, attachments) -> int:
    """
    Send a homework's attachments as separate messages, right after its card.

    Each file is sent independently so one dead reference can't hide the rest:
    Telegram invalidates a ``file_id`` occasionally (and always for a file from
    another bot), and the only honest recovery is to tell the user which
    attachment broke and how to replace it. Returns how many were delivered.
    """
    sent = 0
    for index, attachment in enumerate(attachments, 1):
        caption = f"📎 {_attachment_label(attachment, index)}"
        if attachment.caption:
            caption += f"\n{html_escape(attachment.caption)}"
        try:
            if attachment.file_type == "photo":
                await message.answer_photo(attachment.file_id, caption=caption, parse_mode="HTML")
            else:
                await message.answer_document(attachment.file_id, caption=caption, parse_mode="HTML")
            sent += 1
        except Exception:
            name = f" «{_attachment_label(attachment, index)}»"
            await message.answer(STALE_FILE_TEXT.format(name=name), parse_mode="HTML")
    return sent


def _attachment_lines(attachments) -> str:
    """The attachment list body for the manage screen."""
    if not attachments:
        return "Пока нет ни одного вложения."
    lines = []
    for index, attachment in enumerate(attachments, 1):
        icon = "🖼" if attachment.file_type == "photo" else "📄"
        size = format_file_size(attachment.file_size)
        line = f"{index}. {icon} <b>{_attachment_label(attachment, index)}</b>"
        if size:
            line += f" <i>({size})</i>"
        if attachment.caption:
            line += f"\n   📝 {html_escape(attachment.caption)}"
        lines.append(line)
    return "\n".join(lines)


async def render_attachment_menu(chat_id: int, hw_id: int, is_archive: bool, page: int):
    """``(text, keyboard)`` for the "📎 Вложения" screen, or ``None`` if gone."""
    hw = await get_homework_by_id(chat_id, hw_id)
    if hw is None:
        return None
    attachments = await get_homework_attachments(chat_id, hw_id)
    text = (
        f"📎 <b>Вложения — {html_escape(hw.subject_name)}</b>\n\n"
        f"{_attachment_lines(attachments)}\n\n"
        f"<i>Можно приложить до {MAX_ATTACHMENTS_PER_HOMEWORK} фото или файлов. "
        "Бот не хранит сами файлы — только ссылку Telegram на них.</i>"
    )
    kb = get_attachment_menu_keyboard(
        attachments, hw_id, is_archive, page,
        can_add=len(attachments) < MAX_ATTACHMENTS_PER_HOMEWORK,
    )
    return text, kb


@router.callback_query(F.data.startswith("hw_att_menu:"))
async def process_attachment_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    rendered = await render_attachment_menu(callback.message.chat.id, hw_id, is_archive, page)
    if rendered is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    text, kb = rendered
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("hw_att_add:"))
async def process_attachment_add(callback: CallbackQuery, state: FSMContext):
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    # Attaching a file changes an existing entry → same policy as editing it.
    if not await _guard(callback, hw):
        return
    if await count_homework_attachments(chat_id, hw_id) >= MAX_ATTACHMENTS_PER_HOMEWORK:
        await callback.answer(
            f"⚠️ Больше {MAX_ATTACHMENTS_PER_HOMEWORK} вложений к одному заданию нельзя. "
            "Удали лишнее и попробуй снова.",
            show_alert=True,
        )
        return

    await state.update_data(att_hw_id=hw_id, att_is_archive=is_archive, att_page=page)
    await state.set_state(EditHomeworkStates.waiting_for_attachment)
    await safe_edit_text(
        callback.message,
        "📎 Пришли фотографию или файл (документ) — можно с подписью.\n\n"
        "<i>Файлы не скачиваются и не распаковываются: бот сохраняет только "
        "ссылку Telegram на них.</i>",
        reply_markup=get_cancel_keyboard(
            callback_data=f"hw_att_menu:{hw_id}:{archive_flag}:{page}"
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(EditHomeworkStates.waiting_for_attachment)
async def process_attachment_upload(message: Message, state: FSMContext):
    """
    Accept one photo/document for an existing homework entry.

    Handles *any* content type on purpose (no ``F.photo``-style filter) so an
    unsupported kind gets a clear explanation and the step stays open for a
    retry, instead of falling through to some other handler.
    """
    data = await state.get_data()
    hw_id = data.get("att_hw_id")
    is_archive = bool(data.get("att_is_archive"))
    page = data.get("att_page", 0)
    if hw_id is None:
        await state.clear()
        await message.answer(STALE_BUTTON_TEXT)
        return

    if (message.text or "").strip() == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление вложения отменено.", reply_markup=get_main_menu())
        return

    info, error = extract_attachment(message)
    if info is None:
        await message.answer(error)  # stay in this state so the user can retry
        return

    chat_id = message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if not await _guard(message, hw):
        await state.clear()
        return

    actor_user_id, actor_name = audit.actor_from(message)
    result = await add_homework_attachment(
        chat_id, hw_id,
        file_id=info.file_id, file_unique_id=info.file_unique_id,
        file_type=info.file_type, file_name=info.file_name,
        file_size=info.file_size, caption=info.caption,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )

    if result.status == "missing":
        await state.clear()
        await message.answer(
            "⚠️ Это задание уже не существует (возможно, было удалено).",
            reply_markup=get_main_menu(),
        )
        return
    if result.status == "limit":
        await state.clear()
        await message.answer(
            f"⚠️ К одному заданию можно приложить не больше {MAX_ATTACHMENTS_PER_HOMEWORK} "
            "файлов. Удали лишнее и попробуй снова.",
            reply_markup=get_main_menu(),
        )
        return
    if result.status == "duplicate":
        await message.answer("ℹ️ Этот файл уже приложен к заданию. Можно прислать другой.")
        return

    await audit.record_event(
        message, chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_UPDATE,
        entity_id=hw_id,
        summary=audit.summarize(
            hw.subject_name if hw else None,
            "добавлено вложение",
            info.file_name or ("фото" if info.file_type == "photo" else "файл"),
        ),
    )
    await state.clear()
    await message.answer("✅ Вложение добавлено!", reply_markup=get_main_menu())

    rendered = await render_attachment_menu(chat_id, hw_id, is_archive, page)
    if rendered is not None:
        text, kb = rendered
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("hw_att_del_ask:"))
async def process_attachment_delete_ask(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1, 2, 3, 4)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    attachment_id, hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    if not await _guard(callback, hw):
        return
    attachment = await get_homework_attachment(chat_id, attachment_id)
    if attachment is None:
        await callback.answer("⚠️ Это вложение уже удалено.", show_alert=True)
        rendered = await render_attachment_menu(chat_id, hw_id, is_archive, page)
        if rendered is not None:
            text, kb = rendered
            await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
        return

    await safe_edit_text(
        callback.message,
        f"❗ Удалить вложение «{_attachment_label(attachment)}» из этого задания?",
        reply_markup=get_attachment_delete_confirm_keyboard(
            attachment_id, hw_id, is_archive, page
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hw_att_del:"))
async def process_attachment_delete(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1, 2, 3, 4)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    attachment_id, hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    if not await _guard(callback, hw):
        return

    attachment = await get_homework_attachment(chat_id, attachment_id)
    name = attachment.file_name if attachment else None
    removed = await delete_homework_attachment(chat_id, attachment_id)
    if removed:
        await audit.record_event(
            callback, chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_UPDATE,
            entity_id=hw_id,
            summary=audit.summarize(hw.subject_name, "удалено вложение", name),
        )
        await callback.answer("Вложение удалено.")
    else:
        await callback.answer("⚠️ Это вложение уже удалено.", show_alert=True)

    rendered = await render_attachment_menu(chat_id, hw_id, is_archive, page)
    if rendered is not None:
        text, kb = rendered
        await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


async def _set_completed(callback: CallbackQuery, hw_id: int, page: int, completed: bool):
    """Shared body of the complete/restore buttons (they differ only in flag)."""
    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if not await _guard(callback, hw):
        return
    actor_user_id, actor_name = audit.actor_from(callback)
    ok = await mark_homework_completed(
        chat_id, hw_id, is_completed=completed,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    if not ok:
        await callback.answer("⚠️ Это задание уже не существует.", show_alert=True)
    else:
        await audit.record_event(
            callback, chat_id, audit.ENTITY_HOMEWORK,
            audit.ACTION_COMPLETE if completed else audit.ACTION_RESTORE,
            entity_id=hw_id,
            summary=audit.summarize(hw.subject_name if hw else None),
        )
        await callback.answer(
            "Задание отмечено как выполненное! 🎉" if completed
            else "Задание возвращено в активный список."
        )
    # Completing shows the active list again; restoring stays in the archive.
    text, kb = await format_homework_list(chat_id, is_archive=not completed, page=page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("hw_complete:"))
async def process_hw_complete(callback: CallbackQuery):
    ints = safe_callback_ints(callback.data, 1, 2)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await _set_completed(callback, ints[0], ints[1], completed=True)


@router.callback_query(F.data.startswith("hw_restore:"))
async def process_hw_restore(callback: CallbackQuery):
    ints = safe_callback_ints(callback.data, 1, 2)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await _set_completed(callback, ints[0], ints[1], completed=False)


@router.callback_query(F.data.startswith("hw_delete_ask:"))
async def process_hw_delete_ask(callback: CallbackQuery):
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    hw = await get_homework_by_id(callback.message.chat.id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    if not await _guard(callback, hw):
        return

    await safe_edit_text(
        callback.message,
        f"❗ Удалить задание «{html_escape(hw.subject_name)}» безвозвратно?",
        reply_markup=get_homework_delete_confirm_keyboard(hw_id, is_archive, page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hw_delete_confirm:"))
async def process_hw_delete_confirm(callback: CallbackQuery):
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    chat_id = callback.message.chat.id
    hw = await get_homework_by_id(chat_id, hw_id)
    if not await _guard(callback, hw):
        return
    # Read the subject before deleting so the audit entry can name what went —
    # the record disappears, its safe journal line stays.
    subject = hw.subject_name if hw is not None else None
    ok = await delete_homework(chat_id, hw_id)
    if not ok:
        await callback.answer("⚠️ Это задание уже не существует.", show_alert=True)
    else:
        await audit.record_event(
            callback, chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_DELETE,
            entity_id=hw_id, summary=audit.summarize(subject),
        )
        await callback.answer("Задание успешно удалено.")
    # Returns to the SAME list (archive stays archive, active stays active).
    text, kb = await format_homework_list(chat_id, is_archive=is_archive, page=page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


# ----------- EDIT HOMEWORK FSM -----------

EDIT_FIELD_PROMPTS = {
    "subject": "📚 Текущий предмет: <b>{value}</b>\n\nВведите новое название предмета:",
    "desc": "📝 Текущее описание: <i>{value}</i>\n\nВведите новый текст задания:",
    "date": "📅 Текущая дата сдачи: {value}\n\nВведите новую дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
}


async def _reject_missing_homework(callback: CallbackQuery, is_archive: bool, page: int = 0):
    await callback.answer("⚠️ Это задание не найдено (возможно, уже удалено).", show_alert=True)
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=is_archive, page=page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("hw_edit_menu:"))
async def show_edit_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1, 2, 3)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    is_archive = archive_flag == 1

    hw = await get_homework_by_id(callback.message.chat.id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    if not await _guard(callback, hw):
        return

    kb = get_homework_edit_menu_keyboard(hw_id, is_archive, page)
    await safe_edit_text(
        callback.message,
        f"✏️ <b>Редактирование задания</b>\n\n"
        f"<b>{html_escape(hw.subject_name)}</b> (до {hw.due_date.strftime('%d.%m')})\n"
        f"{_author_lines(hw, await ts.tz_for_chat_id(callback.message.chat.id))}\n\n"
        "Что вы хотите изменить?",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hw_edit_field:"))
async def initiate_edit_field(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    ints = safe_callback_ints(callback.data, 1, 3, 4)
    if ints is None or len(parts) < 5 or parts[2] not in ("subject", "desc", "date"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    hw_id, archive_flag, page = ints
    field = parts[2]
    is_archive = archive_flag == 1

    hw = await get_homework_by_id(callback.message.chat.id, hw_id)
    if hw is None:
        await _reject_missing_homework(callback, is_archive, page)
        return
    if not await _guard(callback, hw):
        return

    current_value = {
        "subject": html_escape(hw.subject_name),
        "desc": html_escape(hw.description),
        "date": hw.due_date.strftime("%d.%m"),
    }[field]

    await state.update_data(edit_hw_id=hw_id, edit_field=field, edit_is_archive=is_archive, edit_page=page)
    await state.set_state(EditHomeworkStates.waiting_for_new_value)

    await safe_edit_text(
        callback.message,
        EDIT_FIELD_PROMPTS[field].format(value=current_value),
        reply_markup=get_cancel_keyboard(callback_data=f"hw_edit_menu:{hw_id}:{1 if is_archive else 0}:{page}"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EditHomeworkStates.waiting_for_new_value, F.text)
async def process_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    hw_id = data["edit_hw_id"]
    field = data["edit_field"]
    is_archive = data["edit_is_archive"]
    page = data.get("edit_page", 0)
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
            due_date = next_occurrence(month, day, await ts.today_for_chat_id(message.chat.id))
        except ValueError:
            await message.answer(
                "Неверный формат даты! Укажи дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
                parse_mode="HTML"
            )
            return
        update_kwargs["due_date"] = due_date

    # Re-check the policy at write time, not only when the menu was opened:
    # the entry (or the chat's policy) may have changed while the user typed.
    hw = await get_homework_by_id(message.chat.id, hw_id)
    if not await _guard(message, hw):
        await state.clear()
        return

    actor_user_id, actor_name = audit.actor_from(message)
    updated = await update_homework(
        message.chat.id, hw_id,
        actor_user_id=actor_user_id, actor_name=actor_name,
        **update_kwargs,
    )
    await state.clear()

    if not updated:
        await message.answer(
            "⚠️ Это задание уже не существует (возможно, было удалено).",
            reply_markup=get_main_menu()
        )
    else:
        await audit.record_event(
            message, message.chat.id, audit.ENTITY_HOMEWORK, audit.ACTION_UPDATE,
            entity_id=hw_id,
            summary=audit.summarize(
                hw.subject_name if hw else None,
                audit.fields_summary([EDIT_FIELD_LABELS.get(field, field)]),
            ),
        )
        await message.answer("✅ Задание успешно обновлено!", reply_markup=get_main_menu())

    hw_text, kb = await format_homework_list(message.chat.id, is_archive=is_archive, page=page)
    await message.answer(hw_text, reply_markup=kb, parse_mode="HTML")


# ----------- ADD HOMEWORK FSM -----------

async def _create_homework(event, chat_id: int, subject: str, due_date, description: str, files=None):
    """
    Persist a new homework entry stamped with its author, attach the files
    buffered during the flow, and journal the creation.

    Adding is never policy-gated: every policy is about who may change an
    *existing* entry, and a class chat must stay able to record its homework.
    """
    actor_user_id, actor_name = audit.actor_from(event)
    hw = await add_homework(
        chat_id, subject, due_date, description,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    attached = 0
    for item in files or []:
        result = await add_homework_attachment(
            chat_id, hw.id,
            file_id=item["file_id"], file_unique_id=item["file_unique_id"],
            file_type=item["file_type"], file_name=item.get("file_name"),
            file_size=item.get("file_size"), caption=item.get("caption"),
            actor_user_id=actor_user_id, actor_name=actor_name,
        )
        if result.status == "ok":
            attached += 1
    await audit.record_event(
        event, chat_id, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE,
        entity_id=hw.id,
        summary=audit.summarize(
            subject,
            f"до {due_date.strftime('%d.%m.%Y')}",
            f"вложений: {attached}" if attached else None,
        ),
    )
    return hw


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
        "➕ <b>Добавление домашнего задания</b>\n\n"
        "Выберите предмет из списка ниже или введите название предмета вручную:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(AddHomeworkStates.waiting_for_subject, F.data.startswith("hwa_sub:"))
async def process_subject_callback(callback: CallbackQuery, state: FSMContext):
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    idx = ints[0]
    data = await state.get_data()
    subjects = data.get("hw_subjects", [])

    if idx < 0 or idx >= len(subjects):
        await callback.answer("Предмет не найден.", show_alert=True)
        return

    subject = subjects[idx]
    await state.update_data(hw_subject=subject)
    await state.set_state(AddHomeworkStates.waiting_for_description)

    await safe_edit_text(
        callback.message,
        f"📝 Предмет: <b>{html_escape(subject)}</b>\n\nВведите текст домашнего задания:",
        reply_markup=get_cancel_keyboard(callback_data="hw_list_active"),
        parse_mode="HTML"
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
        f"📝 Предмет: <b>{html_escape(subject)}</b>\n\nВведите текст домашнего задания:",
        reply_markup=_cancel_reply_keyboard(),
        parse_mode="HTML"
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

    await state.update_data(hw_description=description, hw_files=[])
    await state.set_state(AddHomeworkStates.waiting_for_attachments)
    await message.answer(
        _attachment_prompt(0),
        reply_markup=get_attachment_collect_keyboard(0),
        parse_mode="HTML",
    )


def _attachment_prompt(count: int) -> str:
    if not count:
        return (
            "📎 <b>Вложения</b>\n\n"
            "Пришли фотографию или файл (документ), если к заданию нужно что-то приложить — "
            f"можно до {MAX_ATTACHMENTS_PER_HOMEWORK} штук, с подписью.\n"
            "Или нажми «⏭ Пропустить», если вложения не нужны.\n\n"
            "<i>Бот не скачивает и не распаковывает файлы — сохраняется только "
            "ссылка Telegram на них.</i>"
        )
    return (
        f"📎 Приложено файлов: <b>{count}</b> из {MAX_ATTACHMENTS_PER_HOMEWORK}.\n\n"
        "Пришли ещё файл или нажми «✅ Готово», чтобы перейти к дате сдачи."
    )


@router.message(AddHomeworkStates.waiting_for_attachments)
async def process_add_attachment(message: Message, state: FSMContext):
    """
    Buffer one attachment reference while adding a new homework entry.

    The entry doesn't exist yet, so nothing is written to the DB here: only the
    (small) Telegram references are kept in the FSM state and persisted together
    with the entry once the due date is set. Any content type is accepted so an
    unsupported one gets a clear explanation instead of silently falling through.
    """
    text = (message.text or "").strip()
    if text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_menu())
        return

    data = await state.get_data()
    files = list(data.get("hw_files", []))

    if len(files) >= MAX_ATTACHMENTS_PER_HOMEWORK:
        await message.answer(
            f"⚠️ Больше {MAX_ATTACHMENTS_PER_HOMEWORK} вложений нельзя. "
            "Нажми «✅ Готово», чтобы продолжить.",
            reply_markup=get_attachment_collect_keyboard(len(files)),
        )
        return

    info, error = extract_attachment(message)
    if info is None:
        await message.answer(error, reply_markup=get_attachment_collect_keyboard(len(files)))
        return

    if any(item["file_unique_id"] == info.file_unique_id for item in files):
        await message.answer(
            "ℹ️ Этот файл уже приложен. Пришли другой или нажми «✅ Готово».",
            reply_markup=get_attachment_collect_keyboard(len(files)),
        )
        return

    files.append({
        "file_id": info.file_id,
        "file_unique_id": info.file_unique_id,
        "file_type": info.file_type,
        "file_name": info.file_name,
        "file_size": info.file_size,
        "caption": info.caption,
    })
    await state.update_data(hw_files=files)
    await message.answer(
        _attachment_prompt(len(files)),
        reply_markup=get_attachment_collect_keyboard(len(files)),
        parse_mode="HTML",
    )


@router.callback_query(AddHomeworkStates.waiting_for_attachments, F.data == "hwa_files_done")
async def process_attachments_done(callback: CallbackQuery, state: FSMContext):
    await _prompt_due_date(callback.message, state, edit=True)
    await callback.answer()


async def _prompt_due_date(message: Message, state: FSMContext, edit: bool = False):
    """
    Ask for the due date, offering "tomorrow" / "the day after" / "next lesson
    for this subject" shortcuts. Shared by the attachment step's finish button
    (which edits the existing message) and any other entry point.
    """
    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]
    files = data.get("hw_files", [])

    today = await ts.today_for_chat_id(message.chat.id)
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

    buttons.append([
        InlineKeyboardButton(
            text="🗓 Календарь",
            callback_data=f"hwa_cal:{month_token(today.year, today.month)}",
        )
    ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")])
    await state.set_state(AddHomeworkStates.waiting_for_due_date)

    text = (
        f"📝 Предмет: <b>{html_escape(subject)}</b>\n"
        f"📋 Задание: <i>{html_escape(description)}</i>\n"
    )
    if files:
        text += f"📎 Вложений: <b>{len(files)}</b>\n"
    text += (
        "\nВыбери дату сдачи или введи вручную в формате "
        "<code>ДД.ММ</code> (например, <code>14.10</code>):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await safe_edit_text(message, text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(AddHomeworkStates.waiting_for_due_date, F.data.startswith("hwa_date:"))
async def process_due_date_callback(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split(":", 1)[1]
    try:
        due_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return

    today = await ts.today_for_chat_id(callback.message.chat.id)
    if due_date < today:
        # A stale "Завтра"/"Послезавтра" button pressed after midnight would
        # otherwise silently create an already-overdue homework entry.
        await callback.answer(
            "⚠️ Эта дата уже в прошлом. Выбери дату заново или введи вручную.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]

    await _create_homework(
        callback, callback.message.chat.id, subject, due_date, description,
        files=data.get("hw_files"),
    )
    await state.clear()

    safe_sub = html_escape(subject)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        f"✅ Домашнее задание по предмету <b>{safe_sub}</b> на {due_date.strftime('%d.%m')} сохранено!",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    text, kb = await format_homework_list(callback.message.chat.id, is_archive=False)
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(AddHomeworkStates.waiting_for_due_date, F.data.startswith("hwa_cal:"))
async def navigate_due_date_calendar(callback: CallbackQuery, state: FSMContext):
    """Redraw the due-date calendar for the requested month. Past days are inert
    (min_date is this chat's today), so a pick can only ever be today-or-later."""
    parsed = parse_month(callback.data.split(":", 1)[1])
    if parsed is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    year, month = parsed
    today = await ts.today_for_chat_id(callback.message.chat.id)
    kb = build_calendar(
        year, month,
        pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=today, min_date=today,
        cancel_cb="hw_list_active",
    )
    await safe_edit_text(
        callback.message,
        "🗓 Выбери дату сдачи (или введи вручную в формате <code>ДД.ММ</code>):",
        reply_markup=kb, parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cal:noop")
async def calendar_noop(callback: CallbackQuery):
    """Inert calendar cells (labels, out-of-range days) — acknowledge silently."""
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
        due_date = next_occurrence(month, day, await ts.today_for_chat_id(message.chat.id))
    except ValueError:
        await message.answer(
            "Неверный формат даты! Укажи дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    subject = data["hw_subject"]
    description = data["hw_description"]

    await _create_homework(
        message, message.chat.id, subject, due_date, description,
        files=data.get("hw_files"),
    )
    await state.clear()

    safe_sub = html_escape(subject)
    await message.answer(
        f"✅ Домашнее задание по предмету <b>{safe_sub}</b> на {due_date.strftime('%d.%m')} сохранено!",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    hw_text, kb = await format_homework_list(message.chat.id, is_archive=False)
    await message.answer(hw_text, reply_markup=kb, parse_mode="HTML")


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
