"""``/web`` — hand the user a one-time, signed link into the Telegram Mini App.

Security model (see docs/WEB_APP_ARCHITECTURE.md):

  * membership is verified *before* a token is minted — the caller issued the
    command from inside this chat, and their admin/member role is read from
    Telegram (``is_chat_admin``) and recorded on the ``ChatMembership`` row;
  * the launch token is a fresh 256-bit random value; only its keyed hash is
    stored, it is bound to (telegram_user_id, chat_id), expires in minutes and
    is single-use;
  * the token travels to the Mini App only via the Telegram ``startapp`` deep
    link (so it arrives inside verified ``initData.start_param``), never as an
    untrusted query parameter the frontend would have to trust.
"""
import datetime

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import database.db as db
from middleware.access import is_chat_admin
from web_api.security import generate_token, hash_token
from web_api.settings import get_settings

router = Router()


def build_launch_url(
    bot_username: str | None, short_name: str, web_app_url: str, token: str
) -> str:
    """The URL the "open the app" button points at.

    Prefers the canonical ``https://t.me/<bot>/<short_name>?startapp=<token>``
    deep link, which is the only channel that delivers the token as a verified
    ``start_param``. Falls back to the raw web app URL (local development only,
    when no Mini App short name is configured).
    """
    if short_name and bot_username:
        return f"https://t.me/{bot_username}/{short_name}?startapp={token}"
    sep = "&" if "?" in web_app_url else "?"
    return f"{web_app_url}{sep}tgWebAppStartParam={token}"


@router.message(Command("web"))
async def cmd_web(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    chat = message.chat
    user = message.from_user
    settings = get_settings()

    # Role is authoritative from Telegram: admins in a group, everyone in a
    # private chat (which has no admin distinction).
    is_admin = await is_chat_admin(bot, chat.id, user.id, chat.type)
    role = "admin" if is_admin else "member"

    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (
        now + datetime.timedelta(seconds=settings.launch_token_ttl_seconds)
    ).isoformat()

    # Record (or re-verify) the membership that will scope the web session.
    await db.upsert_membership(chat.id, user.id, role, now_iso)

    raw_token = generate_token()
    await db.create_launch_token(
        hash_token(settings.session_secret, raw_token),
        user.id,
        chat.id,
        now_iso,
        expires_iso,
    )

    bot_username = None
    try:
        me = await bot.get_me()
        bot_username = getattr(me, "username", None)
    except Exception:
        bot_username = None

    url = build_launch_url(
        bot_username, settings.web_app_short_name, settings.web_app_url, raw_token
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть приложение", url=url)]]
    )
    ttl_minutes = max(1, settings.launch_token_ttl_seconds // 60)
    await message.answer(
        "🌐 <b>Веб-приложение</b>\n\n"
        "Открой этот класс в приложении — расписание, домашние задания и "
        "доп. занятия в удобном виде.\n\n"
        f"⏳ Ссылка одноразовая и действует {ttl_minutes} мин.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
