from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_menu() -> ReplyKeyboardMarkup:
    """
    Returns the persistent main menu keyboard.
    """
    keyboard = [
        [
            KeyboardButton(text="📚 Сегодня")
        ],
        [
            KeyboardButton(text="📅 Расписание"),
            KeyboardButton(text="📝 Домашнее задание")
        ],
        [
            KeyboardButton(text="🎯 Доп. занятия")
        ],
        [
            KeyboardButton(text="⏰ Напоминания"),
            KeyboardButton(text="⚙️ Настройки")
        ],
        [
            KeyboardButton(text="❓ Помощь")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        persistent=True,
        input_field_placeholder="Выберите пункт меню..."
    )
