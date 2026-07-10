import datetime
import logging
import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import get_all_chats, get_homework_due_on, get_schedule, get_lesson_slots
from keyboards.inline import DAYS_RU
from config import TIMEZONE

logger = logging.getLogger(__name__)

async def send_hw_reminder(bot: Bot, chat_id: int, timezone: pytz.BaseTzInfo):
    today = datetime.datetime.now(timezone).date()
    tomorrow = today + datetime.timedelta(days=1)
    
    # We fetch homework due tomorrow that is not completed
    homeworks = await get_homework_due_on(chat_id, tomorrow)
    
    if not homeworks:
        # User specified they want reminders of homework. 
        # If there's no homework, we don't necessarily need to spam them, 
        # or we can send a friendly note. Let's send a quiet friendly note 
        # so they know the bot is working, but only if they have lessons tomorrow.
        tomorrow_weekday = tomorrow.weekday()
        tomorrow_schedule = await get_schedule(chat_id, tomorrow_weekday)
        if tomorrow_schedule:
            try:
                await bot.send_message(
                    chat_id,
                    "🔔 **Напоминание о ДЗ на завтра:**\n\n"
                    "🎉 Отличные новости! На завтра нет записанных домашних заданий.",
                    parse_mode="Markdown"
                )
            except TelegramAPIError as e:
                logger.warning(f"Failed to send HW reminder to {chat_id}: {e}")
        return

    text = f"🔔 **Напоминание о домашнем задании на завтра ({tomorrow.strftime('%d.%m')}):**\n\n"
    for i, hw in enumerate(homeworks, 1):
        text += f"{i}️⃣ **{hw.subject_name}**:\n   _{hw.description}_\n\n"
        
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
    except TelegramAPIError as e:
        logger.warning(f"Failed to send HW reminder to {chat_id}: {e}")

async def send_schedule_reminder(bot: Bot, chat_id: int, timezone: pytz.BaseTzInfo):
    today = datetime.datetime.now(timezone).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()
    
    # We skip Sunday reminder (usually sent on Saturday evening) if there are no Sunday lessons,
    # or just show schedule if lessons exist.
    schedule_items = await get_schedule(chat_id, tomorrow_weekday)
    slots = await get_lesson_slots(chat_id)
    
    if not schedule_items or not slots:
        # If no lessons tomorrow, we don't send a schedule reminder (user can relax!)
        return
        
    day_name = DAYS_RU[tomorrow_weekday]
    text = f"🎒 **Пора собирать портфель!**\n\nРасписание на завтра (**{day_name}**):\n\n"
    
    sched_map = {item.lesson_number: item.subject_name for item in schedule_items}
    
    for slot in slots:
        num = slot.lesson_number
        start = slot.start_time
        end = slot.end_time
        subject = sched_map.get(num)
        if subject:
            text += f"{num}️⃣ `{start} - {end}` | 📘 **{subject}**\n"
            
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
    except TelegramAPIError as e:
        logger.warning(f"Failed to send schedule reminder to {chat_id}: {e}")

async def check_and_send_reminders(bot: Bot):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    current_time_str = now.strftime("%H:%M")
    
    chats = await get_all_chats()
    for chat in chats:
        if not chat.is_onboarded:
            continue
            
        # Check Homework Reminder Time
        if chat.hw_reminder_time == current_time_str:
            logger.info(f"Triggering HW reminder for chat {chat.chat_id}")
            await send_hw_reminder(bot, chat.chat_id, tz)
            
        # Check Schedule Reminder Time
        if chat.schedule_reminder_time == current_time_str:
            logger.info(f"Triggering schedule reminder for chat {chat.chat_id}")
            await send_schedule_reminder(bot, chat.chat_id, tz)

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Check reminders every minute at 00 seconds
    scheduler.add_job(
        check_and_send_reminders,
        "cron",
        minute="*",
        second="0",
        args=[bot]
    )
    scheduler.start()
    logger.info("Background scheduler started successfully.")
    return scheduler
