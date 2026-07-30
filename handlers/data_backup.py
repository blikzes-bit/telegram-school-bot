"""
"💾 Данные и резервная копия" — export, backup and restore.

The whole section is admin-only in a group/supergroup (a private chat has a
single user, so there is nothing to restrict): a backup is every homework entry,
every activity and the chat's configuration in one file, and an import can
replace all of it. Every entry point calls ``require_admin`` **before** reading
or writing anything — a hidden button protects nothing, so a stale or
hand-crafted callback is rejected here too.

The import path is the sensitive one and is deliberately narrow:

  * only a **document** is accepted, and its declared size is checked before it
    is downloaded (see :data:`services.backup.MAX_BACKUP_BYTES`);
  * the bytes are parsed and validated by :func:`services.backup.parse_backup`,
    which returns a payload built only from values it recognised — the file
    cannot carry SQL, a file path, or anything that gets executed;
  * the target is always **this** chat: the ``chat_id`` inside the file is shown
    in the report and then discarded;
  * a preview report is shown first, ``replace`` needs two confirmations, and
    the write itself is one transaction that rolls back on any error.

The uploaded file is not kept: only its Telegram ``file_id`` is held in FSM
state between the preview and the confirmation, and the document is re-downloaded
and re-validated at the moment of applying it.
"""
import io
import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import services.audit as audit
import services.backup as backup
import services.timeservice as ts
from keyboards.inline import (
    get_backup_import_confirm_keyboard, get_backup_menu_keyboard,
    get_backup_mode_keyboard, get_cancel_keyboard,
)
from middleware.access import require_admin
from utils import html_escape, safe_edit_text

logger = logging.getLogger(__name__)

router = Router()

STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой раздел заново."
DOWNLOAD_FAILED_TEXT = (
    "⚠️ Не удалось получить файл от Telegram. Попробуй отправить его ещё раз."
)
NEED_DOCUMENT_TEXT = (
    "🤔 Мне нужен <b>файл</b> резервной копии (.json) — пришли его как документ. "
    "Текст, фото и сжатые изображения не подойдут."
)

class BackupStates(StatesGroup):
    waiting_for_file = State()


def _menu_text() -> str:
    return (
        "💾 <b>Данные и резервная копия</b>\n\n"
        "📦 <b>Резервная копия (JSON)</b> — всё, что нужно для восстановления: настройки "
        "и часовой пояс, время звонков, расписание (включая недели A/B), изменения по "
        "датам, доп. занятия и домашние задания.\n"
        "📊 <b>Расписание в CSV</b> — таблица для Excel или Google Таблиц.\n"
        f"📅 <b>Календарь (ICS)</b> — уроки, доп. занятия и сроки сдачи ДЗ на "
        f"{backup.ICS_DAYS_AHEAD} дней вперёд, для любого календаря.\n"
        "📜 <b>История изменений</b> выгружается отдельным файлом и при восстановлении "
        "не используется.\n\n"
        f"⚠️ {html_escape(backup.ATTACHMENT_PORTABILITY_WARNING)}\n\n"
        "🔒 В файлы не попадают токен бота, содержимое вложений и служебные данные "
        "(состояние диалогов, очередь напоминаний)."
    )


async def _show_menu(callback: CallbackQuery):
    await safe_edit_text(
        callback.message, _menu_text(),
        reply_markup=get_backup_menu_keyboard(), parse_mode="HTML",
    )


@router.callback_query(F.data == "bk_menu")
async def open_backup_menu(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    await _show_menu(callback)
    await callback.answer()


# --- Export ------------------------------------------------------------------

async def _send_file(callback: CallbackQuery, data: bytes, filename: str, caption: str):
    await callback.message.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=caption, parse_mode="HTML",
    )


@router.callback_query(F.data == "bk_json")
async def export_backup(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    today = await ts.today_for_chat_id(chat_id)
    payload = await backup.build_backup(chat_id)
    await _send_file(
        callback, backup.dump_json(payload),
        backup.backup_file_name(chat_id, today, "backup"),
        "📦 <b>Резервная копия чата</b>\n"
        f"Формат: <code>schema_version {payload['schema_version']}</code>\n"
        f"📅 Расписание: {len(payload['schedule'])} · "
        f"📝 ДЗ: {len(payload['homework'])} · "
        f"🎯 Доп. занятия: {len(payload['extra_activities'])}\n\n"
        f"⚠️ {html_escape(backup.ATTACHMENT_PORTABILITY_WARNING)}",
    )
    await callback.answer()


@router.callback_query(F.data == "bk_audit")
async def export_audit(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    today = await ts.today_for_chat_id(chat_id)
    payload = await backup.build_audit_export(chat_id)
    note = (
        f"\n\n<i>Показаны последние {backup.MAX_AUDIT_EXPORT_ROWS} записей — "
        "файл обрезан.</i>" if payload["truncated"] else ""
    )
    await _send_file(
        callback, backup.dump_json(payload),
        backup.backup_file_name(chat_id, today, "history"),
        f"📜 <b>История изменений</b>\nЗаписей: {len(payload['audit_log'])}."
        " Этот файл только для чтения — импортом он не восстанавливается." + note,
    )
    await callback.answer()


@router.callback_query(F.data == "bk_csv")
async def export_csv(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    today = await ts.today_for_chat_id(chat_id)
    data = await backup.schedule_csv(chat_id)
    await _send_file(
        callback, data, f"school_bot_schedule_{abs(chat_id)}_{today.isoformat()}.csv",
        "📊 <b>Расписание в CSV</b>\nРазделитель — точка с запятой, кодировка UTF-8 "
        "(открывается в Excel и Google Таблицах без настройки).",
    )
    await callback.answer()


@router.callback_query(F.data == "bk_ics")
async def export_ics(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat_id = callback.message.chat.id
    today = await ts.today_for_chat_id(chat_id)
    data = await backup.calendar_ics(chat_id, today)
    await _send_file(
        callback, data, f"school_bot_calendar_{abs(chat_id)}_{today.isoformat()}.ics",
        f"📅 <b>Календарь</b> на {backup.ICS_DAYS_AHEAD} дней вперёд: уроки "
        "(с учётом изменений по датам), доп. занятия и сроки сдачи ДЗ. "
        "Время указано в UTC — календарь сам покажет его в твоём поясе.",
    )
    await callback.answer()


# --- Import ------------------------------------------------------------------

IMPORT_PROMPT = (
    "⬆️ <b>Восстановление из резервной копии</b>\n\n"
    "Пришли файл <code>.json</code>, который бот выгрузил ранее (как документ, "
    f"не более {backup.MAX_BACKUP_BYTES // 1024} КБ).\n\n"
    "Дальше я покажу, что именно изменится, и спрошу подтверждение. "
    "Ничего не будет записано, пока ты не подтвердишь.\n\n"
    "ℹ️ Данные всегда записываются <b>только в этот чат</b>, какой бы chat_id "
    "ни был указан в файле."
)


@router.callback_query(F.data == "bk_import")
async def start_import(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(BackupStates.waiting_for_file)
    await safe_edit_text(
        callback.message, IMPORT_PROMPT,
        reply_markup=get_cancel_keyboard(callback_data="bk_menu"), parse_mode="HTML",
    )
    await callback.answer()


async def _download(bot, file_id: str) -> bytes:
    """
    Fetch a document's bytes, reading at most one byte more than the limit so an
    oversized file is rejected by the validator instead of buffered whole.
    """
    buffer = io.BytesIO()
    result = await bot.download(file_id, destination=buffer)
    target = result if result is not None else buffer
    try:
        target.seek(0)
    except Exception:
        pass
    return target.read(backup.MAX_BACKUP_BYTES + 1)


async def _load_payload(bot, file_id: str):
    """``(payload, error_text)`` — exactly one of the two is set."""
    try:
        raw = await _download(bot, file_id)
    except Exception as e:
        logger.warning("Backup download failed: %s", type(e).__name__)
        return None, DOWNLOAD_FAILED_TEXT
    try:
        return backup.parse_backup(raw), None
    except backup.BackupError as e:
        return None, f"⚠️ {html_escape(str(e))}"


def _format_report(payload, report, chat_id: int) -> str:
    mode = report["mode"]
    title = (
        "♻️ <b>Заменить все данные</b>" if mode == backup.IMPORT_MODE_REPLACE
        else "➕ <b>Дополнить текущие данные</b>"
    )
    lines = [title, ""]
    if mode == backup.IMPORT_MODE_REPLACE:
        lines.append(
            f"🗑 Будет удалено записей: <b>{report['deleted']}</b> "
            "(расписание, звонки, изменения по датам, доп. занятия и все ДЗ вместе с вложениями)."
        )
        lines.append("")
    lines.append("<b>Что будет записано:</b>")
    if report["lines"]:
        for label, created, updated, skipped in report["lines"]:
            parts = []
            if created:
                parts.append(f"новых {created}")
            if updated:
                parts.append(f"обновлено {updated}")
            if skipped:
                parts.append(f"пропущено {skipped}")
            lines.append(f"• {label}: {', '.join(parts)}")
    else:
        lines.append("• <i>нет записей — файл пустой</i>")

    lines.append("")
    lines.append(
        f"Итого: создать <b>{report['created']}</b>, "
        f"обновить <b>{report['updated']}</b>, пропустить <b>{report['skipped']}</b>."
    )
    if report["settings"]:
        lines.append("⚙️ Настройки чата и часовой пояс будут взяты из файла.")
    if report["attachments"]:
        lines.append(
            f"📎 Вложений в файле: {report['attachments']}. "
            "Восстанавливаются только ссылки Telegram — сами файлы могут больше не открыться."
        )
    if report["audit_skipped"]:
        lines.append(
            f"📜 Записей истории в файле: {report['audit_skipped']} — они не восстанавливаются."
        )
    note = backup.target_chat_note(payload, chat_id)
    if note:
        lines.append(note)
    return "\n".join(lines)


@router.message(BackupStates.waiting_for_file, F.document)
async def receive_backup_file(message: Message, state: FSMContext):
    if not await require_admin(message, message.bot):
        return
    document = message.document
    # Telegram tells us the size up front: refuse before downloading anything.
    if document.file_size and document.file_size > backup.MAX_BACKUP_BYTES:
        await message.answer(
            f"⚠️ Файл слишком большой: {document.file_size // 1024} КБ "
            f"(максимум {backup.MAX_BACKUP_BYTES // 1024} КБ).",
        )
        return

    payload, error = await _load_payload(message.bot, document.file_id)
    if error is not None:
        await message.answer(
            f"{error}\n\nПришли другой файл или нажми «Отмена».",
            reply_markup=get_cancel_keyboard(callback_data="bk_menu"),
            parse_mode="HTML",
        )
        return

    # Only the Telegram reference is kept in FSM state — never the file body.
    await state.update_data(bk_file_id=document.file_id)
    await message.answer(
        "✅ Файл прочитан и проверен.\n\n"
        f"📦 Записей в файле: <b>{payload['total_rows']}</b>\n"
        f"📅 Расписание: {len(payload['schedule'])} · "
        f"📝 ДЗ: {len(payload['homework'])} · "
        f"🎯 Доп. занятия: {len(payload['extra_activities'])}\n\n"
        "Как восстанавливать?\n"
        "• <b>Дополнить</b> — текущие данные остаются, из файла добавляется недостающее.\n"
        "• <b>Заменить всё</b> — сначала удаляются все текущие расписание, ДЗ и доп. "
        "занятия этого чата, затем записывается файл.",
        reply_markup=get_backup_mode_keyboard(), parse_mode="HTML",
    )


@router.message(BackupStates.waiting_for_file)
async def receive_wrong_content(message: Message):
    """Anything that isn't a document while we're waiting for the backup file."""
    await message.answer(
        NEED_DOCUMENT_TEXT,
        reply_markup=get_cancel_keyboard(callback_data="bk_menu"), parse_mode="HTML",
    )


async def _payload_from_state(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_id = data.get("bk_file_id")
    if not file_id:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return None
    payload, error = await _load_payload(callback.bot, file_id)
    if error is not None:
        await safe_edit_text(
            callback.message, f"{error}\n\nНачни импорт заново.",
            reply_markup=get_backup_menu_keyboard(), parse_mode="HTML",
        )
        await callback.answer()
        return None
    return payload


def _mode_from(data: str) -> str:
    mode = data.split(":", 1)[1] if ":" in data else ""
    return mode if mode in backup.IMPORT_MODES else ""


@router.callback_query(BackupStates.waiting_for_file, F.data.startswith("bk_mode:"))
async def preview_mode(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    mode = _mode_from(callback.data)
    if not mode:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    payload = await _payload_from_state(callback, state)
    if payload is None:
        return
    chat_id = callback.message.chat.id
    report = await backup.preview_import(chat_id, payload, mode)
    # `replace` gets a second confirmation step; `merge` is applied from here.
    await safe_edit_text(
        callback.message, _format_report(payload, report, chat_id),
        reply_markup=get_backup_import_confirm_keyboard(mode, first_step=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(BackupStates.waiting_for_file, F.data == "bk_replace_ask2")
async def confirm_replace_again(callback: CallbackQuery, state: FSMContext):
    """Second confirmation for ``replace`` — the destructive one."""
    if not await require_admin(callback, callback.bot):
        return
    payload = await _payload_from_state(callback, state)
    if payload is None:
        return
    report = await backup.preview_import(
        callback.message.chat.id, payload, backup.IMPORT_MODE_REPLACE
    )
    await safe_edit_text(
        callback.message,
        "⚠️ <b>Последнее предупреждение</b>\n\n"
        f"Будут <b>безвозвратно удалены</b> все текущие данные этого чата "
        f"({report['deleted']} записей): расписание, время звонков, изменения по датам, "
        "доп. занятия и все домашние задания вместе с вложениями.\n\n"
        f"Вместо них будет записано <b>{report['created']}</b> записей из файла.\n\n"
        "История изменений (📜) сохранится.",
        reply_markup=get_backup_import_confirm_keyboard(
            backup.IMPORT_MODE_REPLACE, first_step=False
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(BackupStates.waiting_for_file, F.data.startswith("bk_apply:"))
async def apply_backup(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    mode = _mode_from(callback.data)
    if not mode:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    payload = await _payload_from_state(callback, state)
    if payload is None:
        return

    chat = callback.message.chat
    try:
        counts = await backup.apply_import(chat.id, payload, mode, chat_type=chat.type)
    except Exception as e:
        # One transaction: nothing was written. Never show the raw exception.
        logger.exception("Backup import failed for chat %s", chat.id, exc_info=e)
        await state.clear()
        await safe_edit_text(
            callback.message,
            "⚠️ <b>Импорт не выполнен</b>\n\n"
            "Произошла ошибка, и все изменения отменены — данные чата остались "
            "такими же, как до импорта. Попробуй ещё раз или пришли другой файл.",
            reply_markup=get_backup_menu_keyboard(), parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.clear()
    await audit.record_event(
        callback, chat.id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
        summary=audit.summarize(
            f"импорт резервной копии ({backup.MODE_LABELS[mode]})",
            f"создано {sum(v for k, v in counts.items() if k.endswith('_created'))}",
            f"удалено {counts.get('deleted', 0)}" if mode == backup.IMPORT_MODE_REPLACE else None,
        ),
    )

    created = sum(value for key, value in counts.items() if key.endswith("_created"))
    updated = sum(value for key, value in counts.items() if key.endswith("_updated"))
    skipped = sum(value for key, value in counts.items() if key.endswith("_skipped"))
    lines = [
        "✅ <b>Восстановление завершено</b>",
        "",
        f"➕ Создано записей: <b>{created}</b>",
        f"✏️ Обновлено: <b>{updated}</b>",
        f"⏭ Пропущено (уже были): <b>{skipped}</b>",
    ]
    if counts.get("deleted"):
        lines.append(f"🗑 Удалено перед записью: <b>{counts['deleted']}</b>")
    if counts.get("attachments_created"):
        lines.append(
            f"📎 Вложений восстановлено: {counts['attachments_created']} "
            "(ссылки Telegram; часть может больше не открываться)"
        )
    if payload.get("audit_skipped"):
        lines.append(f"📜 Записей истории пропущено: {payload['audit_skipped']}")
    lines.append("")
    lines.append("Проверь «📚 Сегодня» и «📅 Расписание» — данные уже обновлены.")

    await safe_edit_text(
        callback.message, "\n".join(lines),
        reply_markup=get_backup_menu_keyboard(), parse_mode="HTML",
    )
    await callback.answer("Готово")


# A stale confirm button after the state was cleared: tell the user instead of
# silently doing nothing.
@router.callback_query(
    StateFilter(None), F.data.startswith("bk_apply:")
)
@router.callback_query(StateFilter(None), F.data == "bk_replace_ask2")
@router.callback_query(StateFilter(None), F.data.startswith("bk_mode:"))
async def stale_import_button(callback: CallbackQuery):
    await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
