import re
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from database.db import get_or_create_chat, update_chat_reminder_times, update_chat_reminder_flags, delete_chat
from keyboards.inline import get_settings_keyboard, get_cancel_keyboard
from keyboards.reply import get_main_menu
from middleware.access import require_admin
from utils import safe_edit_text

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь время текстом в формате <code>ЧЧ:ММ</code>."


class SettingStates(StatesGroup):
    waiting_for_hw_time = State()
    waiting_for_sch_time = State()
    waiting_for_reset_confirm = State()


def _status_label(enabled: bool) -> str:
    return "🟢 включено" if enabled else "🔴 отключено"


async def format_settings_message(chat_id: int, chat_type: str = "private") -> str:
    chat = await get_or_create_chat(chat_id, chat_type)
    return (
        "⚙️ <b>Настройки оповещений</b>\n\n"
        f"🔔 <b>Напоминание о домашнем задании</b> ({_status_label(chat.hw_reminder_enabled)}):\n"
        f"Бот присылает список ДЗ на завтра каждый день в <b>{chat.hw_reminder_time}</b>.\n\n"
        f"🎒 <b>Напоминание о портфеле</b> ({_status_label(chat.schedule_reminder_enabled)}):\n"
        f"Бот присылает расписание на завтра каждый день в <b>{chat.schedule_reminder_time}</b>.\n\n"
        f"Вы можете изменить это время или включить/отключить напоминания с помощью кнопок ниже:"
    )


async def get_settings_keyboard_for_chat(chat_id: int, chat_type: str = "private"):
    chat = await get_or_create_chat(chat_id, chat_type)
    return get_settings_keyboard(chat.hw_reminder_enabled, chat.schedule_reminder_enabled)


@router.message(F.text == "⏰ Напоминания")
@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message, state: FSMContext):
    await state.clear()
    text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "set_hw_rem")
async def edit_hw_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_hw_time)
    await safe_edit_text(
        callback.message,
        "🔔 <b>Настройка напоминания о ДЗ</b>\n\n"
        "Введите время в 24-часовом формате <code>ЧЧ:ММ</code> (например, <code>17:30</code> или <code>20:00</code>), "
        "когда вы хотите получать напоминание о домашнем задании на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "set_sch_rem")
async def edit_sch_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_sch_time)
    await safe_edit_text(
        callback.message,
        "🎒 <b>Настройка напоминания о портфеле</b>\n\n"
        "Введите время в 24-часовом формате <code>ЧЧ:ММ</code> (например, <code>19:30</code> или <code>21:15</code>), "
        "когда вы хотите получать расписание уроков на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML"
    )
    await callback.answer()


TIME_FORMAT = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")


@router.message(SettingStates.waiting_for_hw_time, F.text)
async def process_hw_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате <code>ЧЧ:ММ</code> (например, <code>18:00</code>):", parse_mode="HTML")
        return

    # Standardize time string (e.g. 8:00 -> 08:00)
    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"

    await update_chat_reminder_times(message.chat.id, hw_time=std_time)
    await state.clear()

    await message.answer(f"✅ Время напоминания о ДЗ успешно изменено на <b>{std_time}</b>!", reply_markup=get_main_menu(), parse_mode="HTML")

    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


@router.message(SettingStates.waiting_for_sch_time, F.text)
async def process_sch_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате <code>ЧЧ:ММ</code> (например, <code>20:30</code>):", parse_mode="HTML")
        return

    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"

    await update_chat_reminder_times(message.chat.id, schedule_time=std_time)
    await state.clear()

    await message.answer(f"✅ Время напоминания о портфеле успешно изменено на <b>{std_time}</b>!", reply_markup=get_main_menu(), parse_mode="HTML")

    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "set_cancel")
async def cancel_settings_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = await format_settings_message(callback.message.chat.id, callback.message.chat.type)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "toggle_hw_rem")
async def toggle_hw_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    await update_chat_reminder_flags(chat.chat_id, hw_enabled=not chat.hw_reminder_enabled)

    text = await format_settings_message(callback.message.chat.id, callback.message.chat.type)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "toggle_sch_rem")
async def toggle_sch_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    await update_chat_reminder_flags(chat.chat_id, schedule_enabled=not chat.schedule_reminder_enabled)

    text = await format_settings_message(callback.message.chat.id, callback.message.chat.type)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# RESET ALL SETTINGS
@router.callback_query(F.data == "set_reset_all")
async def confirm_reset(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_reset_confirm)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, удалить всё", callback_data="set_reset_confirm"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="set_cancel")
        ]
    ])

    await safe_edit_text(
        callback.message,
        "⚠️ <b>ВНИМАНИЕ!</b> ⚠️\n\n"
        "Вы действительно хотите сбросить все настройки?\n"
        "Это безвозвратно удалит ваше расписание, время уроков и все записанные домашние задания!",
        reply_markup=confirm_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(SettingStates.waiting_for_reset_confirm, F.data == "set_reset_confirm")
async def execute_reset(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    chat_id = callback.message.chat.id

    # Delete chat entry from database (cascade deletes everything else)
    await delete_chat(chat_id)

    await state.clear()
    await callback.answer("Все данные удалены.")

    # Re-trigger start flow (will show onboarding start keyboard)
    await safe_edit_text(
        callback.message,
        "👋 Все данные этого чата были успешно удалены.\n"
        "Бот сброшен к первоначальному состоянию. Нажмите кнопку ниже, чтобы начать новую настройку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку", callback_data="ob_start")]
        ])
    )
    # The persistent reply keyboard (main menu) is separate from the inline
    # keyboard above and would otherwise keep showing post-reset — remove it.
    removal_notice = await callback.message.answer(
        "Клавиатура сброшена.", reply_markup=ReplyKeyboardRemove()
    )
    try:
        await removal_notice.delete()
    except Exception:
        pass


# --- Fallback: non-text content while awaiting a reminder time ---
async def settings_non_text(message: Message):
    await message.answer(NON_TEXT_HINT, parse_mode="HTML")


router.message.register(
    settings_non_text,
    StateFilter(
        SettingStates.waiting_for_hw_time,
        SettingStates.waiting_for_sch_time,
    ),
)
