"""
Handles Telegram's "basic group upgraded to supergroup" service message.

When this happens, Telegram assigns the chat a brand new ``chat_id`` and
sends a service message to the *old* id with ``migrate_to_chat_id`` set to
the new one. Without handling this, all of that chat's schedule/homework
data would become permanently orphaned under the old id.
"""
import logging

from aiogram import Router, F
from aiogram.types import Message

from database.db import migrate_chat

router = Router()
logger = logging.getLogger(__name__)


@router.message(F.migrate_to_chat_id.is_not(None))
async def handle_group_upgraded_to_supergroup(message: Message):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    moved = await migrate_chat(old_chat_id, new_chat_id)
    if moved:
        logger.info(f"Migrated chat data from {old_chat_id} to supergroup {new_chat_id}")
    else:
        logger.warning(f"Could not migrate chat data from {old_chat_id} to {new_chat_id}")
