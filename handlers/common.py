from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
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
            "👋 <b>Привет!</b>\n\n"
            "Я твой личный школьный помощник-тетрадь 📓.\n"
            "Я помогу тебе следить за расписанием, записывать домашнее задание и буду "
            "присылать напоминания о завтрашних уроках и домашке, чтобы ты ничего не забыл.\n\n"
            "Для начала работы нам нужно сделать быструю настройку: ввести время звонков и расписание уроков.",
            reply_markup=get_onboarding_start_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "👋 <b>С возвращением!</b>\n\n"
            "Чем я могу помочь тебе сегодня?\n"
            "Используй кнопки меню ниже для управления расписанием и домашним заданием.",
            reply_markup=get_main_menu(),
            parse_mode="HTML"
        )

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📓 <b>Школьный Помощник</b> — справка по командам и функциям:\n\n"
        "📅 <b>Расписание</b>: Показывает расписание на выбранный день недели и время уроков. "
        "Там же можно изменить предмет для любого урока или настроить время звонков "
        "(в группе — только администраторам).\n\n"
        "📝 <b>Домашнее задание</b>: Позволяет записывать новые задания по предметам с указанием даты "
        "сдачи, а также отмечать выполненные задания и просматривать архив.\n\n"
        "⏰ <b>Напоминания</b>: Настройка времени ежедневных уведомлений:\n"
        "• <i>Напоминание о ДЗ</i> — бот пришлет список невыполненных ДЗ на завтра.\n"
        "• <i>Напоминание о портфеле</i> — бот пришлет расписание на завтра, чтобы собрать портфель.\n\n"
        "⚙️ <b>Настройки</b>: Смена времени напоминаний или полный сброс настроек бота "
        "(в группе — только администраторам).\n\n"
        "💡 Если бот завис, напиши <code>/start</code> для возврата в главное меню.",
        parse_mode="HTML"
    )

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.", reply_markup=get_main_menu())
        return
        
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())
