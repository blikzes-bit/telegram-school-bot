import datetime
import logging
import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import (
    get_all_chats, get_homework_due_on, get_overdue_homework, get_schedule,
    get_lesson_slots, update_last_hw_reminder_date, update_last_sch_reminder_date
)
from keyboards.inline import DAYS_RU
from config import TIMEZONE
from utils import escape_markdown, split_message

logger = logging.getLogger(__name__)


async def _send_chunks(bot: Bot, chat_id: int, text: str):
    """
    Sends ``text`` to a chat, splitting it into several messages when it
    exceeds Telegram's 4096-character limit. Any Telegram error propagates to
    the caller so it can decide whether to retry later.
    """
    for chunk in split_message(text):
        await bot.send_message(chat_id, chunk, parse_mode="Markdown")


def _render_homework_list(homeworks) -> str:
    lines = ""
    for i, hw in enumerate(homeworks, 1):
        safe_sub = escape_markdown(hw.subject_name)
        safe_desc = escape_markdown(hw.description)
        lines += f"{i}️⃣ **{safe_sub}**:\n   _{safe_desc}_\n\n"
    return lines


async def send_hw_reminder(bot: Bot, chat_id: int, tz: pytz.BaseTzInfo) -> bool:
    """
    Sends the homework reminder: homework due tomorrow, plus a separate block
    of still-uncompleted homework whose due date has already passed.

    Returns ``True`` when the reminder was fully handled (either delivered, or
    there was legitimately nothing to send) so the caller may stamp the date.
    Returns ``False`` only on a Telegram delivery error, so the scheduler will
    retry on its next run instead of marking today as done.
    """
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    homeworks = await get_homework_due_on(chat_id, tomorrow)
    overdue = await get_overdue_homework(chat_id, today)

    blocks = []

    if homeworks:
        block = (
            f"🔔 **Домашнее задание на завтра ({tomorrow.strftime('%d.%m')}):**\n\n"
        )
        block += _render_homework_list(homeworks)
        blocks.append(block)
    else:
        tomorrow_schedule = await get_schedule(chat_id, tomorrow.weekday())
        if tomorrow_schedule:
            blocks.append(
                "🔔 **Домашнее задание на завтра:**\n\n"
                "🎉 Отличные новости! На завтра нет записанных домашних заданий."
            )
        # No lessons tomorrow: nothing meaningful to report for this block.

    if overdue:
        block = "⚠️ **Просроченные задания:**\n\n"
        block += _render_homework_list(overdue)
        blocks.append(block)

    if not blocks:
        # Nothing due tomorrow, no lessons tomorrow, no overdue items: sending
        # anything would be an empty, meaningless notification.
        return True

    text = "\n\n".join(block.rstrip("\n") for block in blocks)

    try:
        await _send_chunks(bot, chat_id, text)
    except TelegramAPIError as e:
        logger.warning(f"Failed to send HW reminder to {chat_id}: {e}")
        return False
    return True


async def send_schedule_reminder(bot: Bot, chat_id: int, tz: pytz.BaseTzInfo) -> bool:
    """
    Sends the "pack your bag" schedule reminder for tomorrow.

    Same return contract as :func:`send_hw_reminder`.
    """
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()

    schedule_items = await get_schedule(chat_id, tomorrow_weekday)
    slots = await get_lesson_slots(chat_id)

    if not schedule_items or not slots:
        # Nothing scheduled for tomorrow: legitimately nothing to send.
        return True

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
        await _send_chunks(bot, chat_id, text)
    except TelegramAPIError as e:
        logger.warning(f"Failed to send schedule reminder to {chat_id}: {e}")
        return False
    return True


async def check_and_send_reminders(bot: Bot):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    today = now.date()
    current_hour_min = (now.hour, now.minute)

    chats = await get_all_chats()
    for chat in chats:
        # Isolate each chat: a failure for one must not abort the whole sweep.
        try:
            if not chat.is_onboarded:
                continue

            # HW reminder: trigger when current time >= scheduled time and not
            # yet successfully handled today.
            hw_h, hw_m = map(int, chat.hw_reminder_time.split(":"))
            if (
                chat.hw_reminder_enabled
                and current_hour_min >= (hw_h, hw_m)
                and chat.last_hw_reminder_date != today
            ):
                logger.info(f"Triggering HW reminder for chat {chat.chat_id}")
                handled = await send_hw_reminder(bot, chat.chat_id, tz)
                # Only stamp the date when the reminder was actually handled;
                # a Telegram error leaves it unset so we retry next run.
                if handled:
                    await update_last_hw_reminder_date(chat.chat_id, today)

            # Schedule reminder: same trigger + success semantics.
            sch_h, sch_m = map(int, chat.schedule_reminder_time.split(":"))
            if (
                chat.schedule_reminder_enabled
                and current_hour_min >= (sch_h, sch_m)
                and chat.last_sch_reminder_date != today
            ):
                logger.info(f"Triggering schedule reminder for chat {chat.chat_id}")
                handled = await send_schedule_reminder(bot, chat.chat_id, tz)
                if handled:
                    await update_last_sch_reminder_date(chat.chat_id, today)
        except Exception as e:
            # Bad stored time, DB hiccup, unexpected error — log and continue.
            logger.exception(f"Reminder processing failed for chat {chat.chat_id}: {e}")
            continue


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
