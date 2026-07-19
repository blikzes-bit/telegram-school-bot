import re
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_or_create_chat, update_chat_reminder_times, update_chat_reminder_flags, delete_chat
from keyboards.inline import get_settings_keyboard, get_cancel_keyboard
from keyboards.reply import get_main_menu

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь время текстом в формате `ЧЧ:ММ`."

class SettingStates(StatesGroup):
    waiting_for_hw_time = State()
    waiting_for_sch_time = State()
    waiting_for_reset_confirm = State()

def _status_label(enabled: bool) -> str:
    return "🟢 включено" if enabled else "🔴 отключено"

async def format_settings_message(chat_id: int) -> str:
    chat = await get_or_create_chat(chat_id, "private")
    return (
        "⚙️ **Настройки оповещений**\n\n"
        f"🔔 **Напоминание о домашнем задании** ({_status_label(chat.hw_reminder_enabled)}):\n"
        f"Бот присылает список ДЗ на завтра каждый день в **{chat.hw_reminder_time}**.\n\n"
        f"🎒 **Напоминание о портфеле** ({_status_label(chat.schedule_reminder_enabled)}):\n"
        f"Бот присылает расписание на завтра каждый день в **{chat.schedule_reminder_time}**.\n\n"
        f"Вы можете изменить это время или включить/отключить напоминания с помощью кнопок ниже:"
    )

async def get_settings_keyboard_for_chat(chat_id: int):
    chat = await get_or_create_chat(chat_id, "private")
    return get_settings_keyboard(chat.hw_reminder_enabled, chat.schedule_reminder_enabled)

@router.message(F.text == "⏰ Напоминания")
@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message, state: FSMContext):
    await state.clear()
    text = await format_settings_message(message.chat.id)
    kb = await get_settings_keyboard_for_chat(message.chat.id)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "set_hw_rem")
async def edit_hw_reminder(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingStates.waiting_for_hw_time)
    await callback.message.edit_text(
        "🔔 **Настройка напоминания о ДЗ**\n\n"
        "Введите время в 24-часовом формате `ЧЧ:ММ` (например, `17:30` или `20:00`), когда вы хотите получать напоминание о домашнем задании на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "set_sch_rem")
async def edit_sch_reminder(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingStates.waiting_for_sch_time)
    await callback.message.edit_text(
        "🎒 **Настройка напоминания о портфеле**\n\n"
        "Введите время в 24-часовом формате `ЧЧ:ММ` (например, `19:30` или `21:15`), когда вы хотите получать расписание уроков на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="Markdown"
    )
    await callback.answer()

TIME_FORMAT = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")

@router.message(SettingStates.waiting_for_hw_time, F.text)
async def process_hw_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате `ЧЧ:ММ` (например, `18:00`):")
        return
        
    # Standardize time string (e.g. 8:00 -> 08:00)
    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"
    
    await update_chat_reminder_times(message.chat.id, hw_time=std_time)
    await state.clear()
    
    await message.answer(f"✅ Время напоминания о ДЗ успешно изменено на **{std_time}**!", reply_markup=get_main_menu(), parse_mode="Markdown")

    # Show settings menu again
    settings_text = await format_settings_message(message.chat.id)
    kb = await get_settings_keyboard_for_chat(message.chat.id)
    await message.answer(settings_text, reply_markup=kb, parse_mode="Markdown")

@router.message(SettingStates.waiting_for_sch_time, F.text)
async def process_sch_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате `ЧЧ:ММ` (например, `20:30`):")
        return
        
    # Standardize time string
    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"
    
    await update_chat_reminder_times(message.chat.id, schedule_time=std_time)
    await state.clear()
    
    await message.answer(f"✅ Время напоминания о портфеле успешно изменено на **{std_time}**!", reply_markup=get_main_menu(), parse_mode="Markdown")

    # Show settings menu again
    settings_text = await format_settings_message(message.chat.id)
    kb = await get_settings_keyboard_for_chat(message.chat.id)
    await message.answer(settings_text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "set_cancel")
async def cancel_settings_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = await format_settings_message(callback.message.chat.id)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "toggle_hw_rem")
async def toggle_hw_reminder(callback: CallbackQuery, state: FSMContext):
    chat = await get_or_create_chat(callback.message.chat.id, "private")
    await update_chat_reminder_flags(chat.chat_id, hw_enabled=not chat.hw_reminder_enabled)

    text = await format_settings_message(callback.message.chat.id)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "toggle_sch_rem")
async def toggle_sch_reminder(callback: CallbackQuery, state: FSMContext):
    chat = await get_or_create_chat(callback.message.chat.id, "private")
    await update_chat_reminder_flags(chat.chat_id, schedule_enabled=not chat.schedule_reminder_enabled)

    text = await format_settings_message(callback.message.chat.id)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

# RESET ALL SETTINGS
@router.callback_query(F.data == "set_reset_all")
async def confirm_reset(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingStates.waiting_for_reset_confirm)
    
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, удалить всё", callback_data="set_reset_confirm"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="set_cancel")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ **ВНИМАНИЕ!** ⚠️\n\n"
        "Вы действительно хотите сбросить все настройки?\n"
        "Это безвозвратно удалит ваше расписание, время уроков и все записанные домашние задания!",
        reply_markup=confirm_kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(SettingStates.waiting_for_reset_confirm, F.data == "set_reset_confirm")
async def execute_reset(callback: CallbackQuery, state: FSMContext):
    chat_id = callback.message.chat.id
    
    # Delete chat entry from database (cascade deletes everything else)
    await delete_chat(chat_id)
    
    await state.clear()
    await callback.answer("Все данные удалены.")
    
    # Re-trigger start flow (will show onboarding start keyboard)
    await callback.message.edit_text(
        "👋 Все данные этого чата были успешно удалены.\n"
        "Бот сброшен к первоначальному состоянию. Нажмите кнопку ниже, чтобы начать новую настройку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку", callback_data="ob_start")]
        ])
    )


# --- Fallback: non-text content while awaiting a reminder time ---
async def settings_non_text(message: Message):
    await message.answer(NON_TEXT_HINT, parse_mode="Markdown")


router.message.register(
    settings_non_text,
    StateFilter(
        SettingStates.waiting_for_hw_time,
        SettingStates.waiting_for_sch_time,
    ),
)
