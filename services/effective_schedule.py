"""
Single source of truth for the *effective* schedule of a concrete calendar
date: the weekly Schedule template with the per-date overrides
(DayOverride / LessonOverride) applied on top.

The weekly template (LessonSlot + Schedule) is never mutated. Every screen and
reminder that needs "what actually happens on this date" goes through here so
the overlay logic lives in exactly one place:

  * handlers/today.py       — the "📚 Сегодня" screen
  * services/scheduler.py   — the tomorrow / homework reminders
  * handlers/date_overrides.py — the "🗓 Изменения по датам" editor (preview)

``compute_effective_day`` is a pure function (no DB / network), so it is
trivially unit-testable; ``get_effective_day`` is the async convenience wrapper
that fetches the four inputs for one chat+date. The scheduler builds
EffectiveDay objects itself from batch-fetched data to avoid N+1 queries.
"""
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from database.models import DayOverride, LessonOverride, LessonSlot, Schedule
from utils import html_escape

# Whole-day types that suppress all lessons and are shown with their reason.
SPECIAL_DAY_TYPES = ("free", "holiday", "vacation")

DAY_TYPE_LABELS = {
    "free": "🟢 Свободный день",
    "holiday": "🎉 Праздник",
    "vacation": "🏖 Каникулы",
    "remote": "💻 Дистанционный день",
}

# Alternating-week template selectors. 'all' = the single (non-alternating)
# template every chat starts with. Week A is the anchor Monday's week; B is the
# next week. The parenthetical "чётная/нечётная" labels are just the friendly
# UI names for A/B — the actual week is derived from the chat's anchor Monday,
# never from the ISO week-of-year number.
WEEK_ALL = "all"
WEEK_A = "A"
WEEK_B = "B"

WEEK_LABELS = {
    "all": "Обычная неделя",
    "A": "неделя A (нечётная)",
    "B": "неделя B (чётная)",
}

WEEK_LABELS_SHORT = {
    "all": "Обычная",
    "A": "🅰 A (нечёт.)",
    "B": "🅱 B (чёт.)",
}


def resolve_week_type(week_mode: bool, anchor_monday, date: datetime.date) -> str:
    """
    Which weekly template applies on ``date`` for a chat.

      * alternating weeks off, or no anchor set → ``'all'`` (single template);
      * otherwise the week is A when an even number of whole weeks have passed
        since ``anchor_monday`` (the Monday starting week A), else B.

    Uses the Monday of each week so the answer is stable across the whole week
    and correct across month/year boundaries (plain day arithmetic, no ISO
    week-of-year), and independent of the time of day.
    """
    if not week_mode or anchor_monday is None:
        return WEEK_ALL
    monday_of_date = date - datetime.timedelta(days=date.weekday())
    weeks_diff = (monday_of_date - anchor_monday).days // 7
    return WEEK_A if weeks_diff % 2 == 0 else WEEK_B


@dataclass
class EffectiveLesson:
    lesson_number: int
    start_time: Optional[str]
    end_time: Optional[str]
    subject_name: Optional[str]  # None → free slot (no subject in the template)
    cancelled: bool = False
    added: bool = False          # one-off lesson not present in the template
    time_changed: bool = False   # time differs from the template
    subject_changed: bool = False  # subject differs from the template
    note: Optional[str] = None


@dataclass
class EffectiveDay:
    date: datetime.date
    weekday: int
    day_type: Optional[str]      # None (normal) | free | holiday | vacation | remote
    day_note: Optional[str]
    lessons: List[EffectiveLesson] = field(default_factory=list)

    @property
    def is_special(self) -> bool:
        """A day that cancels all lessons (free / holiday / vacation)."""
        return self.day_type in SPECIAL_DAY_TYPES

    @property
    def is_remote(self) -> bool:
        return self.day_type == "remote"

    @property
    def has_lessons(self) -> bool:
        """True if the day has at least one lesson that actually takes place."""
        return any(
            (not lesson.cancelled) and lesson.subject_name for lesson in self.lessons
        )

    @property
    def has_changes(self) -> bool:
        """True if any override (day-level or lesson-level) applies to this date."""
        if self.day_type is not None:
            return True
        return any(
            lesson.cancelled or lesson.added or lesson.time_changed or lesson.subject_changed
            for lesson in self.lessons
        )


def compute_effective_day(
    date: datetime.date,
    slots: Sequence[LessonSlot],
    schedule_items: Sequence[Schedule],
    day_override: Optional[DayOverride],
    lesson_overrides: Sequence[LessonOverride],
) -> EffectiveDay:
    """
    Overlay the per-date changes on the weekly template for ``date``.

    ``schedule_items`` must already be the template rows for this date's
    weekday. All arguments are for a single chat; callers must scope the
    fetches by chat_id themselves.
    """
    weekday = date.weekday()
    day_type = day_override.day_type if day_override is not None else None
    day_note = day_override.note if day_override is not None else None

    # A free/holiday/vacation day cancels everything — no lessons emitted.
    if day_type in SPECIAL_DAY_TYPES:
        return EffectiveDay(date=date, weekday=weekday, day_type=day_type, day_note=day_note, lessons=[])

    subject_map = {item.lesson_number: item.subject_name for item in schedule_items}
    slot_map = {slot.lesson_number: (slot.start_time, slot.end_time) for slot in slots}
    override_map = {ov.lesson_number: ov for ov in lesson_overrides}

    lessons: List[EffectiveLesson] = []
    for num in sorted(set(slot_map) | set(override_map)):
        override = override_map.get(num)
        tmpl_time = slot_map.get(num)
        tmpl_subject = subject_map.get(num)

        if override is not None and override.action == "cancel":
            # Struck-through in the UI; time/subject shown from the template.
            start, end = tmpl_time if tmpl_time is not None else (override.start_time, override.end_time)
            lessons.append(EffectiveLesson(
                lesson_number=num, start_time=start, end_time=end,
                subject_name=tmpl_subject, cancelled=True, note=override.note,
            ))
            continue

        if override is not None and override.action == "set":
            start = override.start_time if override.start_time is not None else (tmpl_time[0] if tmpl_time else None)
            end = override.end_time if override.end_time is not None else (tmpl_time[1] if tmpl_time else None)
            subject = override.subject_name if override.subject_name is not None else tmpl_subject
            is_added = tmpl_time is None and tmpl_subject is None
            time_changed = (
                tmpl_time is not None
                and override.start_time is not None
                and (override.start_time, override.end_time) != tmpl_time
            )
            subject_changed = (
                tmpl_subject is not None
                and override.subject_name is not None
                and override.subject_name != tmpl_subject
            )
            lessons.append(EffectiveLesson(
                lesson_number=num, start_time=start, end_time=end, subject_name=subject,
                added=is_added, time_changed=time_changed, subject_changed=subject_changed,
                note=override.note,
            ))
            continue

        # No override for this slot — plain template lesson (needs a slot time).
        if tmpl_time is not None:
            lessons.append(EffectiveLesson(
                lesson_number=num, start_time=tmpl_time[0], end_time=tmpl_time[1],
                subject_name=tmpl_subject,
            ))

    return EffectiveDay(date=date, weekday=weekday, day_type=day_type, day_note=day_note, lessons=lessons)


async def resolve_week_type_for_chat(chat_id: int, date: datetime.date) -> str:
    """Resolve the applicable week template ('all'/'A'/'B') for a chat+date."""
    from database.db import get_chat
    chat = await get_chat(chat_id)
    if chat is None:
        return WEEK_ALL
    return resolve_week_type(chat.week_mode, chat.week_anchor_monday, date)


async def get_effective_day(chat_id: int, date: datetime.date) -> EffectiveDay:
    """
    Async convenience wrapper: fetch the inputs for one chat+date, selecting the
    correct weekly template (the 'all' template, or week A/B when the chat uses
    alternating weeks). Date overrides always win over the weekly template.
    """
    # Imported here to avoid a circular import (db.py imports models, not this).
    from database.db import (
        get_lesson_slots, get_schedule, get_day_override, get_lesson_overrides,
    )
    week_type = await resolve_week_type_for_chat(chat_id, date)
    slots = await get_lesson_slots(chat_id)
    schedule_items = await get_schedule(chat_id, date.weekday(), week_type=week_type)
    day_override = await get_day_override(chat_id, date)
    lesson_overrides = await get_lesson_overrides(chat_id, date)
    return compute_effective_day(date, slots, schedule_items, day_override, lesson_overrides)


# --- Shared formatting (reused by Today / schedule editor / reminders) -------

def _subject_emoji(subject: str) -> str:
    sub_lower = subject.lower()
    if "мат" in sub_lower or "алг" in sub_lower or "геом" in sub_lower:
        return "📐"
    if "физ" in sub_lower:
        return "⚡️"
    if "хим" in sub_lower or "био" in sub_lower:
        return "🧪"
    if "укр" in sub_lower or "рус" in sub_lower or "яз" in sub_lower or "лит" in sub_lower:
        return "📖"
    if "англ" in sub_lower or "eng" in sub_lower or "ин" in sub_lower:
        return "🇬🇧"
    if "ист" in sub_lower or "геогр" in sub_lower:
        return "🌍"
    return "📘"


def format_day_type_banner(eff_day: EffectiveDay) -> Optional[str]:
    """A one-line banner for a special/remote day (with reason), or None."""
    if eff_day.day_type is None:
        return None
    label = DAY_TYPE_LABELS.get(eff_day.day_type, eff_day.day_type)
    if eff_day.day_note:
        return f"{label}\n📝 {html_escape(eff_day.day_note)}"
    return label


def format_lesson_line(
    lesson: EffectiveLesson,
    *,
    per_subject_emoji: bool = False,
    show_free: bool = False,
) -> Optional[str]:
    """
    One schedule row as an HTML line, or None when the row should be skipped
    (a free/empty template slot when ``show_free`` is False).

      * a cancelled lesson is struck-through with a clear "(отменён)" mark;
      * an added one-off lesson is tagged 🆕, a changed one ✏️;
      * ``per_subject_emoji`` picks a subject-specific icon (day editor / day
        view) instead of the plain 📘 used on the compact screens.
    """
    time_part = f"{lesson.start_time} - {lesson.end_time}"

    if lesson.cancelled:
        subject = html_escape(lesson.subject_name) if lesson.subject_name else "урок"
        line = f"{lesson.lesson_number}️⃣ <code>{time_part}</code> | ❌ <s>{subject}</s> <i>(отменён)</i>"
        if lesson.note:
            line += f"\n   📝 {html_escape(lesson.note)}"
        return line

    if lesson.subject_name:
        emoji = _subject_emoji(lesson.subject_name) if per_subject_emoji else "📘"
        mark = ""
        if lesson.added:
            mark = " 🆕"
        elif lesson.time_changed or lesson.subject_changed:
            mark = " ✏️"
        line = f"{lesson.lesson_number}️⃣ <code>{time_part}</code> | {emoji} <b>{html_escape(lesson.subject_name)}</b>{mark}"
        if lesson.note:
            line += f"\n   📝 {html_escape(lesson.note)}"
        return line

    if show_free:
        return f"{lesson.lesson_number}️⃣ <code>{time_part}</code> | ✏️ <i>Свободно</i>"
    return None


def format_effective_schedule_body(
    eff_day: EffectiveDay,
    *,
    per_subject_emoji: bool = False,
    show_free: bool = False,
    no_lessons_text: str = "🥱 Нет уроков!",
) -> str:
    """
    The schedule body for an effective day: either the special-day banner, or
    the (possibly overridden) lesson lines. Does NOT include a header or the
    extra-activities block — callers add those.
    """
    banner = format_day_type_banner(eff_day)
    if eff_day.is_special:
        # Free / holiday / vacation: show the reason, no lessons.
        return banner or DAY_TYPE_LABELS.get(eff_day.day_type or "", "")

    lines: List[str] = []
    if banner:  # remote day: banner + the (still-happening) lessons below.
        lines.append(banner)

    lesson_lines = [
        line for lesson in eff_day.lessons
        if (line := format_lesson_line(lesson, per_subject_emoji=per_subject_emoji, show_free=show_free)) is not None
    ]
    if lesson_lines:
        lines.extend(lesson_lines)
    elif not banner:
        lines.append(no_lessons_text)

    return "\n".join(lines)
