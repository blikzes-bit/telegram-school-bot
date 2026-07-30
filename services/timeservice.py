"""
The single time service. Every user-visible date and time goes through here.

Each chat has its own IANA timezone (``Chat.timezone``), so there is no longer
any notion of "the app's timezone" for user-facing dates: "Сегодня", homework due
dates, even/odd week resolution, date overrides, extra activities and all
reminders resolve *per chat*. ``config.TIMEZONE`` survives only as the default
for a brand-new chat and as the fallback when a stored zone name turns out to be
unknown — a retired or hand-edited value must never be able to stop the
scheduler for every other chat.

``now_iso_utc`` is the exception on purpose: outbox and audit bookkeeping is
timestamped in UTC so it stays comparable across instances and timezones.

DST is handled explicitly (see :func:`localize`):

  * **a local time that doesn't exist** (spring forward, e.g. 02:30 on the night
    the clock jumps 02:00 → 03:00) is shifted to the corresponding moment just
    after the gap, so a reminder set for it still fires that day;
  * **a local time that happens twice** (autumn fall-back) resolves to the
    *first* occurrence, so a reminder is never late.

Neither case can produce a duplicate reminder: every reminder is deduplicated by
calendar date — via the per-category ``last_*_reminder_date`` stamps and the
outbox job keyed ``(chat, kind, date)`` — so a wall clock that passes the same
minute twice still yields exactly one send.

The quiet-hours helpers are pure functions (no clock access) so they are
trivially unit-testable.
"""
import datetime
from typing import List, Optional, Tuple

import pytz

from config import TIMEZONE

# The process-wide default: what a brand-new chat gets, and the fallback for a
# stored zone name pytz no longer knows.
DEFAULT_TIMEZONE = TIMEZONE
_GLOBAL_TZ = pytz.timezone(TIMEZONE)

# A short, friendly picker of common zones. The user can always type any IANA
# name instead, so this list is convenience, not a restriction.
POPULAR_TIMEZONES: List[Tuple[str, str]] = [
    ("Europe/Kyiv", "🇺🇦 Киев"),
    ("Europe/Warsaw", "🇵🇱 Варшава"),
    ("Europe/Berlin", "🇩🇪 Берлин"),
    ("Europe/Chisinau", "🇲🇩 Кишинёв"),
    ("Europe/Vilnius", "🇱🇹 Вильнюс"),
    ("Europe/Riga", "🇱🇻 Рига"),
    ("Europe/Lisbon", "🇵🇹 Лиссабон"),
    ("Europe/London", "🇬🇧 Лондон"),
    ("Asia/Tbilisi", "🇬🇪 Тбилиси"),
    ("Asia/Yerevan", "🇦🇲 Ереван"),
    ("America/New_York", "🇺🇸 Нью-Йорк"),
    ("UTC", "🌍 UTC"),
]


# --- Timezone resolution ----------------------------------------------------

def is_valid_timezone(name: Optional[str]) -> bool:
    """Whether ``name`` is an IANA zone this build of pytz knows."""
    if not name or not isinstance(name, str):
        return False
    return name.strip() in pytz.all_timezones_set


def normalize_timezone(raw: Optional[str]) -> Optional[str]:
    """
    Canonical IANA name for user input, or ``None`` when it isn't a valid zone.

    Accepts a case-insensitive match ("europe/kyiv") and turns spaces into
    underscores ("America/New York"), because those are the two mistakes people
    actually make when typing a zone by hand. Deliberately does *not* guess
    beyond that: silently picking the wrong continent would be worse than asking
    again.
    """
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip().replace(" ", "_")
    if candidate in pytz.all_timezones_set:
        return candidate
    lowered = candidate.lower()
    for name in pytz.all_timezones:
        if name.lower() == lowered:
            return name
    return None


def tz_from_name(name: str) -> pytz.BaseTzInfo:
    """
    A tzinfo for an already-validated zone name, falling back to the default on
    anything unknown. Keeps callers off ``pytz`` directly so the time service
    stays the only place that knows how zones are built.
    """
    try:
        return pytz.timezone(name)
    except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
        return _GLOBAL_TZ


def chat_tz(chat=None) -> pytz.BaseTzInfo:
    """
    The timezone of a chat.

    Falls back to the global default when there is no chat, no stored value, or
    the stored name is not a zone pytz knows — a chat with a broken value keeps
    working (on the default) instead of raising through the scheduler sweep.
    """
    tz_name = getattr(chat, "timezone", None) if chat is not None else None
    if tz_name:
        try:
            return pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            pass
    return _GLOBAL_TZ


def tz_label(tz: pytz.BaseTzInfo, moment: Optional[datetime.datetime] = None) -> str:
    """``"Europe/Kyiv (UTC+03:00)"`` — the zone plus its current offset."""
    moment = moment or datetime.datetime.now(tz)
    offset = moment.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{getattr(tz, 'zone', str(tz))} (UTC{offset})"


def local_time_label(tz: pytz.BaseTzInfo) -> str:
    """``"28.07.2026 21:15"`` — the current local time in ``tz``, for previews."""
    return datetime.datetime.now(tz).strftime("%d.%m.%Y %H:%M")


# --- Now / today ------------------------------------------------------------

def now(chat=None) -> datetime.datetime:
    """Timezone-aware 'now' for a chat."""
    return datetime.datetime.now(chat_tz(chat))


def today(chat=None) -> datetime.date:
    return now(chat).date()


def tomorrow(chat=None) -> datetime.date:
    return today(chat) + datetime.timedelta(days=1)


def now_iso_utc() -> str:
    """UTC ISO timestamp — used for outbox/audit bookkeeping (instance-agnostic)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def tz_for_chat_id(chat_id: int) -> pytz.BaseTzInfo:
    """
    The timezone of the chat with this id (default when the chat is unknown).

    Convenience for interactive handlers, which have a ``chat_id`` rather than a
    loaded ``Chat``. The scheduler does *not* use this — it resolves each chat's
    zone once per tick from the rows it already has (see ChatClock).
    """
    from database.db import get_chat  # local import: db imports models, not this
    return chat_tz(await get_chat(chat_id))


async def today_for_chat_id(chat_id: int) -> datetime.date:
    """The current local date in this chat's timezone."""
    return datetime.datetime.now(await tz_for_chat_id(chat_id)).date()


def localize(tz: pytz.BaseTzInfo, naive: datetime.datetime) -> datetime.datetime:
    """
    Attach ``tz`` to a naive local datetime, resolving both DST oddities.

      * the local time does not exist (spring forward) → the equivalent moment
        just *after* the gap, so a reminder scheduled inside the skipped hour
        still fires that day rather than being silently dropped;
      * the local time exists twice (fall back) → the *first* occurrence, so a
        reminder is never delivered an hour late.

    Duplicates are impossible either way: reminders are deduplicated per
    calendar date, not per wall-clock minute (see the module docstring).
    """
    try:
        return tz.localize(naive, is_dst=None)
    except pytz.exceptions.NonExistentTimeError:
        # Interpreting the skipped time with the pre-transition offset lands
        # just past the gap once normalised back to wall-clock terms.
        return tz.normalize(tz.localize(naive, is_dst=False))
    except pytz.exceptions.AmbiguousTimeError:
        return tz.localize(naive, is_dst=True)  # the earlier of the two
    except (AttributeError, ValueError):
        # A fixed-offset / non-pytz tzinfo has no localize(); nothing to resolve.
        return naive.replace(tzinfo=tz)


def combine(tz: pytz.BaseTzInfo, date: datetime.date, time: datetime.time) -> datetime.datetime:
    """A tz-aware datetime for ``date`` at local ``time`` (DST-safe)."""
    return localize(tz, datetime.datetime.combine(date, time))


def parse_hhmm(value: Optional[str]) -> Optional[datetime.time]:
    """Parse a stored 'HH:MM' into a ``time``; ``None`` on missing/invalid."""
    if not value:
        return None
    try:
        hour, minute = map(int, value.split(":"))
        return datetime.time(hour=hour, minute=minute)
    except (ValueError, TypeError):
        return None


# --- Quiet hours (pure) -----------------------------------------------------

def has_quiet_hours(quiet_start: Optional[str], quiet_end: Optional[str]) -> bool:
    start = parse_hhmm(quiet_start)
    end = parse_hhmm(quiet_end)
    return start is not None and end is not None and start != end


def in_quiet_hours(moment: datetime.time, quiet_start: Optional[str], quiet_end: Optional[str]) -> bool:
    """
    Whether ``moment`` (a wall-clock time) falls inside the quiet window. The
    window may wrap past midnight (e.g. 22:00→07:00). Half-open [start, end).
    """
    start = parse_hhmm(quiet_start)
    end = parse_hhmm(quiet_end)
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= moment < end
    # Wraps midnight.
    return moment >= start or moment < end


def next_quiet_end(reference: datetime.datetime, quiet_start: Optional[str], quiet_end: Optional[str]) -> Optional[datetime.datetime]:
    """
    Given a ``reference`` datetime that is *inside* quiet hours, the datetime at
    which quiet hours next end. Returns ``None`` if quiet hours aren't set or
    ``reference`` isn't actually in them.
    """
    end = parse_hhmm(quiet_end)
    if end is None or not in_quiet_hours(reference.time(), quiet_start, quiet_end):
        return None
    candidate = reference.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += datetime.timedelta(days=1)
    return candidate
