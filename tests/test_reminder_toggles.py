"""Independent enable/disable toggles for the HW and schedule ("portfolio") reminders."""
from database.db import get_or_create_chat, update_chat_reminder_flags

CHAT_ID = 424242


async def test_reminder_flags_default_to_enabled(db):
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is True
    assert chat.schedule_reminder_enabled is True


async def test_toggle_hw_reminder_off(db):
    await get_or_create_chat(CHAT_ID, "private")
    await update_chat_reminder_flags(CHAT_ID, hw_enabled=False)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is False
    assert chat.schedule_reminder_enabled is True


async def test_toggle_schedule_reminder_off(db):
    await get_or_create_chat(CHAT_ID, "private")
    await update_chat_reminder_flags(CHAT_ID, schedule_enabled=False)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is True
    assert chat.schedule_reminder_enabled is False


async def test_toggles_are_independent(db):
    """Disabling one reminder must not affect the other, in either direction."""
    await get_or_create_chat(CHAT_ID, "private")

    await update_chat_reminder_flags(CHAT_ID, hw_enabled=False)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is False
    assert chat.schedule_reminder_enabled is True

    await update_chat_reminder_flags(CHAT_ID, schedule_enabled=False)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is False
    assert chat.schedule_reminder_enabled is False

    await update_chat_reminder_flags(CHAT_ID, hw_enabled=True)
    chat = await get_or_create_chat(CHAT_ID, "private")
    assert chat.hw_reminder_enabled is True
    assert chat.schedule_reminder_enabled is False
