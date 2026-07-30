"""
"📜 История" — the chat's audit journal.

Admin-only in a group/supergroup (private chats have a single user, so there is
nothing to restrict), paginated, and filterable by the kind of change. Every
entry point calls ``require_admin`` **before** reading anything: this is a
server-side check, not a hidden button — the section is reachable only from the
settings screen, but a stale or hand-crafted callback must be rejected here too.

All queries are scoped to the current ``chat_id``, so one chat can never read
another chat's history. Entries survive the records they describe: a deleted
homework disappears from the list but its "удалил(а)" line stays here.

Rows are pruned by the nightly scheduler housekeeping once they are older than
the configured retention window (see config.AUDIT_RETENTION_DAYS).
"""
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from database.db import count_audit_logs, get_audit_logs, get_chat
from keyboards.inline import AUDIT_FILTERS, get_audit_keyboard
from middleware.access import require_admin
import services.audit as audit
import services.timeservice as ts
from utils import html_escape, safe_edit_text

router = Router()

STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой раздел заново."

# Entries per page — small enough that a page never approaches Telegram's
# message limit even with long names and summaries.
PAGE_SIZE = 8

_FILTER_KEYS = {key for key, _ in AUDIT_FILTERS}


def _entity_type(filter_key: str) -> Optional[str]:
    """``None`` for the unfiltered "all" tab, otherwise the entity type."""
    return None if filter_key == "all" else filter_key


def _parse_target(data: str) -> Optional[Tuple[str, int]]:
    """
    ``(filter_key, page)`` from ``au_page:<filter>:<page>``, or None when the
    callback is malformed / carries an unknown filter (stale or tampered).
    """
    parts = data.split(":")
    if len(parts) < 3 or parts[1] not in _FILTER_KEYS:
        return None
    try:
        page = int(parts[2])
    except ValueError:
        return None
    return parts[1], max(0, page)


def format_audit_entry(entry, tz=None) -> str:
    """
    One journal line: when, who, what kind of record, what happened, plus the
    short stored summary. All stored text (actor name, summary) is HTML-escaped
    here — a user who names themselves ``<b>`` cannot break the message.
    """
    when = audit.format_ts(entry.created_at, tz)
    who = html_escape(audit.actor_label(entry.actor_user_id, entry.actor_name))
    entity = audit.ENTITY_LABELS.get(entry.entity_type, entry.entity_type)
    action = audit.ACTION_LABELS.get(entry.action, entry.action)
    line = f"🕒 <i>{when}</i> — <b>{who}</b> {action}: {entity}"
    if entry.summary:
        line += f"\n   <i>{html_escape(entry.summary)}</i>"
    return line


async def render_history(chat_id: int, filter_key: str, page: int) -> Tuple[str, InlineKeyboardMarkup]:
    """The history screen's text + keyboard for one chat/filter/page."""
    entity_type = _entity_type(filter_key)
    total = await count_audit_logs(chat_id, entity_type)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages - 1)

    chat = await get_chat(chat_id)
    tz = ts.chat_tz(chat)
    entries = await get_audit_logs(
        chat_id, entity_type, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )

    label = next((lbl for key, lbl in AUDIT_FILTERS if key == filter_key), "🗂 Все")
    text = f"📜 <b>История изменений</b> — {html_escape(label)}"
    if total_pages > 1:
        text += f"  (стр. {page + 1}/{total_pages})"
    text += "\n\n"

    if not entries:
        text += (
            "Пока ничего не записано.\n\n"
            "<i>Здесь появятся записи о добавлении, изменении и удалении ДЗ, "
            "доп. занятий и расписания.</i>"
        )
    else:
        text += "\n\n".join(format_audit_entry(entry, tz) for entry in entries)

    return text, get_audit_keyboard(filter_key, page, total_pages)


async def _show(callback: CallbackQuery, filter_key: str, page: int):
    text, kb = await render_history(callback.message.chat.id, filter_key, page)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "au_open")
async def open_history(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    await _show(callback, "all", 0)
    await callback.answer()


@router.callback_query(F.data.startswith("au_page:"))
async def page_history(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    target = _parse_target(callback.data)
    if target is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await _show(callback, target[0], target[1])
    await callback.answer()


@router.callback_query(F.data == "au_noop")
async def noop(callback: CallbackQuery):
    await callback.answer()
