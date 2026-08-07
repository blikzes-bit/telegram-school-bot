"""The persistent main menu.

The menu follows the chat's profile: a tutor chat has no school timetable, so it
gets no "📅 Расписание" button, and only a tutor chat shows "💳 Оплата". A button
that leads to an empty screen is worse than no button, especially for the
youngest users, who read the menu as the list of things this bot can do.

``get_main_menu()`` with no argument keeps the full menu — that is what the
tests and any caller without a chat row in hand get, and it matches the
behaviour that existed before the menu became profile-aware.
"""
from typing import Optional

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from services import profiles


def get_main_menu(chat: Optional[object] = None) -> ReplyKeyboardMarkup:
    """The persistent main menu, tailored to what this chat actually uses."""
    features = profiles.features_for(chat) if chat is not None else None

    def uses(flag: str) -> bool:
        return True if features is None else bool(getattr(features, flag))

    keyboard = [[KeyboardButton(text="📚 Сегодня")]]

    second_row = []
    if uses("school_schedule"):
        second_row.append(KeyboardButton(text="📅 Расписание"))
    if uses("homework"):
        second_row.append(KeyboardButton(text="📝 Домашнее задание"))
    if second_row:
        keyboard.append(second_row)

    third_row = []
    if uses("extra_activities"):
        third_row.append(KeyboardButton(text="🎯 Доп. занятия"))
    if uses("payments"):
        third_row.append(KeyboardButton(text="💳 Оплата"))
    if third_row:
        keyboard.append(third_row)

    keyboard.append([
        KeyboardButton(text="⏰ Напоминания"),
        KeyboardButton(text="⚙️ Настройки"),
    ])
    keyboard.append([KeyboardButton(text="❓ Помощь")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        persistent=True,
        input_field_placeholder="Выберите пункт меню...",
    )


async def main_menu_for(chat_id: int, chat_type: str) -> ReplyKeyboardMarkup:
    """:func:`get_main_menu` for a chat id, loading the row it needs.

    Used by every handler that (re)draws the menu, so the menu a user sees never
    depends on *which* action happened to redraw it last.
    """
    from database.db import get_or_create_chat

    chat = await get_or_create_chat(chat_id, chat_type)
    return get_main_menu(chat)
