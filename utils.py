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
