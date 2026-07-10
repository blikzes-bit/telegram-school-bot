import datetime
import logging
import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import (
    get_all_chats, get_homework_due_on, get_schedule, get_lesson_slots,
    update_last_hw_reminder_date, update_last_sch_reminder_date
)
from keyboards.inline import DAYS_RU
from config import TIMEZONE
from utils import escape_markdown

logger = logging.getLogger(__name__)

async def send_hw_reminder(bot: Bot, chat_id: int, tz: pytz.BaseTzInfo):
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    homeworks = await get_homework_due_on(chat_id, tomorrow)

    if not homeworks:
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
                await update_last_hw_reminder_date(chat_id, today)
            except TelegramAPIError as e:
                logger.warning(f"Failed to send HW reminder to {chat_id}: {e}")
        return

    text = f"🔔 **Напоминание о домашнем задании на завтра ({tomorrow.strftime('%d.%m')}):**\n\n"
    for i, hw in enumerate(homeworks, 1):
        safe_sub = escape_markdown(hw.subject_name)
        safe_desc = escape_markdown(hw.description)
        text += f"{i}️⃣ **{safe_sub}**:\n   _{safe_desc}_\n\n"

    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
        await update_last_hw_reminder_date(chat_id, today)
    except TelegramAPIError as e:
        logger.warning(f"Failed to send HW reminder to {chat_id}: {e}")

async def send_schedule_reminder(bot: Bot, chat_id: int, tz: pytz.BaseTzInfo):
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()

    schedule_items = await get_schedule(chat_id, tomorrow_weekday)
    slots = await get_lesson_slots(chat_id)

    if not schedule_items or not slots:
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
            safe_sub = escape_markdown(subject)
            text += f"{num}️⃣ `{start} - {end}` | 📘 **{safe_sub}**\n"

    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
        await update_last_sch_reminder_date(chat_id, today)
    except TelegramAPIError as e:
        logger.warning(f"Failed to send schedule reminder to {chat_id}: {e}")

async def check_and_send_reminders(bot: Bot):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    today = now.date()
    current_time_str = now.strftime("%H:%M")
    current_hour_min = (now.hour, now.minute)

    chats = await get_all_chats()
    for chat in chats:
        if not chat.is_onboarded:
            continue

        # Check HW reminder: trigger if current time >= scheduled time AND not yet sent today
        hw_h, hw_m = map(int, chat.hw_reminder_time.split(":"))
        if (current_hour_min >= (hw_h, hw_m)) and chat.last_hw_reminder_date != today:
            logger.info(f"Triggering HW reminder for chat {chat.chat_id}")
            await send_hw_reminder(bot, chat.chat_id, tz)

        # Check schedule reminder: trigger if current time >= scheduled time AND not yet sent today
        sch_h, sch_m = map(int, chat.schedule_reminder_time.split(":"))
        if (current_hour_min >= (sch_h, sch_m)) and chat.last_sch_reminder_date != today:
            logger.info(f"Triggering schedule reminder for chat {chat.chat_id}")
            await send_schedule_reminder(bot, chat.chat_id, tz)

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
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
