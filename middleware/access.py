"""
Access-control policy for the bot.

Policy (documented here as the single source of truth, mirrored in README):

  * In a **private chat** there is exactly one user, so no admin distinction
    is made — every action below is always allowed.
  * In a **group/supergroup**, the following are admin-only (they change
    shared, chat-wide configuration and are hard/annoying to undo):
      - full reset (settings -> "Сбросить все настройки")
      - schedule / lesson-call-time changes (onboarding, re-onboarding, and
        the "📅 Расписание" edit flows)
      - reminder settings (time of day, enable/disable toggles)
  * Homework (add/edit/complete/restore/delete) is **not** admin-gated in
    groups — a class chat is expected to maintain its homework list
    collaboratively. Deleting a homework entry instead requires an explicit
    confirmation step (see handlers/homework.py) as its safety net, rather
    than being restricted to admins.

Two independent pieces live here:
  * ``ChatContextMiddleware`` — resolves/creates the ``Chat`` row for every
    update and stores it as ``data["chat"]``, and clears any stale
    ``is_blocked`` flag now that the chat is talking to the bot again.
  * ``OnboardingGuardMiddleware`` — attached only to routers whose handlers
    require a completed onboarding (today/schedule/homework/settings), so
    that command/callback handlers never have to re-check this themselves,
    and stale inline keyboards from before a reset can't be used to bypass
    onboarding.

``require_admin`` is called explicitly by the small number of entry-point
handlers that start an admin-only flow (see handlers/onboarding.py,
handlers/schedule.py, handlers/settings.py).
"""
from typing import Any, Awaitable, Callable, Dict, Union

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from database.db import get_or_create_chat, mark_chat_seen

ONBOARDING_REQUIRED_TEXT = (
    "⚠️ Сначала нужно завершить настройку бота. Отправь /start, чтобы начать."
)
ADMIN_ONLY_TEXT = "🚫 Это действие доступно только администраторам чата."


def is_group_chat(chat_type: str) -> bool:
    return chat_type in ("group", "supergroup")


async def is_chat_admin(bot, chat_id: int, user_id: int, chat_type: str) -> bool:
    """Private chats have no admin distinction — always allowed there."""
    if not is_group_chat(chat_type):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        # If Telegram can't tell us (network hiccup, bot removed, etc.) fail
        # closed for an admin-only action rather than silently allowing it.
        return False
    return member.status in ("administrator", "creator")


async def require_admin(event: Union[Message, CallbackQuery], bot) -> bool:
    """
    Returns True if the action may proceed. On rejection, answers the user
    with a clear message/alert and returns False — callers must ``return``
    immediately afterwards.
    """
    chat = event.chat if isinstance(event, Message) else event.message.chat
    user = event.from_user
    allowed = await is_chat_admin(bot, chat.id, user.id, chat.type)
    if allowed:
        return True

    if isinstance(event, CallbackQuery):
        await event.answer(ADMIN_ONLY_TEXT, show_alert=True)
    else:
        await event.answer(ADMIN_ONLY_TEXT)
    return False


class ChatContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        tg_chat = None
        if event.message is not None:
            tg_chat = event.message.chat
        elif event.callback_query is not None and event.callback_query.message is not None:
            tg_chat = event.callback_query.message.chat

        if tg_chat is not None:
            chat = await get_or_create_chat(tg_chat.id, tg_chat.type)
            if chat.is_blocked:
                await mark_chat_seen(tg_chat.id)
                chat.is_blocked = False
            data["chat"] = chat

        return await handler(event, data)


class OnboardingGuardMiddleware(BaseMiddleware):
    """Attach to a router (not the top-level Dispatcher) that requires a
    completed onboarding for every one of its handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any],
    ) -> Any:
        chat = data.get("chat")
        if chat is not None and not chat.is_onboarded:
            if isinstance(event, CallbackQuery):
                await event.answer(ONBOARDING_REQUIRED_TEXT, show_alert=True)
            else:
                await event.answer(ONBOARDING_REQUIRED_TEXT)
            return None
        return await handler(event, data)
