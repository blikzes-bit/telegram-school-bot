import asyncio
import datetime
import logging
import os
import pytz
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import (
    get_all_chats, get_homework, get_schedule, get_lesson_slots,
    update_last_hw_reminder_date, update_last_sch_reminder_date,
    claim_reminder_job, advance_reminder_job, get_reminder_job_chunks,
    set_chat_blocked, get_incomplete_homework_for_chats, get_schedule_for_chats,
    get_lesson_slots_for_chats,
)
from keyboards.inline import DAYS_RU
from config import TIMEZONE, HEARTBEAT_FILE
from utils import html_escape, split_message

logger = logging.getLogger(__name__)


def _touch_heartbeat():
    """
    Updates the heartbeat file's mtime once per tick so the Docker
    HEALTHCHECK can tell a hung/deadlocked event loop from a healthy one —
    the process can be "running" while its background job stopped ticking.
    """
    try:
        with open(HEARTBEAT_FILE, "a"):
            os.utime(HEARTBEAT_FILE, None)
    except OSError:
        logger.warning("Could not update heartbeat file %s", HEARTBEAT_FILE)

# Small delay between successive sends so a chat with many chunks — or a
# sweep across many chats — stays well under Telegram's per-chat/global rate
# limits. Not a full token-bucket; see README for the documented limitation.
SEND_THROTTLE_SECONDS = 0.05


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _send_job(bot: Bot, chat_id: int, job) -> bool:
    """
    Sends the remaining, not-yet-delivered chunks of an outbox ``job``,
    persisting progress after every chunk so a crash/restart resumes from
    exactly where it left off instead of resending already-delivered parts.

    Returns True once every chunk has been sent. Raises on a delivery error
    so the caller can decide how to react (retry-after wait, mark the chat
    blocked, or just leave the job for the next scheduler tick).
    """
    chunks = await get_reminder_job_chunks(job)
    for i in range(job.chunks_sent, len(chunks)):
        try:
            await bot.send_message(chat_id, chunks[i], parse_mode="HTML")
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 60))
            await bot.send_message(chat_id, chunks[i], parse_mode="HTML")

        await advance_reminder_job(job.id, i + 1, _now_iso(), done=(i + 1 == len(chunks)))
        if i + 1 < len(chunks):
            await asyncio.sleep(SEND_THROTTLE_SECONDS)
    return True


async def _send_reminder(bot: Bot, chat_id: int, kind: str, job_date: datetime.date, text: str) -> bool:
    """
    Claims (or resumes) the outbox job for ``(chat_id, kind, job_date)`` and
    delivers it. Returns True when fully delivered (including "already
    delivered by an earlier attempt"), False when nothing could be sent this
    tick (transient error, or another run is actively claiming the job) —
    the scheduler will simply try again on its next pass.
    """
    chunks = split_message(text)
    job = await claim_reminder_job(chat_id, kind, job_date, chunks, _now_iso())
    if job is None:
        # Either already fully delivered, or another (possibly stale) run
        # currently owns this job — either way, don't send anything now.
        return True

    try:
        await _send_job(bot, chat_id, job)
    except TelegramForbiddenError:
        logger.warning(f"Chat {chat_id} blocked/kicked the bot — suppressing further reminders.")
        await set_chat_blocked(chat_id, True)
        return False
    except TelegramAPIError as e:
        logger.warning(f"Telegram API error sending {kind} reminder to {chat_id}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Transient error sending {kind} reminder to {chat_id}: {e}")
        return False
    return True


def _render_homework_list(homeworks) -> str:
    lines = ""
    for i, hw in enumerate(homeworks, 1):
        safe_sub = html_escape(hw.subject_name)
        safe_desc = html_escape(hw.description)
        lines += f"{i}️⃣ <b>{safe_sub}</b>:\n   <i>{safe_desc}</i>\n\n"
    return lines


async def send_hw_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo,
    incomplete_homework=None, tomorrow_schedule=None,
) -> bool:
    """
    Sends the homework reminder: homework due tomorrow, plus a separate block
    of still-uncompleted homework whose due date has already passed.

    ``incomplete_homework``/``tomorrow_schedule`` let a caller that already
    batch-fetched data for many chats at once (see check_and_send_reminders)
    pass it in directly, avoiding a redundant per-chat query. When omitted
    (e.g. calling this function directly, as the tests do), it's fetched here
    exactly as before — the batching is purely an optimization, not a
    behavior or API change for existing callers.

    Returns ``True`` when the reminder was fully handled (either delivered, or
    there was legitimately nothing to send) so the caller may stamp the date.
    Returns ``False`` only on a delivery error, so the scheduler will retry on
    its next run instead of marking today as done.
    """
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    if incomplete_homework is None:
        incomplete_homework = await get_homework(chat_id, is_completed=False)
    homeworks = [hw for hw in incomplete_homework if hw.due_date == tomorrow]
    overdue = sorted((hw for hw in incomplete_homework if hw.due_date < today), key=lambda hw: hw.due_date)

    blocks = []

    if homeworks:
        block = (
            f"🔔 <b>Домашнее задание на завтра ({tomorrow.strftime('%d.%m')}):</b>\n\n"
        )
        block += _render_homework_list(homeworks)
        blocks.append(block)
    else:
        if tomorrow_schedule is None:
            tomorrow_schedule = await get_schedule(chat_id, tomorrow.weekday())
        if tomorrow_schedule:
            blocks.append(
                "🔔 <b>Домашнее задание на завтра:</b>\n\n"
                "🎉 Отличные новости! На завтра нет записанных домашних заданий."
            )
        # No lessons tomorrow: nothing meaningful to report for this block.

    if overdue:
        block = "⚠️ <b>Просроченные задания:</b>\n\n"
        block += _render_homework_list(overdue)
        blocks.append(block)

    if not blocks:
        # Nothing due tomorrow, no lessons tomorrow, no overdue items: sending
        # anything would be an empty, meaningless notification.
        return True

    text = "\n\n".join(block.rstrip("\n") for block in blocks)
    return await _send_reminder(bot, chat_id, "hw", today, text)


async def send_schedule_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo,
    schedule_items=None, slots=None,
) -> bool:
    """
    Sends the "pack your bag" schedule reminder for tomorrow.

    ``schedule_items``/``slots`` follow the same optional-batch-data contract
    as :func:`send_hw_reminder`. Same return contract as :func:`send_hw_reminder`.
    """
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()

    if schedule_items is None:
        schedule_items = await get_schedule(chat_id, tomorrow_weekday)
    if slots is None:
        slots = await get_lesson_slots(chat_id)

    if not schedule_items or not slots:
        # Nothing scheduled for tomorrow: legitimately nothing to send.
        return True

    day_name = DAYS_RU[tomorrow_weekday]
    sched_map = {item.lesson_number: item.subject_name for item in schedule_items}

    lines = []
    for slot in slots:
        num = slot.lesson_number
        start = slot.start_time
        end = slot.end_time
        subject = sched_map.get(num)
        if subject:
            safe_sub = html_escape(subject)
            lines.append(f"{num}️⃣ <code>{start} - {end}</code> | 📘 <b>{safe_sub}</b>")

    if not lines:
        # schedule_items/slots exist, but none of the schedule's lesson
        # numbers match any configured lesson slot — there is nothing real
        # to show, so don't send a header-only, contentless notification.
        return True

    text = f"🎒 <b>Пора собирать портфель!</b>\n\nРасписание на завтра (<b>{day_name}</b>):\n\n" + "\n".join(lines)
    return await _send_reminder(bot, chat_id, "sched", today, text)


def _due_now(chat, reminder_time_attr: str, enabled_attr: str, last_date_attr: str, current_hour_min, today) -> bool:
    if not getattr(chat, enabled_attr):
        return False
    if getattr(chat, last_date_attr) == today:
        return False
    h, m = map(int, getattr(chat, reminder_time_attr).split(":"))
    return current_hour_min >= (h, m)


async def check_and_send_reminders(bot: Bot):
    _touch_heartbeat()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    today = now.date()
    tomorrow_weekday = (today + datetime.timedelta(days=1)).weekday()
    current_hour_min = (now.hour, now.minute)

    chats = [c for c in await get_all_chats() if c.is_onboarded]

    # Figure out which chats need which reminder this tick BEFORE touching the
    # DB again, then fetch homework/schedule/slots for all of them in three
    # queries total instead of up to four queries per chat.
    hw_due_ids, sch_due_ids = [], []
    for chat in chats:
        try:
            if _due_now(chat, "hw_reminder_time", "hw_reminder_enabled", "last_hw_reminder_date", current_hour_min, today):
                hw_due_ids.append(chat.chat_id)
            if _due_now(chat, "schedule_reminder_time", "schedule_reminder_enabled", "last_sch_reminder_date", current_hour_min, today):
                sch_due_ids.append(chat.chat_id)
        except (ValueError, AttributeError) as e:
            logger.exception(f"Bad reminder-time config for chat {chat.chat_id}: {e}")

    if not hw_due_ids and not sch_due_ids:
        return

    # Tomorrow's schedule is used both by the HW reminder (to tell "no
    # homework" apart from "no lessons at all") and by the schedule reminder
    # itself, so fetch it once for the union of both chat sets.
    schedule_chat_ids = list(set(hw_due_ids) | set(sch_due_ids))
    homework_by_chat = await get_incomplete_homework_for_chats(hw_due_ids)
    schedule_by_chat = await get_schedule_for_chats(schedule_chat_ids, tomorrow_weekday)
    slots_by_chat = await get_lesson_slots_for_chats(sch_due_ids)

    for chat in chats:
        # Isolate each chat: a failure for one must not abort the whole sweep.
        try:
            if chat.chat_id in hw_due_ids:
                logger.info(f"Triggering HW reminder for chat {chat.chat_id}")
                handled = await send_hw_reminder(
                    bot, chat.chat_id, tz,
                    incomplete_homework=homework_by_chat.get(chat.chat_id, []),
                    tomorrow_schedule=schedule_by_chat.get(chat.chat_id, []),
                )
                # Only stamp the date when the reminder was actually handled;
                # a delivery error leaves it unset so we retry next run.
                if handled:
                    await update_last_hw_reminder_date(chat.chat_id, today)

            if chat.chat_id in sch_due_ids:
                logger.info(f"Triggering schedule reminder for chat {chat.chat_id}")
                handled = await send_schedule_reminder(
                    bot, chat.chat_id, tz,
                    schedule_items=schedule_by_chat.get(chat.chat_id, []),
                    slots=slots_by_chat.get(chat.chat_id, []),
                )
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
