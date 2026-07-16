import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_or_create_chat, update_chat_reminder_times, delete_chat
from keyboards.inline import get_settings_keyboard, get_cancel_keyboard
from keyboards.reply import get_main_menu

router = Router()

class SettingStates(StatesGroup):
    waiting_for_hw_time = State()
    waiting_for_sch_time = State()
    waiting_for_reset_confirm = State()

async def format_settings_message(chat_id: int) -> str:
    chat = await get_or_create_chat(chat_id, "private")
    return (
        "⚙️ **Настройки оповещений**\n\n"
        f"🔔 **Напоминание о домашнем задании**:\n"
        f"Бот присылает список ДЗ на завтра каждый день в **{chat.hw_reminder_time}**.\n\n"
        f"🎒 **Напоминание о портфеле**:\n"
        f"Бот присылает расписание на завтра каждый день в **{chat.schedule_reminder_time}**.\n\n"
        f"Вы можете изменить это время с помощью кнопок ниже:"
    )

@router.message(F.text == "⏰ Напоминания")
@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message, state: FSMContext):
    await state.clear()
    text = await format_settings_message(message.chat.id)
    kb = get_settings_keyboard()
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

@router.message(SettingStates.waiting_for_hw_time)
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
    await message.answer(settings_text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")

@router.message(SettingStates.waiting_for_sch_time)
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
    await message.answer(settings_text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "set_cancel")
async def cancel_settings_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = await format_settings_message(callback.message.chat.id)
    kb = get_settings_keyboard()
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
