"""
Who may do what in a chat. **The single source of truth for both surfaces** —
the Telegram bot and the Mini App API both resolve rights here, so the two can
never drift apart. Every check runs server-side, before the database is touched:
hiding a button is a nicety, a stale or hand-crafted callback must still be
refused here.

Two layers, and they compose:

1. **Roles** (:func:`capabilities`) — *may this person change things at all?*
   Driven by ``chats.access_mode``:

     * ``telegram`` (the default and every pre-existing chat) — Telegram admins
       get administrator rights, everyone else gets editor rights. Byte-for-byte
       the behaviour that existed before roles.
     * ``roles`` — the member's ``chat_memberships.app_role``
       (owner/editor/student/viewer) decides, and anybody without a role is a
       viewer. This is the explicit "only the people I picked enter data" switch.

   ``chats.owner_user_id`` always outranks both, so a chat cannot lock out the
   person who set it up.

2. **Homework-edit policy** (below) — *whose* entries may an editor change?
   Lives on ``Chat.hw_edit_policy`` and narrows, never widens, layer 1.

Policies:

  * ``collaborative``    — anybody in the chat. The default, and exactly how
    every chat behaved before this existed, so enabling authorship changed
    nothing for existing chats.
  * ``creator_or_admin`` — the entry's author, or a chat administrator.
  * ``admin_only``       — chat administrators only.

Two cases are always allowed regardless of policy:

  * **private chats** — a single user, so there is no one to restrict;
  * **an entry with no known author** (``created_by_user_id IS NULL``: every row
    written before authorship existed) under ``creator_or_admin`` — there is no
    creator to compare against, and silently locking a class out of its own
    pre-existing homework would be a worse failure than allowing the edit. A
    chat that wants those locked down picks ``admin_only``, where NULL authors
    are restricted like everything else.
"""
from dataclasses import dataclass
from typing import Any, Optional

from middleware.access import is_chat_admin, is_group_chat

# --- App roles ---------------------------------------------------------------
# A member's role *inside the app*, independent of Telegram admin status. Stored
# on ``chat_memberships.app_role``; NULL means "not assigned".

ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_STUDENT = "student"
ROLE_VIEWER = "viewer"

APP_ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_STUDENT, ROLE_VIEWER)

# Reported (never assigned) roles for a chat still on Telegram-derived rights.
# They exist so the API can say *why* someone has the rights they have, and are
# deliberately not part of ``APP_ROLES``: nobody can be given them.
ROLE_TG_ADMIN = "admin"
ROLE_TG_MEMBER = "member"

# Roles the owner may hand out. ``owner`` is not in the list: ownership follows
# ``chats.owner_user_id`` and is not something to be granted by picking it from
# a dropdown.
ASSIGNABLE_ROLES = (ROLE_EDITOR, ROLE_STUDENT, ROLE_VIEWER)

ROLE_LABELS = {
    ROLE_OWNER: "👑 Владелец",
    ROLE_EDITOR: "✍️ Редактор",
    ROLE_STUDENT: "🎓 Ученик",
    ROLE_VIEWER: "👀 Только смотрит",
    ROLE_TG_ADMIN: "🛡 Админ чата",
    ROLE_TG_MEMBER: "👥 Участник чата",
}

ROLE_DESCRIPTIONS = {
    ROLE_OWNER: "может всё, включая расписание и участников",
    ROLE_EDITOR: "может вести домашку и занятия, но не расписание",
    ROLE_STUDENT: "смотрит и отмечает домашку выполненной",
    ROLE_VIEWER: "только смотрит",
}

# --- Access mode -------------------------------------------------------------
# How rights are decided in a chat. NULL is treated as ``telegram``.

ACCESS_TELEGRAM = "telegram"
ACCESS_ROLES = "roles"

ACCESS_MODES = (ACCESS_TELEGRAM, ACCESS_ROLES)

ACCESS_MODE_LABELS = {
    ACCESS_TELEGRAM: "👥 Как в Telegram",
    ACCESS_ROLES: "🔒 Только выбранные люди",
}

ACCESS_MODE_DESCRIPTIONS = {
    ACCESS_TELEGRAM: "вносить данные могут администраторы чата, как и раньше",
    ACCESS_ROLES: "вносит только владелец и те, кому он выдал роль редактора",
}


def normalize_app_role(value: Optional[str]) -> Optional[str]:
    """Map a stored/incoming app role onto a known one, or ``None``."""
    return value if value in APP_ROLES else None


def normalize_access_mode(value: Optional[str]) -> str:
    """Unknown/absent access mode means the pre-existing Telegram behaviour."""
    return value if value in ACCESS_MODES else ACCESS_TELEGRAM


@dataclass(frozen=True)
class Capabilities:
    """What one person may do in one chat.

    Computed once, server-side, by :func:`capabilities` — the single function
    both the bot and the web API call, so the two surfaces can never disagree
    about who is allowed to do what.

    Homework *editing* is two independent gates and both must pass: this
    ``can_edit_homework`` (the role may touch existing entries at all) and the
    per-entry ``hw_edit_policy`` (see :func:`can_edit_homework_sync`), which can
    still narrow an editor down to their own entries.
    """

    role: str
    is_owner: bool
    # Kept under this name because it is what the rest of the code already asks
    # for: "may act like a chat administrator here".
    is_admin: bool
    can_edit_schedule: bool
    can_add_homework: bool
    can_edit_homework: bool
    can_complete_homework: bool
    can_edit_extra: bool
    # Money is its own gate: an editor keeps the lesson list, the owner keeps
    # the payments. Same set as extras today, separate so it can diverge without
    # a second meaning being smuggled into ``can_edit_extra``.
    can_edit_payments: bool
    can_manage_members: bool


def _capabilities_for_role(role: str) -> Capabilities:
    if role == ROLE_OWNER:
        return Capabilities(
            role=role, is_owner=True, is_admin=True, can_edit_schedule=True,
            can_add_homework=True, can_edit_homework=True,
            can_complete_homework=True, can_edit_extra=True,
            can_edit_payments=True, can_manage_members=True,
        )
    if role == ROLE_EDITOR:
        return Capabilities(
            role=role, is_owner=False, is_admin=False, can_edit_schedule=False,
            can_add_homework=True, can_edit_homework=True,
            can_complete_homework=True, can_edit_extra=True,
            can_edit_payments=True, can_manage_members=False,
        )
    if role == ROLE_STUDENT:
        return Capabilities(
            role=role, is_owner=False, is_admin=False, can_edit_schedule=False,
            can_add_homework=False, can_edit_homework=False,
            can_complete_homework=True, can_edit_extra=False,
            can_edit_payments=False, can_manage_members=False,
        )
    return Capabilities(
        role=ROLE_VIEWER, is_owner=False, is_admin=False, can_edit_schedule=False,
        can_add_homework=False, can_edit_homework=False,
        can_complete_homework=False, can_edit_extra=False,
        can_edit_payments=False, can_manage_members=False,
    )


def capabilities(
    chat: Any,
    *,
    user_id: Optional[int],
    is_telegram_admin: bool,
    app_role: Optional[str] = None,
) -> Capabilities:
    """What ``user_id`` may do in ``chat``.

    Resolution order, and why each step is there:

    1. **A private chat is its single user's own.** Nothing to restrict, so full
       capabilities — unchanged from before app roles existed.
    2. **The recorded owner always wins.** ``chats.owner_user_id`` outranks
       everything else so a chat can never lock out the person who set it up
       (e.g. by turning on role mode before assigning themselves a role).
    3. **Access mode ``telegram`` (the default, and every pre-existing chat)** —
       Telegram admins get administrator capabilities, everyone else gets
       *editor*, which is exactly the old behaviour: any member could add and
       (subject to ``hw_edit_policy``) edit homework, while schedule and
       settings were admin-only.
    4. **Access mode ``roles``** — the assigned ``app_role`` decides, and
       somebody with no role assigned is a **viewer**. This is what makes "only
       the teacher enters data" real rather than a hidden button: an uninvited
       member can read and nothing more.

    Note that ``is_telegram_admin`` is *not* consulted in role mode. That is
    deliberate: in a family group half the members are Telegram admins, and the
    point of role mode is that app rights stop following that.
    """
    if not is_group_chat(getattr(chat, "chat_type", "private")):
        return _capabilities_for_role(ROLE_OWNER)

    owner_id = getattr(chat, "owner_user_id", None)
    if owner_id is not None and user_id is not None and owner_id == user_id:
        return _capabilities_for_role(ROLE_OWNER)

    mode = normalize_access_mode(getattr(chat, "access_mode", None))
    if mode == ACCESS_TELEGRAM:
        if is_telegram_admin:
            return Capabilities(
                role=ROLE_TG_ADMIN, is_owner=False, is_admin=True,
                can_edit_schedule=True, can_add_homework=True,
                can_edit_homework=True, can_complete_homework=True,
                can_edit_extra=True, can_edit_payments=True,
                can_manage_members=True,
            )
        # A plain group member, reproducing the pre-roles rules exactly: the
        # homework list is collaborative (subject to ``hw_edit_policy``), while
        # extra activities, the schedule and settings are admin-only. This is
        # *not* the ``editor`` app role — an editor may also manage extras.
        return Capabilities(
            role=ROLE_TG_MEMBER, is_owner=False, is_admin=False,
            can_edit_schedule=False, can_add_homework=True,
            can_edit_homework=True, can_complete_homework=True,
            can_edit_extra=False, can_edit_payments=False,
            can_manage_members=False,
        )

    return _capabilities_for_role(normalize_app_role(app_role) or ROLE_VIEWER)


async def capabilities_for_event(bot: Any, chat: Any, user_id: Optional[int]) -> Capabilities:
    """:func:`capabilities` for a bot update, resolving the two lookups it needs.

    The Telegram admin check is skipped entirely in role mode — there is no
    reason to spend a ``getChatMember`` call on an answer that is not consulted.
    """
    from database.db import get_membership

    chat_id = getattr(chat, "chat_id", None)
    chat_type = getattr(chat, "chat_type", "private")
    mode = normalize_access_mode(getattr(chat, "access_mode", None))

    is_telegram_admin = False
    if mode == ACCESS_TELEGRAM and bot is not None and chat_id is not None and user_id is not None:
        is_telegram_admin = await is_chat_admin(bot, chat_id, user_id, chat_type)

    app_role = None
    if mode == ACCESS_ROLES and chat_id is not None and user_id is not None:
        membership = await get_membership(chat_id, user_id)
        app_role = getattr(membership, "app_role", None)

    return capabilities(
        chat, user_id=user_id, is_telegram_admin=is_telegram_admin, app_role=app_role,
    )



EXTRA_DENIED_TEXT = (
    "🚫 Доп. занятия здесь может менять только тот, кому это разрешил владелец."
)


async def require_extra_access(event: Any, chat: Any, bot: Any = None) -> bool:
    """
    Guard for adding/editing/deleting an extra activity from the bot.

    Distinct from ``middleware.access.require_admin`` because an *editor* is
    meant to manage sessions and clubs without getting the keys to the schedule
    and settings. In the default access mode this resolves to "admins only in a
    group, anyone in a private chat" — exactly the previous rule.
    """
    from services.audit import actor_from

    user_id, _ = actor_from(event)
    caps = await capabilities_for_event(bot or getattr(event, "bot", None), chat, user_id)
    if caps.can_edit_extra:
        return True

    from middleware.access import ADMIN_ONLY_TEXT

    mode = normalize_access_mode(getattr(chat, "access_mode", None))
    text = EXTRA_DENIED_TEXT if mode == ACCESS_ROLES else ADMIN_ONLY_TEXT
    if hasattr(event, "data"):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return False


POLICY_COLLABORATIVE = "collaborative"
POLICY_CREATOR_OR_ADMIN = "creator_or_admin"
POLICY_ADMIN_ONLY = "admin_only"

HW_EDIT_POLICIES = (POLICY_COLLABORATIVE, POLICY_CREATOR_OR_ADMIN, POLICY_ADMIN_ONLY)

DEFAULT_HW_EDIT_POLICY = POLICY_COLLABORATIVE

POLICY_LABELS = {
    POLICY_COLLABORATIVE: "👥 Все участники",
    POLICY_CREATOR_OR_ADMIN: "✍️ Автор или админ",
    POLICY_ADMIN_ONLY: "🛡 Только админы",
}

POLICY_DESCRIPTIONS = {
    POLICY_COLLABORATIVE: "любой участник чата может изменять и удалять любое ДЗ",
    POLICY_CREATOR_OR_ADMIN: "изменять ДЗ может тот, кто его добавил, или администратор",
    POLICY_ADMIN_ONLY: "изменять ДЗ могут только администраторы чата",
}

DENIED_TEXTS = {
    POLICY_CREATOR_OR_ADMIN: (
        "🚫 Это ДЗ добавил другой участник. Изменять его может только автор "
        "или администратор чата."
    ),
    POLICY_ADMIN_ONLY: "🚫 В этом чате изменять ДЗ могут только администраторы.",
}

GENERIC_DENIED_TEXT = "🚫 У тебя нет прав на это действие."


def normalize_policy(value: Optional[str]) -> str:
    """Map any stored/incoming value onto a known policy (default on garbage)."""
    return value if value in HW_EDIT_POLICIES else DEFAULT_HW_EDIT_POLICY


def denied_text(policy: Optional[str]) -> str:
    return DENIED_TEXTS.get(normalize_policy(policy), GENERIC_DENIED_TEXT)


async def can_edit_homework(
    bot: Any,
    chat: Any,
    homework: Any,
    user_id: Optional[int],
    *,
    completing: bool = False,
) -> bool:
    """
    Whether ``user_id`` may modify ``homework`` (edit / complete / restore /
    delete) in ``chat``.

    Both layers are applied, role first: a viewer is refused even under the
    ``collaborative`` policy, and a student may tick homework off
    (``completing=True``) without being able to rewrite it.

    ``chat`` is the ``Chat`` row (its ``chat_type`` decides group-vs-private),
    ``homework`` may be ``None`` — a missing entry is reported as "not found" by
    the caller, so there is nothing to protect and we allow it through.
    """
    if chat is None:
        return True
    chat_type = getattr(chat, "chat_type", "private")
    if not is_group_chat(chat_type):
        return True  # private chat: a single user, nothing to restrict

    caps = await capabilities_for_event(bot, chat, user_id)
    if not (caps.can_complete_homework if completing else caps.can_edit_homework):
        return False

    policy = normalize_policy(getattr(chat, "hw_edit_policy", None))
    if policy == POLICY_COLLABORATIVE:
        return True
    if homework is None:
        return True

    if policy == POLICY_CREATOR_OR_ADMIN:
        author_id = getattr(homework, "created_by_user_id", None)
        if author_id is None:
            # Legacy entry with no recorded author — see module docstring.
            return True
        if user_id is not None and author_id == user_id:
            return True

    return caps.is_admin


def can_edit_homework_sync(
    *,
    is_private: bool,
    is_admin: bool,
    policy: Optional[str],
    author_id: Optional[int],
    user_id: Optional[int],
) -> bool:
    """
    Telegram-API-free variant of :func:`can_edit_homework` for callers (the web
    API) that already know ``is_admin`` from a verified ``ChatMembership`` role
    instead of calling ``getChatMember``. Mirrors the exact same policy for an
    *existing* homework entry (not the "may add" case, which is unrestricted).
    """
    if is_private:
        return True
    policy = normalize_policy(policy)
    if policy == POLICY_COLLABORATIVE:
        return True
    if policy == POLICY_CREATOR_OR_ADMIN:
        if author_id is None:
            return True
        if user_id is not None and author_id == user_id:
            return True
    return is_admin


async def require_homework_access(
    event: Any, chat: Any, homework: Any, *, completing: bool = False
) -> bool:
    """
    Guard for a mutating homework handler: returns True when the action may
    proceed, otherwise answers the user with a clear reason and returns False
    (callers must ``return`` immediately afterwards).

    ``completing=True`` uses the looser "may tick it off" gate — see
    :func:`can_edit_homework`.
    """
    from services.audit import actor_from

    user_id, _ = actor_from(event)
    bot = getattr(event, "bot", None)
    if await can_edit_homework(bot, chat, homework, user_id, completing=completing):
        return True

    text = denied_text(getattr(chat, "hw_edit_policy", None))
    # A CallbackQuery.answer takes show_alert; a Message.answer does not.
    if hasattr(event, "data"):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return False
