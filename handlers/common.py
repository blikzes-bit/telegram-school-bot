from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from database.db import get_or_create_chat
from keyboards.reply import get_main_menu
from keyboards.inline import get_onboarding_start_keyboard

router = Router()

@router.message(CommandStart())
@router.message(F.text == "🚀 Начать настройку")
async def cmd_start(message: Message, state: FSMContext):
    # Clear any active states
    await state.clear()
    
    # Register/get the chat
    chat = await get_or_create_chat(message.chat.id, message.chat.type)
    
    if not chat.is_onboarded:
        await message.answer(
            f"👋 **Привет!**\n\n"
            f"Я твой личный школьный помощник-тетрадь 📓.\n"
            f"Я помогу тебе следить за расписанием, записывать домашнее задание и буду "
            f"присылать напоминания о завтрашних уроках и домашке, чтобы ты ничего не забыл.\n\n"
            f"Для начала работы нам нужно сделать быструю настройку: ввести время звонков и расписание уроков.",
            reply_markup=get_onboarding_start_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "👋 **С возвращением!**\n\n"
            "Чем я могу помочь тебе сегодня?\n"
            "Используй кнопки меню ниже для управления расписанием и домашним заданием.",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📓 **Школьный Помощник** — справка по командам и функциям:\n\n"
        "📅 **Расписание**: Показывает расписание на выбранный день недели и время уроков. "
        "Там же можно изменить предмет для любого урока или настроить время звонков.\n\n"
        "📝 **Домашнее задание**: Позволяет записывать новые задания по предметам с указанием даты "
        "сдачи, а также отмечать выполненные задания и просматривать архив.\n\n"
        "⏰ **Напоминания**: Настройка времени ежедневных уведомлений:\n"
        "• *Напоминание о ДЗ* — бот пришлет список невыполненных ДЗ на завтра.\n"
        "• *Напоминание о портфеле* — бот пришлет расписание на завтра, чтобы собрать портфель.\n\n"
        "⚙️ **Настройки**: Смена времени напоминаний или полный сброс настроек бота.\n\n"
        "💡 Если бот завис, напиши `/start` для возврата в главное меню.",
        parse_mode="Markdown"
    )

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.", reply_markup=get_main_menu())
        return
        
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())
