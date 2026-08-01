"""
Homework-edit policy: who may change a homework entry in a given chat.

The policy lives on ``Chat.hw_edit_policy`` and is enforced **server-side** by
:func:`can_edit_homework` / :func:`require_homework_access`, which every
mutating homework handler calls before touching the DB. Hiding a button is only
a UI nicety — a stale or hand-crafted callback must still be rejected here.

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
from typing import Any, Optional

from middleware.access import is_chat_admin, is_group_chat

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


async def can_edit_homework(bot: Any, chat: Any, homework: Any, user_id: Optional[int]) -> bool:
    """
    Whether ``user_id`` may modify ``homework`` (edit / complete / restore /
    delete) in ``chat``.

    ``chat`` is the ``Chat`` row (its ``chat_type`` decides group-vs-private),
    ``homework`` may be ``None`` — a missing entry is reported as "not found" by
    the caller, so there is nothing to protect and we allow it through.
    """
    if chat is None:
        return True
    chat_type = getattr(chat, "chat_type", "private")
    if not is_group_chat(chat_type):
        return True  # private chat: a single user, nothing to restrict

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

    if user_id is None:
        return False  # no identifiable user → cannot be an admin either
    return await is_chat_admin(bot, chat.chat_id, user_id, chat_type)


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


async def require_homework_access(event: Any, chat: Any, homework: Any) -> bool:
    """
    Guard for a mutating homework handler: returns True when the action may
    proceed, otherwise answers the user with a clear reason and returns False
    (callers must ``return`` immediately afterwards).
    """
    from services.audit import actor_from

    user_id, _ = actor_from(event)
    bot = getattr(event, "bot", None)
    if await can_edit_homework(bot, chat, homework, user_id):
        return True

    text = denied_text(getattr(chat, "hw_edit_policy", None))
    # A CallbackQuery.answer takes show_alert; a Message.answer does not.
    if hasattr(event, "data"):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return False
