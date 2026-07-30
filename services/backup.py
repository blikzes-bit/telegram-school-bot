"""
Export, backup and safe restore of one chat's data.

Three export shapes, all built from data this bot already stores:

  * **JSON backup** (:func:`build_backup`) — the complete, restorable picture of
    one chat: settings and timezone, lesson call times, the weekly template
    (including the A/B week templates and the anchor Monday), per-date overrides,
    extra activities, homework with its attachment *metadata*. Carries a
    ``schema_version`` so a future format change can be detected instead of
    guessed at.
  * **CSV** (:func:`schedule_csv`) — the weekly template as a spreadsheet.
  * **ICS** (:func:`calendar_ics`) — lessons, extra activities and homework
    deadlines as a calendar feed, hand-rolled (RFC 5545 is a text format; adding
    a dependency for a few dozen lines would be worse).

What is **never** exported: ``BOT_TOKEN`` and anything else from the
environment, Telegram *file contents* (only ``file_id`` references and
metadata), FSM state, delivery bookkeeping (the reminder outbox, ``is_blocked``,
``last_*_reminder_date``). The audit journal is exported only on explicit
request, as its own file.

Import is the dangerous direction, so it is deliberately paranoid:

  * the uploaded bytes are size-capped *before* parsing and the row counts are
    capped after (:data:`MAX_BACKUP_BYTES`, :data:`MAX_ROWS`);
  * :func:`parse_backup` validates ``schema_version``, structure, types, ranges
    and string lengths, and returns a *normalised* payload built only from
    values it recognised — nothing from the file is ever handed to the DB
    unexamined, there is no SQL, no file path and no expression in the format,
    and nothing in it is executed;
  * the ``chat_id`` inside the file is read for the report only and then thrown
    away: the target is always the current Telegram chat
    (see :func:`target_chat_note`);
  * the write itself is one transaction that rolls back completely on any error
    (see :func:`database.db.import_chat_data`).
"""
import csv
import datetime
import io
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import services.timeservice as ts
from database.db import (
    get_all_audit_logs, get_all_day_overrides, get_all_homework_attachments,
    get_all_lesson_overrides, get_all_schedule, get_chat, get_extra_activities,
    get_homework, get_lesson_slots, import_chat_data,
)
from database.models import Chat
from services.effective_schedule import compute_effective_day, resolve_week_type
from services.permissions import HW_EDIT_POLICIES
from utils import (
    MAX_ATTACHMENT_CAPTION_LEN, MAX_ATTACHMENTS_PER_HOMEWORK, MAX_DESCRIPTION_LEN,
    MAX_FILE_NAME_LEN, MAX_LOCATION_LEN, MAX_NOTE_LEN, MAX_SUBJECT_LEN,
    MAX_TITLE_LEN, safe_file_name,
)

APP_NAME = "telegram_school_bot"

# Bump only for a *breaking* format change, and then teach :func:`parse_backup`
# how to read the old one (or reject it with a clear message, as now).
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = (1,)

IMPORT_MODE_MERGE = "merge"
IMPORT_MODE_REPLACE = "replace"
IMPORT_MODES = (IMPORT_MODE_MERGE, IMPORT_MODE_REPLACE)

MODE_LABELS = {
    IMPORT_MODE_MERGE: "➕ Дополнить",
    IMPORT_MODE_REPLACE: "♻️ Заменить всё",
}

# --- Limits -----------------------------------------------------------------

# A real class chat's backup is a few dozen KB. The cap is checked against the
# document's declared size *before* downloading and against the actual bytes
# after, so an oversized file is never parsed.
MAX_BACKUP_BYTES = 2 * 1024 * 1024

# Per-collection row caps. Generous for real use, small enough that a crafted
# file can't turn one import into a million inserts.
MAX_ROWS = {
    "lesson_slots": 30,
    "schedule": 3000,
    "day_overrides": 2000,
    "lesson_overrides": 5000,
    "extra_activities": 500,
    "homework": 3000,
    "audit_log": 5000,
}
MAX_TOTAL_ROWS = 12000

# How many journal rows one audit export may contain.
MAX_AUDIT_EXPORT_ROWS = MAX_ROWS["audit_log"]

# ICS horizon: how far ahead lessons/activities are materialised.
ICS_DAYS_AHEAD = 60

# Timestamps (ISO-8601 UTC strings) are copied through verbatim; cap the length
# so a hand-edited file can't smuggle a novel into a VARCHAR.
MAX_TIMESTAMP_LEN = 64
MAX_ACTOR_NAME_LEN = 64
MAX_FILE_ID_LEN = 256

ATTACHMENT_PORTABILITY_WARNING = (
    "Вложения сохранены только как ссылки Telegram (file_id) и метаданные — "
    "сами файлы бот не скачивает. Telegram не гарантирует, что такая ссылка "
    "останется рабочей вечно и что её сможет открыть другой бот: после "
    "восстановления вложение может потребоваться приложить заново."
)


class BackupError(ValueError):
    """
    A validation failure with a message meant for the user.

    Raised only with text that is safe to show: what is wrong with the file,
    never an internal traceback or a raw fragment of the file's contents.
    """


# --- Export -----------------------------------------------------------------

def _iso(value: Optional[datetime.date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _chat_settings(chat: Chat) -> Dict[str, Any]:
    """The chat's exportable configuration (no identity, no delivery state)."""
    return {
        "hw_reminder_time": chat.hw_reminder_time,
        "schedule_reminder_time": chat.schedule_reminder_time,
        "hw_reminder_enabled": bool(chat.hw_reminder_enabled),
        "schedule_reminder_enabled": bool(chat.schedule_reminder_enabled),
        "hw_duetoday_enabled": bool(chat.hw_duetoday_enabled),
        "hw_duetoday_time": chat.hw_duetoday_time,
        "changes_reminder_enabled": bool(chat.changes_reminder_enabled),
        "extra_reminder_enabled": bool(chat.extra_reminder_enabled),
        "quiet_start": chat.quiet_start,
        "quiet_end": chat.quiet_end,
        "hw_edit_policy": chat.hw_edit_policy,
        "timezone": chat.timezone,
        "week_mode": bool(chat.week_mode),
        "week_anchor_monday": _iso(chat.week_anchor_monday),
    }


def _authorship(row: Any) -> Dict[str, Any]:
    """
    Who created/last changed a row. Only the Telegram id and display name the
    bot already stores — the same two fields shown in the UI, nothing more.
    """
    return {
        "created_by_user_id": getattr(row, "created_by_user_id", None),
        "created_by_name": getattr(row, "created_by_name", None),
        "updated_by_user_id": getattr(row, "updated_by_user_id", None),
        "updated_by_name": getattr(row, "updated_by_name", None),
        "created_at": getattr(row, "created_at", None),
        "updated_at": getattr(row, "updated_at", None),
    }


async def build_backup(chat_id: int) -> Dict[str, Any]:
    """
    The full JSON-serialisable backup of one chat. Every read is scoped to
    ``chat_id``; nothing outside this chat is touched.
    """
    chat = await get_chat(chat_id)
    slots = await get_lesson_slots(chat_id)
    schedule = await get_all_schedule(chat_id)
    day_overrides = await get_all_day_overrides(chat_id)
    lesson_overrides = await get_all_lesson_overrides(chat_id)
    extra = await get_extra_activities(chat_id)
    homework = await get_homework(chat_id)
    attachments = await get_all_homework_attachments(chat_id)

    return {
        "schema_version": SCHEMA_VERSION,
        "app": APP_NAME,
        "kind": "chat_backup",
        "exported_at": ts.now_iso_utc(),
        # Informational only: an import always targets the current chat.
        "source_chat_id": chat_id,
        "notes": [ATTACHMENT_PORTABILITY_WARNING],
        "chat": _chat_settings(chat) if chat is not None else {},
        "lesson_slots": [
            {
                "lesson_number": slot.lesson_number,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
            }
            for slot in slots
        ],
        "schedule": [
            {
                "week_type": row.week_type,
                "day_of_week": row.day_of_week,
                "lesson_number": row.lesson_number,
                "subject_name": row.subject_name,
            }
            for row in schedule
        ],
        "day_overrides": [
            {
                "date": _iso(row.date),
                "day_type": row.day_type,
                "note": row.note,
                **_authorship(row),
            }
            for row in day_overrides
        ],
        "lesson_overrides": [
            {
                "date": _iso(row.date),
                "lesson_number": row.lesson_number,
                "action": row.action,
                "subject_name": row.subject_name,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "note": row.note,
                **_authorship(row),
            }
            for row in lesson_overrides
        ],
        "extra_activities": [
            {
                "title": row.title,
                "kind": row.kind,
                "day_of_week": row.day_of_week,
                "activity_date": _iso(row.activity_date),
                "start_time": row.start_time,
                "end_time": row.end_time,
                "location": row.location,
                "note": row.note,
                "reminder_enabled": bool(row.reminder_enabled),
                "reminder_minutes": row.reminder_minutes,
                **_authorship(row),
            }
            for row in extra
        ],
        "homework": [
            {
                "subject_name": hw.subject_name,
                "due_date": _iso(hw.due_date),
                "description": hw.description,
                "is_completed": bool(hw.is_completed),
                **_authorship(hw),
                "attachments": [
                    {
                        "file_id": att.file_id,
                        "file_unique_id": att.file_unique_id,
                        "file_type": att.file_type,
                        "file_name": att.file_name,
                        "file_size": att.file_size,
                        "caption": att.caption,
                        "created_at": att.created_at,
                        "created_by_user_id": att.created_by_user_id,
                        "created_by_name": att.created_by_name,
                    }
                    for att in attachments.get(hw.id, [])
                ],
            }
            for hw in homework
        ],
    }


async def build_audit_export(chat_id: int) -> Dict[str, Any]:
    """
    The chat's journal as its own file (newest first, capped). Kept out of the
    main backup: history is append-only and is never restored by an import, so
    bundling it would only bloat the backup people actually restore from.
    """
    entries = await get_all_audit_logs(chat_id, MAX_AUDIT_EXPORT_ROWS)
    return {
        "schema_version": SCHEMA_VERSION,
        "app": APP_NAME,
        "kind": "audit_log",
        "exported_at": ts.now_iso_utc(),
        "source_chat_id": chat_id,
        "truncated": len(entries) >= MAX_AUDIT_EXPORT_ROWS,
        "audit_log": [
            {
                "created_at": entry.created_at,
                "actor_user_id": entry.actor_user_id,
                "actor_name": entry.actor_name,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "action": entry.action,
                "summary": entry.summary,
            }
            for entry in entries
        ],
    }


def dump_json(payload: Dict[str, Any]) -> bytes:
    """UTF-8 JSON bytes, indented and with real Cyrillic (not \\uXXXX escapes)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


def backup_file_name(chat_id: int, today: datetime.date, kind: str = "backup") -> str:
    """A stable, filesystem-safe name for the document we send back."""
    return f"school_bot_{kind}_{abs(chat_id)}_{today.isoformat()}.json"


# --- CSV --------------------------------------------------------------------

CSV_HEADER = ("Неделя", "День", "Урок", "Начало", "Конец", "Предмет")

CSV_DAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
CSV_WEEK_LABELS = {"all": "Обычная", "A": "A (нечётная)", "B": "B (чётная)"}


async def schedule_csv(chat_id: int) -> bytes:
    """
    The weekly template as CSV: one row per lesson, sorted by week/day/lesson.

    Written with ``;`` and a UTF-8 BOM because that is what a Russian-locale
    Excel opens correctly without an import wizard.
    """
    slots = {slot.lesson_number: slot for slot in await get_lesson_slots(chat_id)}
    rows = await get_all_schedule(chat_id)

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        slot = slots.get(row.lesson_number)
        writer.writerow([
            CSV_WEEK_LABELS.get(row.week_type, row.week_type),
            CSV_DAYS[row.day_of_week] if 0 <= row.day_of_week < 7 else row.day_of_week,
            row.lesson_number,
            slot.start_time if slot else "",
            slot.end_time if slot else "",
            row.subject_name,
        ])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


# --- ICS --------------------------------------------------------------------

def _ics_escape(text: str) -> str:
    """Escape a text value per RFC 5545 §3.3.11."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _ics_fold(line: str) -> str:
    """
    Fold one content line to 75 octets, continuing with a leading space.

    Folding counts *octets*, not characters, so the split is computed on the
    UTF-8 encoding and never lands inside a multi-byte character (which would
    corrupt Cyrillic subject names).
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces: List[str] = []
    limit = 73  # leave room for CRLF; continuation lines start with a space
    while raw:
        chunk, raw = raw[:limit], raw[limit:]
        # Don't cut a multi-byte character in half: give trailing continuation
        # bytes back to the next chunk.
        while raw and (raw[0] & 0xC0) == 0x80:
            chunk, raw = chunk[:-1], chunk[-1:] + raw
        pieces.append(chunk.decode("utf-8"))
    return "\r\n ".join(pieces)


def _ics_datetime(moment: datetime.datetime) -> str:
    """A UTC timestamp in ICS basic format (always ends in Z)."""
    return moment.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_event(
    uid: str, stamp: str, summary: str, *,
    start: Optional[str] = None, end: Optional[str] = None,
    date_start: Optional[str] = None, date_end: Optional[str] = None,
    description: Optional[str] = None, location: Optional[str] = None,
) -> List[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"SUMMARY:{_ics_escape(summary)}",
    ]
    if date_start is not None:
        lines.append(f"DTSTART;VALUE=DATE:{date_start}")
        lines.append(f"DTEND;VALUE=DATE:{date_end}")
    else:
        lines.append(f"DTSTART:{start}")
        lines.append(f"DTEND:{end}")
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_escape(description)}")
    lines.append("END:VEVENT")
    return lines


async def calendar_ics(chat_id: int, today: datetime.date, days_ahead: int = ICS_DAYS_AHEAD) -> bytes:
    """
    Lessons, extra activities and homework deadlines for the next
    ``days_ahead`` days as an ICS calendar.

    Times are emitted in UTC (converted from the chat's own timezone through
    :mod:`services.timeservice`, so DST is handled there), which every calendar
    app understands without a VTIMEZONE block. Homework deadlines are all-day
    events. All inputs are fetched once and the effective day is computed in
    memory, so the horizon costs a fixed handful of queries.
    """
    chat = await get_chat(chat_id)
    tz = ts.chat_tz(chat)
    slots = await get_lesson_slots(chat_id)
    schedule = await get_all_schedule(chat_id)
    day_overrides = {row.date: row for row in await get_all_day_overrides(chat_id)}
    lesson_overrides: Dict[datetime.date, List[Any]] = {}
    for row in await get_all_lesson_overrides(chat_id):
        lesson_overrides.setdefault(row.date, []).append(row)
    extra = await get_extra_activities(chat_id)
    homework = await get_homework(chat_id, is_completed=False)

    week_mode = bool(getattr(chat, "week_mode", False))
    anchor = getattr(chat, "week_anchor_monday", None)

    stamp = _ics_datetime(datetime.datetime.now(datetime.timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{APP_NAME}//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape('Школа')}",
    ]

    def _time_range(date: datetime.date, start_hhmm: str, end_hhmm: Optional[str]) -> Optional[Tuple[str, str]]:
        start_time = ts.parse_hhmm(start_hhmm)
        if start_time is None:
            return None
        start_dt = ts.combine(tz, date, start_time)
        end_time = ts.parse_hhmm(end_hhmm) if end_hhmm else None
        if end_time is None:
            end_dt = start_dt + datetime.timedelta(minutes=45)
        else:
            end_dt = ts.combine(tz, date, end_time)
            if end_dt <= start_dt:  # crosses midnight
                end_dt += datetime.timedelta(days=1)
        return _ics_datetime(start_dt), _ics_datetime(end_dt)

    for offset in range(days_ahead):
        date = today + datetime.timedelta(days=offset)
        week_type = resolve_week_type(week_mode, anchor, date)
        day_rows = [
            row for row in schedule
            if row.day_of_week == date.weekday() and row.week_type == week_type
        ]
        effective = compute_effective_day(
            date, slots, day_rows, day_overrides.get(date), lesson_overrides.get(date, [])
        )
        for lesson in effective.lessons:
            if lesson.cancelled or not lesson.subject_name or not lesson.start_time:
                continue
            span = _time_range(date, lesson.start_time, lesson.end_time)
            if span is None:
                continue
            lines.extend(_ics_event(
                f"lesson-{abs(chat_id)}-{date.isoformat()}-{lesson.lesson_number}@{APP_NAME}",
                stamp, lesson.subject_name,
                start=span[0], end=span[1],
                description=lesson.note,
            ))

        if effective.is_special:
            continue  # a free/holiday day cancels extra activities too
        for activity in extra:
            if activity.kind == "weekly":
                if activity.day_of_week != date.weekday():
                    continue
            elif activity.activity_date != date:
                continue
            span = _time_range(date, activity.start_time, activity.end_time)
            if span is None:
                continue
            lines.extend(_ics_event(
                f"extra-{abs(chat_id)}-{date.isoformat()}-{activity.id}@{APP_NAME}",
                stamp, f"🎯 {activity.title}",
                start=span[0], end=span[1],
                location=activity.location, description=activity.note,
            ))

    for hw in homework:
        lines.extend(_ics_event(
            f"hw-{abs(chat_id)}-{hw.id}@{APP_NAME}",
            stamp, f"📝 {hw.subject_name}",
            date_start=hw.due_date.strftime("%Y%m%d"),
            date_end=(hw.due_date + datetime.timedelta(days=1)).strftime("%Y%m%d"),
            description=hw.description,
        ))

    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines).encode("utf-8") + b"\r\n"


# --- Import: validation -----------------------------------------------------

def _fail(message: str) -> None:
    raise BackupError(message)


def _as_dict(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"Раздел «{where}» должен быть объектом JSON.")
    return value


def _as_list(container: Dict[str, Any], key: str) -> List[Any]:
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(f"Раздел «{key}» должен быть списком.")
    limit = MAX_ROWS.get(key)
    if limit is not None and len(value) > limit:
        _fail(f"Слишком много записей в разделе «{key}»: {len(value)} (максимум {limit}).")
    return value


def _str_field(
    row: Dict[str, Any], key: str, where: str, *,
    required: bool = True, max_len: int = 200, allow_empty: bool = False,
) -> Optional[str]:
    value = row.get(key)
    if value is None:
        if required:
            _fail(f"В разделе «{where}» отсутствует обязательное поле «{key}».")
        return None
    if not isinstance(value, str):
        _fail(f"Поле «{key}» в разделе «{where}» должно быть строкой.")
    value = value.strip()
    if not value and not allow_empty:
        if required:
            _fail(f"Поле «{key}» в разделе «{where}» не может быть пустым.")
        return None
    if len(value) > max_len:
        _fail(f"Поле «{key}» в разделе «{where}» длиннее допустимых {max_len} символов.")
    return value


def _int_field(
    row: Dict[str, Any], key: str, where: str, *,
    required: bool = True, minimum: Optional[int] = None, maximum: Optional[int] = None,
) -> Optional[int]:
    value = row.get(key)
    if value is None:
        if required:
            _fail(f"В разделе «{where}» отсутствует обязательное поле «{key}».")
        return None
    # bool is a subclass of int — a JSON ``true`` is not a number here.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"Поле «{key}» в разделе «{where}» должно быть целым числом.")
    if minimum is not None and value < minimum:
        _fail(f"Поле «{key}» в разделе «{where}» меньше допустимого минимума {minimum}.")
    if maximum is not None and value > maximum:
        _fail(f"Поле «{key}» в разделе «{where}» больше допустимого максимума {maximum}.")
    return value


def _bool_field(row: Dict[str, Any], key: str, where: str, default: bool = False) -> bool:
    value = row.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(f"Поле «{key}» в разделе «{where}» должно быть true или false.")
    return value


def _date_field(
    row: Dict[str, Any], key: str, where: str, required: bool = True
) -> Optional[datetime.date]:
    raw = row.get(key)
    if raw is None:
        if required:
            _fail(f"В разделе «{where}» отсутствует обязательная дата «{key}».")
        return None
    if not isinstance(raw, str):
        _fail(f"Дата «{key}» в разделе «{where}» должна быть строкой ГГГГ-ММ-ДД.")
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        _fail(f"Дата «{key}» в разделе «{where}» не в формате ГГГГ-ММ-ДД.")
        return None


def _hhmm_field(
    row: Dict[str, Any], key: str, where: str, required: bool = True
) -> Optional[str]:
    raw = _str_field(row, key, where, required=required, max_len=5)
    if raw is None:
        return None
    parsed = ts.parse_hhmm(raw)
    if parsed is None:
        _fail(f"Время «{key}» в разделе «{where}» должно быть в формате ЧЧ:ММ.")
        return None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _enum_field(
    row: Dict[str, Any], key: str, where: str, allowed: Sequence[str], required: bool = True
) -> Optional[str]:
    value = _str_field(row, key, where, required=required, max_len=32)
    if value is None:
        return None
    if value not in allowed:
        _fail(f"Недопустимое значение поля «{key}» в разделе «{where}»: {value!r}.")
    return value


def _authorship_fields(row: Dict[str, Any], where: str) -> Dict[str, Any]:
    """Copy the (optional) authorship columns, validated and length-capped."""
    return {
        "created_by_user_id": _int_field(row, "created_by_user_id", where, required=False),
        "created_by_name": _str_field(
            row, "created_by_name", where, required=False, max_len=MAX_ACTOR_NAME_LEN
        ),
        "updated_by_user_id": _int_field(row, "updated_by_user_id", where, required=False),
        "updated_by_name": _str_field(
            row, "updated_by_name", where, required=False, max_len=MAX_ACTOR_NAME_LEN
        ),
        "created_at": _str_field(
            row, "created_at", where, required=False, max_len=MAX_TIMESTAMP_LEN
        ),
        "updated_at": _str_field(
            row, "updated_at", where, required=False, max_len=MAX_TIMESTAMP_LEN
        ),
    }


def _validate_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    The chat block, filtered down to the columns an import may set. Unknown keys
    are ignored (forward compatibility), known ones are strictly validated.
    """
    where = "chat"
    settings = _as_dict(raw, where)
    out: Dict[str, Any] = {}

    for key in ("hw_reminder_time", "schedule_reminder_time", "hw_duetoday_time"):
        if key in settings:
            out[key] = _hhmm_field(settings, key, where)
    for key in (
        "hw_reminder_enabled", "schedule_reminder_enabled", "hw_duetoday_enabled",
        "changes_reminder_enabled", "extra_reminder_enabled", "week_mode",
    ):
        if key in settings:
            out[key] = _bool_field(settings, key, where)

    # Quiet hours: both must be present and different, or the pair is cleared.
    if "quiet_start" in settings or "quiet_end" in settings:
        start = _hhmm_field(settings, "quiet_start", where, required=False)
        end = _hhmm_field(settings, "quiet_end", where, required=False)
        if start is None or end is None or start == end:
            start = end = None
        out["quiet_start"] = start
        out["quiet_end"] = end

    if "hw_edit_policy" in settings:
        out["hw_edit_policy"] = _enum_field(
            settings, "hw_edit_policy", where, HW_EDIT_POLICIES
        )

    if "timezone" in settings:
        raw_tz = _str_field(settings, "timezone", where, max_len=64)
        canonical = ts.normalize_timezone(raw_tz)
        if canonical is None:
            _fail(f"Неизвестный часовой пояс в резервной копии: {raw_tz!r}.")
        out["timezone"] = canonical

    if "week_anchor_monday" in settings:
        anchor = _date_field(settings, "week_anchor_monday", where, required=False)
        if anchor is not None and anchor.weekday() != 0:
            _fail("Поле «week_anchor_monday» должно указывать на понедельник.")
        out["week_anchor_monday"] = anchor

    return out


def parse_backup(raw: bytes) -> Dict[str, Any]:
    """
    Parse and validate uploaded backup bytes into a normalised payload.

    Raises :class:`BackupError` with a user-facing message on anything wrong:
    size, encoding, JSON syntax, schema version, structure, types, ranges,
    lengths or row counts. The returned dict contains *only* values this
    function recognised and converted itself — the original objects are never
    passed on.
    """
    if not isinstance(raw, (bytes, bytearray)):
        _fail("Не удалось прочитать файл резервной копии.")
    if len(raw) > MAX_BACKUP_BYTES:
        _fail(
            f"Файл слишком большой: {len(raw) // 1024} КБ "
            f"(максимум {MAX_BACKUP_BYTES // 1024} КБ)."
        )
    if not raw.strip():
        _fail("Файл пустой.")

    try:
        text = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError:
        _fail("Файл должен быть текстовым в кодировке UTF-8.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _fail(f"Это не корректный JSON (строка {e.lineno}, символ {e.colno}).")
    if not isinstance(data, dict):
        _fail("Корень файла должен быть объектом JSON.")

    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        _fail("В файле нет числового поля «schema_version» — это не резервная копия бота.")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        _fail(
            f"Версия формата {version} не поддерживается "
            f"(эта версия бота читает {', '.join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)}). "
            "Сделайте новую резервную копию."
        )
    if data.get("kind") == "audit_log":
        _fail(
            "Это экспорт истории изменений, а не резервная копия. "
            "История не восстанавливается — она только выгружается."
        )

    payload: Dict[str, Any] = {
        "schema_version": version,
        "source_chat_id": data.get("source_chat_id") if isinstance(
            data.get("source_chat_id"), int
        ) else None,
        "exported_at": data.get("exported_at") if isinstance(
            data.get("exported_at"), str
        ) else None,
        "chat": _validate_settings(data.get("chat") or {}),
    }

    total_rows = 0

    # --- lesson slots ---
    slots: List[Dict[str, Any]] = []
    seen_lessons = set()
    for row in _as_list(data, "lesson_slots"):
        where = "lesson_slots"
        row = _as_dict(row, where)
        number = _int_field(row, "lesson_number", where, minimum=1, maximum=MAX_ROWS["lesson_slots"])
        if number in seen_lessons:
            _fail(f"Урок №{number} встречается в «lesson_slots» дважды.")
        seen_lessons.add(number)
        start = _hhmm_field(row, "start_time", where)
        end = _hhmm_field(row, "end_time", where)
        if start is not None and end is not None and start >= end:
            _fail(f"Урок №{number}: начало ({start}) должно быть раньше конца ({end}).")
        slots.append({"lesson_number": number, "start_time": start, "end_time": end})
    payload["lesson_slots"] = sorted(slots, key=lambda item: item["lesson_number"])
    total_rows += len(slots)

    # --- weekly template ---
    schedule: List[Dict[str, Any]] = []
    seen_cells = set()
    for row in _as_list(data, "schedule"):
        where = "schedule"
        row = _as_dict(row, where)
        week_type = _enum_field(row, "week_type", where, ("all", "A", "B"), required=False) or "all"
        day = _int_field(row, "day_of_week", where, minimum=0, maximum=6)
        number = _int_field(row, "lesson_number", where, minimum=1, maximum=MAX_ROWS["lesson_slots"])
        key = (week_type, day, number)
        if key in seen_cells:
            _fail(f"Запись расписания {week_type}/{day}/{number} встречается дважды.")
        seen_cells.add(key)
        schedule.append({
            "week_type": week_type,
            "day_of_week": day,
            "lesson_number": number,
            "subject_name": _str_field(row, "subject_name", where, max_len=MAX_SUBJECT_LEN),
        })
    payload["schedule"] = schedule
    total_rows += len(schedule)

    # --- whole-day overrides ---
    day_overrides: List[Dict[str, Any]] = []
    seen_dates = set()
    for row in _as_list(data, "day_overrides"):
        where = "day_overrides"
        row = _as_dict(row, where)
        date = _date_field(row, "date", where)
        if date in seen_dates:
            _fail(f"Дата {date} встречается в «day_overrides» дважды.")
        seen_dates.add(date)
        day_overrides.append({
            "date": date,
            "day_type": _enum_field(
                row, "day_type", where, ("free", "holiday", "vacation", "remote")
            ),
            "note": _str_field(row, "note", where, required=False, max_len=MAX_NOTE_LEN),
            **_authorship_fields(row, where),
        })
    payload["day_overrides"] = day_overrides
    total_rows += len(day_overrides)

    # --- per-lesson overrides ---
    lesson_overrides: List[Dict[str, Any]] = []
    seen_lesson_ov = set()
    for row in _as_list(data, "lesson_overrides"):
        where = "lesson_overrides"
        row = _as_dict(row, where)
        date = _date_field(row, "date", where)
        number = _int_field(row, "lesson_number", where, minimum=1, maximum=MAX_ROWS["lesson_slots"])
        key = (date, number)
        if key in seen_lesson_ov:
            _fail(f"Изменение урока №{number} на {date} встречается дважды.")
        seen_lesson_ov.add(key)
        start = _hhmm_field(row, "start_time", where, required=False)
        end = _hhmm_field(row, "end_time", where, required=False)
        lesson_overrides.append({
            "date": date,
            "lesson_number": number,
            "action": _enum_field(row, "action", where, ("cancel", "set")),
            "subject_name": _str_field(
                row, "subject_name", where, required=False, max_len=MAX_SUBJECT_LEN
            ),
            "start_time": start,
            "end_time": end,
            "note": _str_field(row, "note", where, required=False, max_len=MAX_NOTE_LEN),
            **_authorship_fields(row, where),
        })
    payload["lesson_overrides"] = lesson_overrides
    total_rows += len(lesson_overrides)

    # --- extra activities ---
    extra: List[Dict[str, Any]] = []
    for row in _as_list(data, "extra_activities"):
        where = "extra_activities"
        row = _as_dict(row, where)
        kind = _enum_field(row, "kind", where, ("weekly", "once"))
        day = _int_field(row, "day_of_week", where, required=False, minimum=0, maximum=6)
        date = _date_field(row, "activity_date", where, required=False)
        # The DB has a CHECK constraint for exactly this; rejecting it here keeps
        # the failure a readable message instead of an IntegrityError mid-import.
        if kind == "weekly" and (day is None or date is not None):
            _fail("Еженедельное доп. занятие должно задавать «day_of_week» и не задавать «activity_date».")
        if kind == "once" and (date is None or day is not None):
            _fail("Разовое доп. занятие должно задавать «activity_date» и не задавать «day_of_week».")
        start = _hhmm_field(row, "start_time", where)
        end = _hhmm_field(row, "end_time", where, required=False)
        extra.append({
            "title": _str_field(row, "title", where, max_len=MAX_TITLE_LEN),
            "kind": kind,
            "day_of_week": day,
            "activity_date": date,
            "start_time": start,
            "end_time": end,
            "location": _str_field(
                row, "location", where, required=False, max_len=MAX_LOCATION_LEN
            ),
            "note": _str_field(row, "note", where, required=False, max_len=MAX_NOTE_LEN),
            "reminder_enabled": _bool_field(row, "reminder_enabled", where),
            "reminder_minutes": _int_field(
                row, "reminder_minutes", where, required=False, minimum=0, maximum=10080
            ) or 0,
            **_authorship_fields(row, where),
        })
    payload["extra_activities"] = extra
    total_rows += len(extra)

    # --- homework + attachment metadata ---
    homework: List[Dict[str, Any]] = []
    attachment_rows = 0
    for row in _as_list(data, "homework"):
        where = "homework"
        row = _as_dict(row, where)
        raw_attachments = row.get("attachments") or []
        if not isinstance(raw_attachments, list):
            _fail("Поле «attachments» у ДЗ должно быть списком.")
        if len(raw_attachments) > MAX_ATTACHMENTS_PER_HOMEWORK:
            _fail(
                f"У одного ДЗ не может быть больше {MAX_ATTACHMENTS_PER_HOMEWORK} вложений "
                f"(в файле: {len(raw_attachments)})."
            )
        attachments = []
        for att in raw_attachments:
            aw = "attachments"
            att = _as_dict(att, aw)
            attachments.append({
                # Only Telegram's own references — never file contents, never a
                # path. The name is untrusted display metadata and is sanitised.
                "file_id": _str_field(att, "file_id", aw, max_len=MAX_FILE_ID_LEN),
                "file_unique_id": _str_field(att, "file_unique_id", aw, max_len=MAX_FILE_ID_LEN),
                "file_type": _enum_field(att, "file_type", aw, ("photo", "document")),
                "file_name": safe_file_name(
                    _str_field(att, "file_name", aw, required=False, max_len=MAX_FILE_NAME_LEN)
                ),
                "file_size": _int_field(att, "file_size", aw, required=False, minimum=0),
                "caption": _str_field(
                    att, "caption", aw, required=False, max_len=MAX_ATTACHMENT_CAPTION_LEN
                ),
                "created_at": _str_field(
                    att, "created_at", aw, required=False, max_len=MAX_TIMESTAMP_LEN
                ),
                "created_by_user_id": _int_field(att, "created_by_user_id", aw, required=False),
                "created_by_name": _str_field(
                    att, "created_by_name", aw, required=False, max_len=MAX_ACTOR_NAME_LEN
                ),
            })
        attachment_rows += len(attachments)
        homework.append({
            "subject_name": _str_field(row, "subject_name", where, max_len=MAX_SUBJECT_LEN),
            "due_date": _date_field(row, "due_date", where),
            "description": _str_field(row, "description", where, max_len=MAX_DESCRIPTION_LEN),
            "is_completed": _bool_field(row, "is_completed", where),
            **_authorship_fields(row, where),
            "attachments": attachments,
        })
    payload["homework"] = homework
    total_rows += len(homework) + attachment_rows

    # The journal is export-only: count it for the report, never import it.
    audit_rows = data.get("audit_log")
    payload["audit_skipped"] = len(audit_rows) if isinstance(audit_rows, list) else 0

    if total_rows > MAX_TOTAL_ROWS:
        _fail(f"В файле слишком много записей: {total_rows} (максимум {MAX_TOTAL_ROWS}).")
    payload["total_rows"] = total_rows
    return payload


# --- Import: preview + apply ------------------------------------------------

async def preview_import(chat_id: int, payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """
    A dry run: what *would* happen, without writing anything.

    Reads the chat's current keys and applies exactly the matching rules
    :func:`database.db.import_chat_data` uses, so the numbers the user confirms
    are the numbers they get.
    """
    if mode not in IMPORT_MODES:
        raise BackupError("Неизвестный режим импорта.")

    report: Dict[str, Any] = {"mode": mode, "created": 0, "updated": 0, "skipped": 0, "deleted": 0}
    lines: List[Tuple[str, int, int, int]] = []  # (label, created, updated, skipped)

    slots = {slot.lesson_number for slot in await get_lesson_slots(chat_id)}
    cells = {
        (row.week_type, row.day_of_week, row.lesson_number)
        for row in await get_all_schedule(chat_id)
    }
    day_dates = {row.date for row in await get_all_day_overrides(chat_id)}
    lesson_keys = {
        (row.date, row.lesson_number) for row in await get_all_lesson_overrides(chat_id)
    }
    extra_keys = {
        (row.title, row.kind, row.day_of_week, row.activity_date, row.start_time)
        for row in await get_extra_activities(chat_id)
    }
    hw_rows = await get_homework(chat_id)
    hw_keys = {(row.subject_name, row.due_date, row.description) for row in hw_rows}

    replace = mode == IMPORT_MODE_REPLACE
    if replace:
        report["deleted"] = (
            len(slots) + len(cells) + len(day_dates) + len(lesson_keys)
            + len(extra_keys) + len(hw_rows)
        )
        slots, cells, day_dates, lesson_keys, extra_keys, hw_keys = set(), set(), set(), set(), set(), set()

    def count_keyed(label: str, items, key_fn, existing) -> None:
        created = updated = 0
        seen = set(existing)
        for item in items:
            key = key_fn(item)
            if key in seen:
                updated += 1
            else:
                seen.add(key)
                created += 1
        if created or updated:
            lines.append((label, created, updated, 0))
        report["created"] += created
        report["updated"] += updated

    count_keyed("🕒 Время звонков", payload.get("lesson_slots", []),
                lambda item: item["lesson_number"], slots)
    count_keyed("📅 Расписание", payload.get("schedule", []),
                lambda item: (item["week_type"], item["day_of_week"], item["lesson_number"]), cells)
    count_keyed("🗓 Тип дня по датам", payload.get("day_overrides", []),
                lambda item: item["date"], day_dates)
    count_keyed("🗓 Изменения уроков", payload.get("lesson_overrides", []),
                lambda item: (item["date"], item["lesson_number"]), lesson_keys)

    def count_content(label: str, items, key_fn, existing) -> None:
        created = skipped = 0
        seen = set(existing)
        for item in items:
            key = key_fn(item)
            if key in seen:
                skipped += 1
            else:
                seen.add(key)
                created += 1
        if created or skipped:
            lines.append((label, created, 0, skipped))
        report["created"] += created
        report["skipped"] += skipped

    count_content("🎯 Доп. занятия", payload.get("extra_activities", []),
                  lambda item: (item["title"], item["kind"], item["day_of_week"],
                                item["activity_date"], item["start_time"]), extra_keys)
    count_content("📝 Домашние задания", payload.get("homework", []),
                  lambda item: (item["subject_name"], item["due_date"], item["description"]),
                  hw_keys)

    attachments = sum(len(item.get("attachments") or []) for item in payload.get("homework", []))
    report["attachments"] = attachments
    report["settings"] = bool(payload.get("chat"))
    report["audit_skipped"] = payload.get("audit_skipped", 0)
    report["lines"] = lines
    return report


async def apply_import(
    chat_id: int, payload: Dict[str, Any], mode: str, chat_type: str = "private"
) -> Dict[str, int]:
    """
    Write the backup into ``chat_id`` in one all-or-nothing transaction.

    The mode is re-validated here even though the UI only offers two buttons: a
    stale or hand-crafted callback must not be able to pick a third behaviour.
    """
    if mode not in IMPORT_MODES:
        raise BackupError("Неизвестный режим импорта.")
    return await import_chat_data(chat_id, payload, mode, chat_type=chat_type)


def target_chat_note(payload: Dict[str, Any], chat_id: int) -> Optional[str]:
    """
    A heads-up when the file came from a different chat. Not an error: importing
    another chat's backup is a legitimate way to move a class to a new group —
    the point is that the *target* is always this chat, whatever the file says.
    """
    source = payload.get("source_chat_id")
    if source is None or source == chat_id:
        return None
    return (
        "ℹ️ Файл выгружен из другого чата. Данные всё равно будут записаны "
        "только в этот чат — chat_id из файла игнорируется."
    )
