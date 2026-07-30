"""
A compact, dependency-free inline calendar for picking a single date.

The widget is deliberately stateless: every button carries everything the
handler needs in its ``callback_data``, and both encodings stay well inside
Telegram's 64-byte limit (a full pick is ``"<prefix>:2026-09-01"`` — ~20 bytes).

Reuse contract
--------------
``build_calendar`` renders one month. The caller supplies two short callback
prefixes:

  * ``pick_prefix`` — a tapped, allowed day sends ``"<pick_prefix>:<ISO date>"``.
    Flows that already accept an ISO date (e.g. the homework quick-pick buttons)
    can reuse their existing handler unchanged.
  * ``nav_prefix`` — the ‹/› arrows send ``"<nav_prefix>:<YYYY-MM>"``; the caller
    re-renders that month. Keeping the target month (not a full date) in the
    arrows is what keeps navigation callbacks tiny.

Out-of-range days (before ``min_date`` or after ``max_date``) and the static
labels are rendered as inert ``noop`` cells so a stale keyboard can never submit
an invalid date. ``parse_month`` returns ``None`` on any malformed token, so a
tampered or outdated arrow degrades to a handled "stale button" rather than a
crash.
"""
import calendar
import datetime
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

NOOP = "cal:noop"

_WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def month_token(year: int, month: int) -> str:
    """``(2026, 9) -> "2026-09"`` — the compact month key used in nav callbacks."""
    return f"{year:04d}-{month:02d}"


def parse_month(token: Optional[str]) -> Optional[tuple]:
    """Parse a ``"YYYY-MM"`` token into ``(year, month)`` or ``None`` if it is
    missing / malformed / not a real month. Never raises."""
    if not token:
        return None
    parts = token.split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (1 <= month <= 12) or not (1 <= year <= 9999):
        return None
    return year, month


def shift_month(year: int, month: int, delta: int) -> tuple:
    """Move ``delta`` months from ``(year, month)``, rolling the year over."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def build_calendar(
    year: int,
    month: int,
    *,
    pick_prefix: str,
    nav_prefix: str,
    today: datetime.date,
    min_date: Optional[datetime.date] = None,
    max_date: Optional[datetime.date] = None,
    cancel_cb: Optional[str] = None,
    cancel_text: str = "❌ Отмена",
) -> InlineKeyboardMarkup:
    """Render one month as an inline keyboard.

    The current day is marked, days outside ``[min_date, max_date]`` are inert,
    and the ‹/› arrows to a month that is entirely out of range are hidden so the
    user can't page into a dead zone.
    """
    rows: list = []

    # --- Header: ‹  Month Year  › -------------------------------------------
    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, +1)

    # An arrow is only useful if the neighbouring month can contain a pickable
    # day. Compare against the first/last day of that month.
    prev_last = datetime.date(prev_y, prev_m, calendar.monthrange(prev_y, prev_m)[1])
    next_first = datetime.date(next_y, next_m, 1)
    show_prev = min_date is None or prev_last >= min_date
    show_next = max_date is None or next_first <= max_date

    left = InlineKeyboardButton(
        text="‹", callback_data=f"{nav_prefix}:{month_token(prev_y, prev_m)}"
    ) if show_prev else InlineKeyboardButton(text=" ", callback_data=NOOP)
    right = InlineKeyboardButton(
        text="›", callback_data=f"{nav_prefix}:{month_token(next_y, next_m)}"
    ) if show_next else InlineKeyboardButton(text=" ", callback_data=NOOP)
    rows.append([
        left,
        InlineKeyboardButton(text=f"{_MONTHS_RU[month - 1]} {year}", callback_data=NOOP),
        right,
    ])

    # --- Weekday labels ------------------------------------------------------
    rows.append([InlineKeyboardButton(text=lbl, callback_data=NOOP) for lbl in _WEEKDAY_LABELS])

    # --- Day grid ------------------------------------------------------------
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            if day.month != month:
                row.append(InlineKeyboardButton(text=" ", callback_data=NOOP))
                continue
            out_of_range = (min_date is not None and day < min_date) or (
                max_date is not None and day > max_date
            )
            if out_of_range:
                # Inert cell: dim marker, never submits a disallowed date.
                row.append(InlineKeyboardButton(text="·", callback_data=NOOP))
                continue
            label = f"[{day.day}]" if day == today else str(day.day)
            row.append(InlineKeyboardButton(
                text=label, callback_data=f"{pick_prefix}:{day.isoformat()}"
            ))
        rows.append(row)

    if cancel_cb is not None:
        rows.append([InlineKeyboardButton(text=cancel_text, callback_data=cancel_cb)])

    return InlineKeyboardMarkup(inline_keyboard=rows)
