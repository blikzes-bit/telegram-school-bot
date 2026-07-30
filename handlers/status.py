"""
``/status`` — safe administrator diagnostics.

In a group/supergroup the command is admin-only (it reports operational health
of the whole deployment); in a private chat the single user may always see it.
It never exposes secrets — see :mod:`services.status` for what is collected.
"""
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import services.status as status_service
from middleware.access import require_admin

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    # Server-side gate before touching anything: a hidden command protects nothing.
    if not await require_admin(message, message.bot):
        return
    try:
        data = await status_service.collect_status()
        text = status_service.format_status(data)
    except Exception:
        logger.exception("Failed to collect /status diagnostics")
        await message.answer("⚠️ Не удалось собрать статус, попробуй позже.")
        return
    await message.answer(text, parse_mode="HTML")
