import datetime
import html
import re
from typing import List, Optional, Sequence, Tuple

from aiogram.exceptions import TelegramBadRequest

# --- Telegram / input limits ---------------------------------------------

# Hard limit imposed by the Telegram Bot API on a single text message.
MAX_MESSAGE_LENGTH = 4096
# Conservative limit used when rendering a single interactive page so that
# Markdown escaping / emoji never push us over MAX_MESSAGE_LENGTH.
SAFE_PAGE_LIMIT = 3500
# Max homework items shown on one interactive page (usability cap; the real
# guard is SAFE_PAGE_LIMIT which may produce fewer items per page).
HW_MAX_PER_PAGE = 8

# Reasonable input caps so a single subject/description can never blow past
# Telegram's message limit (escaping can nearly double the rendered length).
MAX_SUBJECT_LEN = 100
MAX_DESCRIPTION_LEN = 1000

# Field caps for extra activities (clubs / tutors / sections).
MAX_TITLE_LEN = 100
MAX_LOCATION_LEN = 100
MAX_NOTE_LEN = 300

# --- Homework attachments --------------------------------------------------

# How many files one homework entry may carry. A deliberate, low cap: a card
# with its attachments must stay a glance, and every attachment is one extra
# Telegram send when the card is opened.
MAX_ATTACHMENTS_PER_HOMEWORK = 5

# Telegram itself refuses to *send back* a document larger than 50 MB, so
# accepting one would store a reference we can never deliver. Photos are always
# well under this. We never download the file — this is a metadata check only.
MAX_ATTACHMENT_SIZE_BYTES = 50 * 1024 * 1024

# Captions are shown under the file; Telegram's own limit is 1024 characters.
MAX_ATTACHMENT_CAPTION_LEN = 500

# Display cap for the (untrusted) original file name.
MAX_FILE_NAME_LEN = 100

# The only two attachment kinds this bot understands. An "image sent as a file"
# arrives as a document and is stored as one — that is intentional: it keeps its
# original quality, exactly as the sender chose.
ATTACHMENT_TYPES = ("photo", "document")


def safe_file_name(raw: Optional[str]) -> Optional[str]:
    """
    Sanitise a client-supplied file name down to harmless display metadata.

    The name is **never** trusted: it is never used as a filesystem path and
    nothing is written to disk. We still strip anything that could mislead or
    break rendering:

      * any directory component (``../../etc/passwd`` → ``passwd``), so the
        value can't read like a path even in logs;
      * control characters and right-to-left overrides (the classic
        ``report\\u202Egnp.exe`` trick that displays as ``reportexe.png``);
      * surrounding whitespace, collapsed inner whitespace, and a length cap.

    Returns ``None`` when nothing usable is left, in which case the UI falls
    back to a generic label.
    """
    if not raw:
        return None
    name = str(raw)
    # Drop directory components from either separator style.
    name = name.replace("\\", "/").split("/")[-1]
    # Strip control chars and bidi overrides that can disguise the extension.
    name = "".join(
        ch for ch in name
        if ch.isprintable() and ch not in "‪‫‬‭‮⁦⁧⁨⁩"
    )
    name = " ".join(name.split())
    if name in ("", ".", ".."):
        return None
    return name[:MAX_FILE_NAME_LEN]


def format_file_size(size: Optional[int]) -> str:
    """Human-readable size for an attachment line ("1.2 МБ"), "" if unknown."""
    if not size or size < 0:
        return ""
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"


def html_escape(text: str) -> str:
    """
    Escapes ``&``, ``<`` and ``>`` in user-provided text so it can be safely
    interpolated into a message sent with ``parse_mode="HTML"`` — Telegram's
    HTML parse mode does not tolerate unescaped entities the way legacy
    Markdown silently ignored most punctuation.
    """
    return html.escape(text, quote=False)


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """
    Splits ``text`` into chunks each no longer than ``limit`` characters.

    The split prefers paragraph boundaries (``\\n\\n``), then single line
    breaks, and only falls back to a hard character cut when a single line is
    itself longer than the limit. This keeps whole homework entries / schedule
    rows together and avoids slicing through Markdown markup where possible.
    """
    if text is None:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text

    while len(remaining) > limit:
        window = remaining[:limit]

        # Prefer a paragraph boundary in the second half of the window.
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            nl = window.rfind("\n")
            split_at = nl if nl >= limit // 2 else limit

        chunk = remaining[:split_at].rstrip("\n")
        # Guard against an empty chunk (e.g. leading newlines): force progress.
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_long_message(message, text: str, **kwargs):
    """
    Sends ``text`` via ``message.answer`` splitting it into several messages
    when it exceeds Telegram's length limit. ``reply_markup`` (if provided) is
    attached only to the final chunk so the keyboard sits under the whole list.
    """
    chunks = split_message(text)
    reply_markup = kwargs.pop("reply_markup", None)
    for idx, chunk in enumerate(chunks):
        is_last = idx == len(chunks) - 1
        await message.answer(
            chunk,
            reply_markup=reply_markup if is_last else None,
            **kwargs,
        )


async def safe_edit_text(message, text, **kwargs):
    """
    Edits a message's text while ignoring Telegram's "message is not modified"
    error, which is raised when the new content is identical to the current one
    (e.g. tapping a refresh button or re-selecting the already active day).
    """
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


# --- Lesson time parsing / validation -------------------------------------

_TIME_INTERVAL_RE = re.compile(
    r"^\s*([0-1]?\d|2[0-3]):([0-5]\d)\s*-\s*([0-1]?\d|2[0-3]):([0-5]\d)\s*$"
)

_SINGLE_TIME_RE = re.compile(r"^\s*([0-1]?\d|2[0-3]):([0-5]\d)\s*$")


def parse_time_interval(raw: Optional[str]) -> Tuple[str, str]:
    """
    Parses a ``"HH:MM - HH:MM"`` interval and returns a normalized
    ``(start, end)`` tuple in ``HH:MM`` form (zero-padded).

    Raises ``ValueError`` with a user-friendly Russian message when:
      * the value is empty / has the wrong format;
      * the start time is not strictly earlier than the end time
        (this also rejects reversed and zero-length intervals).
    """
    if not raw:
        raise ValueError("Пустое значение времени.")

    match = _TIME_INTERVAL_RE.match(raw)
    if not match:
        raise ValueError(
            "Неверный формат! Используй `ЧЧ:ММ - ЧЧ:ММ`, например `08:30 - 09:15`."
        )

    start_h, start_m, end_h, end_m = (int(match.group(i)) for i in range(1, 5))
    start = f"{start_h:02d}:{start_m:02d}"
    end = f"{end_h:02d}:{end_m:02d}"

    if (start_h, start_m) >= (end_h, end_m):
        raise ValueError(
            f"Начало урока ({start}) должно быть строго раньше конца ({end})."
        )

    return start, end


def parse_activity_time(raw: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    Parses a start time for an extra activity, accepting either a single time
    ``"18:00"`` (returns ``("18:00", None)``) or an interval ``"18:00 - 19:00"``
    (returns ``("18:00", "19:00")``), both normalized to zero-padded ``HH:MM``.

    Raises ``ValueError`` with a user-friendly Russian message on a bad format
    or when the interval's start is not strictly earlier than its end.
    """
    if not raw:
        raise ValueError("Пустое значение времени.")

    match = _SINGLE_TIME_RE.match(raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return f"{hour:02d}:{minute:02d}", None

    if _TIME_INTERVAL_RE.match(raw):
        # Reuse the interval parser (also enforces start < end).
        start, end = parse_time_interval(raw)
        return start, end

    raise ValueError(
        "Неверный формат времени! Используй `ЧЧ:ММ` или `ЧЧ:ММ - ЧЧ:ММ`, "
        "например `18:00` или `18:00 - 19:00`."
    )


def validate_against_previous(start: str, prev_end: Optional[str]) -> None:
    """
    Ensures a new lesson does not start before the previous lesson ends.

    Times are normalized ``HH:MM`` strings, so a lexicographic comparison is
    equivalent to a chronological one. Raises ``ValueError`` on overlap.
    """
    if prev_end is not None and start < prev_end:
        raise ValueError(
            f"Урок не может начинаться ({start}) раньше конца предыдущего ({prev_end})."
        )


def next_occurrence(month: int, day: int, today: datetime.date, max_years_ahead: int = 8) -> datetime.date:
    """
    Finds the next future-or-today occurrence of a ``(month, day)`` date,
    starting from ``today.year`` and advancing one year at a time.

    Handles February 29th correctly: if ``today.year`` (or ``today.year + 1``,
    etc.) is not a leap year, ``datetime.date(year, 2, 29)`` raises
    ``ValueError`` — that year is simply skipped rather than treated as an
    invalid input, so the next actual leap year is returned instead of
    raising a spurious "invalid date" error.
    """
    for offset in range(max_years_ahead + 1):
        year = today.year + offset
        try:
            candidate = datetime.date(year, month, day)
        except ValueError:
            continue  # e.g. Feb 29 in a non-leap year - try the next year
        if candidate >= today:
            return candidate
    raise ValueError(f"Не удалось найти подходящую дату для {day:02d}.{month:02d}.")


def safe_parse_int(parts: Sequence[str], idx: int) -> Optional[int]:
    """
    Safely extracts ``int(parts[idx])`` from split callback_data, returning
    ``None`` instead of raising on a missing index or a non-numeric segment
    (stale/tampered/malformed callback_data).
    """
    if idx >= len(parts):
        return None
    try:
        return int(parts[idx])
    except (TypeError, ValueError):
        return None


def safe_callback_ints(data: str, *idxs: int, sep: str = ":") -> Optional[Tuple[int, ...]]:
    """
    Splits ``data`` on ``sep`` and extracts integers at ``idxs``, or returns
    ``None`` if the data is too short or any requested segment isn't a valid
    integer. Convenience wrapper around :func:`safe_parse_int` for the common
    "parse several int fields from one callback_data string" case.
    """
    parts = data.split(sep)
    values = []
    for idx in idxs:
        value = safe_parse_int(parts, idx)
        if value is None:
            return None
        values.append(value)
    return tuple(values)
