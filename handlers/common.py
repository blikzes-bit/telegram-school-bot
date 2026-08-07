from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from database.db import get_or_create_chat
from keyboards.reply import main_menu_for
from keyboards.inline import get_onboarding_start_keyboard
from services import profiles

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
            "Сначала один вопрос: как ты будешь этим пользоваться — для себя, "
            "для класса или для занятий с репетитором. От ответа зависит, что "
            "нужно будет настроить (иногда — вообще ничего).",
            reply_markup=get_onboarding_start_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "👋 <b>С возвращением!</b>\n\n"
            "Чем я могу помочь тебе сегодня?\n"
            "Используй кнопки меню ниже для управления расписанием и домашним заданием.",
            reply_markup=await main_menu_for(message.chat.id, message.chat.type),
            parse_mode="HTML"
        )

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    """Short, plain-words help for *this* chat.

    Deliberately not an encyclopedia: it lists only the sections this chat
    actually has (a tutor chat has no timetable, a diary has no roles), in the
    order they appear in the menu, one line each. The full documentation lives in
    the README — a help screen nobody finishes reading helps nobody.
    """
    await state.clear()
    chat = await get_or_create_chat(message.chat.id, message.chat.type)
    features = profiles.features_for(chat)
    profile = profiles.resolve(chat)

    lines = [
        "📓 <b>Что я умею</b>",
        "",
        f"Этот чат сейчас в режиме: <b>{profiles.PROFILE_LABELS[profile]}</b>.",
        "",
        "📚 <b>Сегодня</b> — что сегодня: уроки, домашка, занятия.",
    ]
    if features.school_schedule:
        lines.append(
            "📅 <b>Расписание</b> — обычное расписание на каждый день недели. "
            "Там же — изменения на конкретную дату: отменить урок, заменить "
            "предмет, поставить свободный день или каникулы."
        )
    if features.homework:
        lines.append(
            "📝 <b>Домашнее задание</b> — записать, что задали, отметить "
            "выполненным, приложить фото или файл."
        )
    if features.extra_activities:
        lines.append(
            "🎯 <b>Доп. занятия</b> — кружки, репетиторы, секции: день, время, место."
        )
    if features.payments:
        lines.append(
            "💳 <b>Оплата</b> — за что и когда платить. Напомню заранее."
        )
    lines += [
        "⏰ <b>Напоминания</b> — что и во сколько присылать. Есть тихие часы.",
        "⚙️ <b>Настройки</b> — режим чата, часовой пояс, кто что может менять, "
        "история изменений, резервная копия.",
        "🌐 <b>Приложение</b> (/web) — то же самое, но экранами: удобнее вводить "
        "и смотреть.",
        "",
        "💡 Если что-то зависло — напиши <code>/start</code>.",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.", reply_markup=await main_menu_for(message.chat.id, message.chat.type))
        return
        
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=await main_menu_for(message.chat.id, message.chat.type))
