"""
Authorship + audit journal: who did what, in a form that is safe to store.

Two responsibilities, both deliberately small:

  * ``actor_from`` turns an aiogram ``Message``/``CallbackQuery`` into the only
    two pieces of identity we persist: the Telegram user id and a *display
    name*. Nothing else from the Update is read — no username, no phone, no
    message payload, no token. The name is trimmed to ``ACTOR_NAME_MAX``.
  * ``record`` writes one AuditLog row with a short, already-truncated summary.
    Summaries are built here from explicit, caller-supplied strings via
    :func:`summarize` — never by dumping an object or an Update.

Audit writes must never break the user-visible action they describe: ``record``
swallows and logs its own errors, because failing to journal a homework edit is
not a reason to fail the edit itself.

Timestamps are ISO-8601 UTC strings (same convention as authorship columns and
``ReminderJob.updated_at``), so they are instance- and timezone-agnostic.
"""
import datetime
import logging
from typing import Any, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Display names are shown inside HTML messages (escaped at render time) and in
# audit summaries; a hard cap keeps one absurd name from bloating every row.
ACTOR_NAME_MAX = 64
# Summaries are one short line in the history list — never a diff dump.
AUDIT_SUMMARY_MAX = 200

# --- Entity types (what changed) --------------------------------------------

ENTITY_HOMEWORK = "homework"
ENTITY_EXTRA = "extra"
ENTITY_SCHEDULE = "schedule"          # weekly template (lessons / call times / A-B weeks)
ENTITY_DAY_OVERRIDE = "day_override"  # per-date whole-day setting
ENTITY_LESSON_OVERRIDE = "lesson_override"  # per-date per-lesson change
ENTITY_SETTINGS = "settings"          # reminder settings, policies, full reset

ENTITY_TYPES = (
    ENTITY_HOMEWORK, ENTITY_EXTRA, ENTITY_SCHEDULE,
    ENTITY_DAY_OVERRIDE, ENTITY_LESSON_OVERRIDE, ENTITY_SETTINGS,
)

ENTITY_LABELS = {
    ENTITY_HOMEWORK: "📝 ДЗ",
    ENTITY_EXTRA: "🎯 Доп. занятие",
    ENTITY_SCHEDULE: "📅 Расписание",
    ENTITY_DAY_OVERRIDE: "🗓 Тип дня",
    ENTITY_LESSON_OVERRIDE: "🗓 Изменение урока",
    ENTITY_SETTINGS: "⚙️ Настройки",
}

# --- Actions ----------------------------------------------------------------

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_COMPLETE = "complete"
ACTION_RESTORE = "restore"

ACTIONS = (ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, ACTION_COMPLETE, ACTION_RESTORE)

ACTION_LABELS = {
    ACTION_CREATE: "добавил(а)",
    ACTION_UPDATE: "изменил(а)",
    ACTION_DELETE: "удалил(а)",
    ACTION_COMPLETE: "отметил(а) выполненным",
    ACTION_RESTORE: "вернул(а) в список",
}

UNKNOWN_ACTOR = "неизвестный автор"


# --- Time -------------------------------------------------------------------

def now_iso() -> str:
    """ISO-8601 UTC timestamp for authorship/audit columns."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def format_ts(value: Optional[str], tz=None) -> str:
    """
    Render a stored ISO timestamp as ``ДД.ММ.ГГГГ ЧЧ:ММ`` for display,
    converted into ``tz`` when given (the chat's timezone). Returns ``"—"`` for
    a missing value and the raw string for anything unparseable, so a legacy or
    hand-edited value can never crash a screen.
    """
    if not value:
        return "—"
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    if tz is not None:
        moment = moment.astimezone(tz)
    return moment.strftime("%d.%m.%Y %H:%M")


# --- Actor ------------------------------------------------------------------

def _display_name(user: Any) -> Optional[str]:
    """
    A human-readable name from a Telegram user, or None.

    Prefers ``full_name`` (first + last), falls back to ``first_name``. The
    username is deliberately not used: the display name is enough to answer
    "who wrote this?" without persisting an extra handle.
    """
    if user is None:
        return None
    name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
    if not name:
        return None
    name = " ".join(str(name).split())  # collapse whitespace/newlines
    return name[:ACTOR_NAME_MAX] or None


def actor_from(event: Any) -> Tuple[Optional[int], Optional[str]]:
    """
    ``(user_id, display_name)`` for the user behind a Message/CallbackQuery.

    Returns ``(None, None)`` when there is no user to attribute the action to
    (channel posts, service messages, background jobs) — "unknown author" is a
    supported state everywhere, exactly like a pre-existing NULL row.
    """
    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", None) if user is not None else None
    return (user_id if isinstance(user_id, int) else None), _display_name(user)


def actor_label(user_id: Optional[int], name: Optional[str]) -> str:
    """Plain-text (unescaped) label for an actor; callers escape for HTML."""
    if name:
        return name
    if user_id is not None:
        return f"id {user_id}"
    return UNKNOWN_ACTOR


# --- Summaries --------------------------------------------------------------

def summarize(*parts: Optional[str]) -> Optional[str]:
    """
    Join short, caller-chosen fragments into one safe summary line.

    Fragments are expected to be small, already-meaningful strings (a subject
    name, a date, a field label) — never whole objects or Updates. Newlines are
    flattened and the result is truncated to ``AUDIT_SUMMARY_MAX`` so one long
    homework description can't turn into a giant journal row.
    """
    chunks = [" ".join(str(p).split()) for p in parts if p]
    if not chunks:
        return None
    text = " · ".join(chunks)
    if len(text) > AUDIT_SUMMARY_MAX:
        text = text[: AUDIT_SUMMARY_MAX - 1].rstrip() + "…"
    return text


def fields_summary(field_labels: Sequence[str]) -> Optional[str]:
    """"поля: предмет, дата" — which fields an update touched (no values)."""
    labels = [label for label in field_labels if label]
    if not labels:
        return None
    return "поля: " + ", ".join(labels)


# --- Writing ----------------------------------------------------------------

async def record(
    chat_id: int,
    entity_type: str,
    action: str,
    *,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    entity_id: Optional[int] = None,
    summary: Optional[str] = None,
) -> None:
    """
    Append one audit entry. Never raises: an audit failure must not roll back or
    abort the user action it describes (it is logged instead).

    Unknown ``entity_type``/``action`` values are rejected here rather than at
    the DB layer so a typo in a caller surfaces as a log line, not a broken
    handler mid-flow.
    """
    if entity_type not in ENTITY_TYPES or action not in ACTIONS:
        logger.warning("Refusing to audit unknown entity/action: %s/%s", entity_type, action)
        return
    try:
        from database.db import add_audit_log
        await add_audit_log(
            chat_id=chat_id,
            entity_type=entity_type,
            action=action,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            entity_id=entity_id,
            summary=summary,
            created_at=now_iso(),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to write audit entry for chat %s: %s", chat_id, e)


async def record_event(
    event: Any,
    chat_id: int,
    entity_type: str,
    action: str,
    *,
    entity_id: Optional[int] = None,
    summary: Optional[str] = None,
) -> None:
    """:func:`record` with the actor taken straight from an aiogram event."""
    actor_user_id, actor_name = actor_from(event)
    await record(
        chat_id, entity_type, action,
        actor_user_id=actor_user_id, actor_name=actor_name,
        entity_id=entity_id, summary=summary,
    )
