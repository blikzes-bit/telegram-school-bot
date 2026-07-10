from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAYS_SHORT_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

def get_schedule_days_keyboard(current_day: int) -> InlineKeyboardMarkup:
    """
    Returns an inline keyboard with days of the week. The current day is highlighted.
    """
    buttons = []
    # Row for days
    day_row = []
    for i, short_name in enumerate(DAYS_SHORT_RU):
        text = f"• {short_name} •" if i == current_day else short_name
        day_row.append(InlineKeyboardButton(text=text, callback_data=f"sch_day:{i}"))
    buttons.append(day_row)
    
    # Row for management
    buttons.append([
        InlineKeyboardButton(text="✏️ Изменить уроки на этот день", callback_data=f"sch_edit:{current_day}"),
        InlineKeyboardButton(text="🕒 Настройка звонков", callback_data="sch_edit_times")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_homework_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add"),
                InlineKeyboardButton(text="🗄️ Архив (Выполненные)", callback_data="hw_archive")
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить список", callback_data="hw_list_active")
            ]
        ]
    )

def get_homework_action_keyboard(hw_id: int, is_archive: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if not is_archive:
        buttons.append(InlineKeyboardButton(text="✅ Выполнено", callback_data=f"hw_complete:{hw_id}"))
    else:
        buttons.append(InlineKeyboardButton(text="🔄 Вернуть в список", callback_data=f"hw_restore:{hw_id}"))
        
    buttons.append(InlineKeyboardButton(text="❌ Удалить", callback_data=f"hw_delete:{hw_id}"))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

def get_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔔 Напоминание о ДЗ", callback_data="set_hw_rem")
            ],
            [
                InlineKeyboardButton(text="🎒 Напоминание о портфеле", callback_data="set_sch_rem")
            ],
            [
                InlineKeyboardButton(text="⚙️ Сбросить все настройки", callback_data="set_reset_all")
            ]
        ]
    )

def get_cancel_keyboard(callback_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]
        ]
    )

def get_onboarding_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку", callback_data="ob_start")]
        ]
    )
