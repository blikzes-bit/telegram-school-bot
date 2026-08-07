"""
Chat profile: *what this chat is for*. The single source of truth for both the
bot and the web API.

Three profiles, because the same bot serves three genuinely different jobs:

  * ``personal`` — one person's electronic diary in a private chat. Nobody to
    restrict, nobody to invite; just a timetable and homework.
  * ``class``    — a school class: one teacher (or a few admins) and up to
    ~30 students. Everyone reads, data entry is expected to be the teacher's.
  * ``tutor``    — lessons with a tutor: the tutor, the student, maybe parents.
    There is **no school timetable with bell times** here — the lessons *are*
    the sessions, so they live as extra activities, and the school-schedule
    onboarding is skipped entirely.

Two rules keep this safe to introduce into a live deployment:

1. **``Chat.profile`` is nullable and ``NULL`` means "never asked".** A NULL is
   resolved on read (:func:`resolve`) from the chat type — private → personal,
   group → class — which is exactly how every existing chat already behaves. No
   backfill, no migration of behaviour.
2. **A profile decides what is *shown* and what defaults are picked when it is
   chosen — never who is allowed to do what.** Permissions stay in
   ``services.permissions`` / ``middleware.access``. Changing a profile later
   therefore cannot hand anybody rights they did not have, and cannot delete
   data: a ``tutor`` chat merely stops showing the weekly timetable it may still
   have stored, so switching back restores the view.
"""
from dataclasses import dataclass
from typing import Optional

from services.permissions import POLICY_ADMIN_ONLY, POLICY_COLLABORATIVE

PROFILE_PERSONAL = "personal"
PROFILE_CLASS = "class"
PROFILE_TUTOR = "tutor"

PROFILES = (PROFILE_PERSONAL, PROFILE_CLASS, PROFILE_TUTOR)

PROFILE_LABELS = {
    PROFILE_PERSONAL: "📖 Личный дневник",
    PROFILE_CLASS: "🏫 Класс",
    PROFILE_TUTOR: "👩‍🏫 Занятия с репетитором",
}

# Deliberately written in plain words: this text is the first thing a new user
# reads, and it has to make sense to a seven-year-old as well as to a teacher.
PROFILE_DESCRIPTIONS = {
    PROFILE_PERSONAL: "только для меня: мои уроки, моя домашка, мои напоминания",
    PROFILE_CLASS: "школьный класс: расписание и домашка на всех, вносит обычно учитель",
    PROFILE_TUTOR: "занятия с репетитором: без школьного расписания, только сами занятия",
}


@dataclass(frozen=True)
class ProfileFeatures:
    """Which parts of the app this profile uses.

    Consumed by the bot's menus and by the Mini App's navigation. A feature that
    is off is *hidden*, not merely disabled — an empty screen nobody can use is
    worse than no screen at all.
    """

    # The weekly school timetable with bell times (and therefore the
    # lesson-based onboarding, "Расписание", per-date overrides, A/B weeks).
    school_schedule: bool
    homework: bool
    extra_activities: bool
    # Lesson payments. Only the tutor profile has a money side; a school class
    # and a personal diary have nothing to pay for through this bot.
    payments: bool
    # Is "who may change homework" a meaningful setting? (No, if there is
    # exactly one person in the chat.)
    homework_policy: bool


_FEATURES = {
    PROFILE_PERSONAL: ProfileFeatures(
        school_schedule=True, homework=True, extra_activities=True,
        payments=False, homework_policy=False,
    ),
    PROFILE_CLASS: ProfileFeatures(
        school_schedule=True, homework=True, extra_activities=True,
        payments=False, homework_policy=True,
    ),
    PROFILE_TUTOR: ProfileFeatures(
        school_schedule=False, homework=True, extra_activities=True,
        payments=True, homework_policy=True,
    ),
}


@dataclass(frozen=True)
class ProfileDefaults:
    """Settings applied **once**, at the moment a profile is chosen in onboarding.

    They are starting points, not rules: the chat can change every one of them
    afterwards, and re-picking the same profile later never silently re-applies
    them (see ``handlers/onboarding``). This is what makes "only the teacher
    enters data" true out of the box for a new class without touching a single
    existing chat.
    """

    hw_edit_policy: str
    # "Собери портфель на завтра" only makes sense with a school timetable.
    schedule_reminder_enabled: bool
    # Heads-up about tomorrow's cancellations/replacements — same reasoning.
    changes_reminder_enabled: bool


_DEFAULTS = {
    PROFILE_PERSONAL: ProfileDefaults(
        hw_edit_policy=POLICY_COLLABORATIVE,  # a single user — nothing to gate
        schedule_reminder_enabled=True,
        changes_reminder_enabled=True,
    ),
    PROFILE_CLASS: ProfileDefaults(
        hw_edit_policy=POLICY_ADMIN_ONLY,
        schedule_reminder_enabled=True,
        changes_reminder_enabled=True,
    ),
    PROFILE_TUTOR: ProfileDefaults(
        hw_edit_policy=POLICY_ADMIN_ONLY,
        schedule_reminder_enabled=False,
        changes_reminder_enabled=False,
    ),
}


def normalize(value: Optional[str]) -> Optional[str]:
    """Map a stored/incoming value onto a known profile, or ``None``.

    Unknown text (a hand-crafted request, a value written by a newer version)
    becomes ``None``, i.e. "not chosen", so :func:`resolve` falls back to the
    chat-type default instead of the app behaving unpredictably.
    """
    return value if value in PROFILES else None


def resolve(chat: object) -> str:
    """The profile actually in effect for ``chat``.

    ``NULL``/unknown resolves from the chat type — private chats are personal
    diaries, groups are classes — which reproduces the behaviour every chat had
    before profiles existed.
    """
    stored = normalize(getattr(chat, "profile", None))
    if stored is not None:
        return stored
    chat_type = getattr(chat, "chat_type", "private")
    return PROFILE_CLASS if chat_type in ("group", "supergroup") else PROFILE_PERSONAL


def features(profile: Optional[str]) -> ProfileFeatures:
    """Features of a profile name (unknown → the class profile, the widest set)."""
    return _FEATURES.get(profile or "", _FEATURES[PROFILE_CLASS])


def features_for(chat: object) -> ProfileFeatures:
    return features(resolve(chat))


def defaults(profile: Optional[str]) -> ProfileDefaults:
    return _DEFAULTS.get(profile or "", _DEFAULTS[PROFILE_CLASS])


def label(profile: Optional[str]) -> str:
    return PROFILE_LABELS.get(profile or "", PROFILE_LABELS[PROFILE_CLASS])


# --- Payments (tutor profile) ------------------------------------------------

PAYMENT_PERIODS = ("one_time", "monthly", "per_lesson")

PAYMENT_PERIOD_LABELS = {
    "one_time": "разово",
    "monthly": "каждый месяц",
    "per_lesson": "за занятие",
}


# Thousands separator inside an amount: a NO-BREAK SPACE (U+00A0), so a number
# like "1 250 UAH" can never be broken across two lines mid-figure.
THOUSANDS_SEPARATOR = " "


def format_amount(amount_minor: int, currency: str) -> str:
    """Money as a human string, e.g. ``350 UAH`` or ``1 250,50 UAH``.

    Formatted here, once, so the bot and the app never disagree — and so no
    client has to do arithmetic on money. Kept plain: a no-break space between
    thousands and a comma before the pennies, with the pennies dropped entirely
    when they are zero (the common case for a lesson fee).
    """
    whole, pennies = divmod(max(0, int(amount_minor)), 100)
    grouped = f"{whole:,}".replace(",", THOUSANDS_SEPARATOR)
    text = grouped if pennies == 0 else f"{grouped},{pennies:02d}"
    return f"{text} {currency}".strip()


def payment_status(due_date, today, is_paid: bool, remind_days_before: int) -> str:
    """``paid`` | ``overdue`` | ``due_soon`` | ``upcoming``.

    ``due_soon`` uses the entry's own reminder window, so what the UI highlights
    and what the reminder talks about are the same thing by construction.
    """
    if is_paid:
        return "paid"
    if due_date < today:
        return "overdue"
    if (due_date - today).days <= max(0, remind_days_before):
        return "due_soon"
    return "upcoming"


def needs_schedule_onboarding(profile: Optional[str]) -> bool:
    """Whether onboarding must ask for lesson count / bell times / subjects."""
    return features(profile).school_schedule
