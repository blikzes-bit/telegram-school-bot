import asyncio
import datetime
import logging
import os
from collections import defaultdict
from typing import Optional

import pytz
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.db import (
    get_all_chats, get_homework,
    update_last_hw_reminder_date, update_last_sch_reminder_date,
    update_last_duetoday_reminder_date, update_last_changes_reminder_date,
    claim_reminder_job, advance_reminder_job, get_reminder_job_chunks,
    cleanup_old_reminder_jobs, cleanup_old_audit_logs,
    set_chat_blocked, get_incomplete_homework_for_chats, get_schedule_for_chats,
    get_lesson_slots_for_chats, get_extra_activities, get_extra_activities_for_chats,
    get_day_overrides_for_chats, get_lesson_overrides_for_chats,
)
from handlers.extra import activities_on_date, format_extra_activities_block, format_extra_activity_line
from services.effective_schedule import (
    EffectiveDay, compute_effective_day, get_effective_day,
    format_effective_schedule_body, resolve_week_type,
)
import services.timeservice as ts
from keyboards.inline import DAYS_RU
from config import AUDIT_RETENTION_DAYS, HEARTBEAT_FILE
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

# Completed outbox rows older than this many days are pruned nightly.
REMINDER_JOB_RETENTION_DAYS = 7

# When the nightly housekeeping runs, on the UTC clock. Deliberately not tied to
# any chat's timezone so it fires exactly once a day however many zones the
# chats span.
HOUSEKEEPING_UTC_HM = (3, 30)


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

        await advance_reminder_job(job.id, i + 1, ts.now_iso_utc(), done=(i + 1 == len(chunks)))
        if i + 1 < len(chunks):
            await asyncio.sleep(SEND_THROTTLE_SECONDS)
    return True


async def _send_reminder(bot: Bot, chat_id: int, kind: str, job_date: datetime.date, text: str) -> bool:
    """
    Claim (or resume) the outbox job for ``(chat_id, kind, job_date)`` and
    deliver it. The claim distinguishes four situations (see
    database.db.ReminderClaim):

      * ``done``    — already delivered by an earlier attempt → True;
      * ``busy``    — another running instance owns it right now → False (this
        is NOT a delivery; the caller must not record it as sent, so no chat
        gets double-messaged by two bot copies);
      * ``claimed`` — we own it → send; a transient error / RetryAfter leaves it
        resumable and returns False so a later tick retries; Forbidden marks the
        chat blocked and returns False.
    """
    chunks = split_message(text)
    claim = await claim_reminder_job(chat_id, kind, job_date, chunks, ts.now_iso_utc())

    if claim.status == "done":
        return True
    if claim.status == "busy":
        logger.info("Reminder %s for chat %s is owned by another run — skipping.", kind, chat_id)
        return False

    try:
        await _send_job(bot, chat_id, claim.job)
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


# --- Category senders (each idempotent via its own outbox kind) -------------

async def send_hw_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo,
    incomplete_homework=None, effective_day: Optional[EffectiveDay] = None,
    today: Optional[datetime.date] = None,
) -> bool:
    """
    Homework-due-tomorrow reminder (+ a separate overdue block). Optional
    batch-fetched ``incomplete_homework``/``effective_day`` avoid per-chat
    queries during the sweep. Returns True when handled (delivered or nothing
    to send), False on a delivery error so the scheduler retries.

    ``tz`` is *this chat's* timezone. The sweep also passes the chat-local
    ``today`` it already computed, so one sweep can't straddle midnight and
    disagree with itself about which day it is.
    """
    today = today or datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    if incomplete_homework is None:
        incomplete_homework = await get_homework(chat_id, is_completed=False)
    homeworks = [hw for hw in incomplete_homework if hw.due_date == tomorrow]
    overdue = sorted((hw for hw in incomplete_homework if hw.due_date < today), key=lambda hw: hw.due_date)

    blocks = []
    if homeworks:
        block = f"🔔 <b>Домашнее задание на завтра ({tomorrow.strftime('%d.%m')}):</b>\n\n"
        block += _render_homework_list(homeworks)
        blocks.append(block)
    else:
        if effective_day is None:
            effective_day = await get_effective_day(chat_id, tomorrow)
        if effective_day.has_lessons:
            blocks.append(
                "🔔 <b>Домашнее задание на завтра:</b>\n\n"
                "🎉 Отличные новости! На завтра нет записанных домашних заданий."
            )

    if overdue:
        block = "⚠️ <b>Просроченные задания:</b>\n\n" + _render_homework_list(overdue)
        blocks.append(block)

    if not blocks:
        return True

    text = "\n\n".join(block.rstrip("\n") for block in blocks)
    return await _send_reminder(bot, chat_id, "hw", today, text)


async def send_schedule_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo,
    effective_day: Optional[EffectiveDay] = None, extra_activities=None,
    today: Optional[datetime.date] = None,
) -> bool:
    """
    "Pack your bag" reminder for tomorrow: the effective lessons plus a
    dedicated block of tomorrow's extra activities. ``tz``/``today`` are this
    chat's own (see :func:`send_hw_reminder`).
    """
    today = today or datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_weekday = tomorrow.weekday()

    if effective_day is None:
        effective_day = await get_effective_day(chat_id, tomorrow)
    if extra_activities is None:
        extra_activities = await get_extra_activities(chat_id)

    parts = []
    if effective_day.has_lessons or effective_day.has_changes:
        day_name = DAYS_RU[tomorrow_weekday]
        body = format_effective_schedule_body(effective_day, no_lessons_text="🥱 Завтра нет уроков!")
        parts.append(f"Расписание на завтра (<b>{day_name}</b>):\n\n{body}")

    extra_block = format_extra_activities_block(activities_on_date(extra_activities, tomorrow))
    if extra_block:
        parts.append(extra_block)

    if not parts:
        return True

    text = "🎒 <b>Пора собирать портфель!</b>\n\n" + "\n\n".join(parts)
    return await _send_reminder(bot, chat_id, "sched", today, text)


async def send_changes_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo,
    effective_day: Optional[EffectiveDay] = None,
    today: Optional[datetime.date] = None,
) -> bool:
    """
    Heads-up when tomorrow's schedule differs from the usual template
    (cancellations, substitutions, a free/holiday day, an added lesson). Sends
    nothing (and is "handled") when tomorrow has no changes.
    """
    today = today or datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    if effective_day is None:
        effective_day = await get_effective_day(chat_id, tomorrow)
    if not effective_day.has_changes:
        return True

    day_name = DAYS_RU[tomorrow.weekday()]
    body = format_effective_schedule_body(effective_day, no_lessons_text="🥱 Завтра нет уроков!")
    text = (
        f"⚠️ <b>Внимание: завтра ({day_name}) изменения в расписании!</b>\n\n{body}"
    )
    return await _send_reminder(bot, chat_id, "changes", today, text)


async def send_duetoday_reminder(
    bot: Bot, chat_id: int, tz: pytz.BaseTzInfo, incomplete_homework=None,
    today: Optional[datetime.date] = None,
) -> bool:
    """Morning reminder of homework that is due *today* (in this chat's zone)."""
    today = today or datetime.datetime.now(tz).date()
    if incomplete_homework is None:
        incomplete_homework = await get_homework(chat_id, is_completed=False)
    due_today = [hw for hw in incomplete_homework if hw.due_date == today]
    if not due_today:
        return True
    text = "⏰ <b>Сегодня нужно сдать домашнее задание:</b>\n\n" + _render_homework_list(due_today)
    return await _send_reminder(bot, chat_id, "duetoday", today, text.rstrip("\n"))


# --- Extra-activity reminders (per activity, "N minutes before") ------------

def _extra_occurrence(activity, tz: pytz.BaseTzInfo, now: datetime.datetime):
    """
    The relevant occurrence of ``activity`` around ``now`` as
    ``(occurrence_date, start_datetime)`` (start tz-aware), or ``None``.

    For a weekly activity this is today's occurrence (or next week's if today's
    already started); for a one-off it is its fixed date.
    """
    start_t = ts.parse_hhmm(activity.start_time)
    if start_t is None:
        return None
    today = now.date()
    if activity.kind == "once":
        if activity.activity_date is None:
            return None
        occ_date = activity.activity_date
    elif activity.kind == "weekly" and activity.day_of_week is not None:
        days_ahead = (activity.day_of_week - today.weekday()) % 7
        occ_date = today + datetime.timedelta(days=days_ahead)
    else:
        return None

    # ts.combine resolves DST oddities: a start time that doesn't exist on a
    # spring-forward day is shifted just past the gap, and an ambiguous one on a
    # fall-back day resolves to its first occurrence.
    start_dt = ts.combine(tz, occ_date, start_t)
    if activity.kind == "weekly" and occ_date == today and start_dt <= now:
        occ_date = occ_date + datetime.timedelta(days=7)
        start_dt = ts.combine(tz, occ_date, start_t)
    return occ_date, start_dt


def _extra_should_send(now: datetime.datetime, start_dt: datetime.datetime, minutes: int, chat) -> bool:
    """
    Whether an activity reminder is due *right now*.

    Fires in the window ``[start - minutes, start)`` — never after the activity
    has already begun. During quiet hours the reminder is deferred, EXCEPT when
    deferring until quiet-hours end would push it past the activity's start; in
    that case it is sent now so it still arrives before the activity begins.
    """
    trigger = start_dt - datetime.timedelta(minutes=minutes)
    if now < trigger or now >= start_dt:
        return False
    if ts.in_quiet_hours(now.time(), chat.quiet_start, chat.quiet_end):
        quiet_end = ts.next_quiet_end(now, chat.quiet_start, chat.quiet_end)
        if quiet_end is not None and quiet_end < start_dt:
            return False  # safe to defer: quiet ends before the activity starts
    return True


async def send_extra_activity_reminders(bot: Bot, chat, activities, tz: pytz.BaseTzInfo, now: datetime.datetime):
    """
    Evaluate every reminder-enabled activity for ``chat`` and send those whose
    "N minutes before start" moment has arrived. Each occurrence is idempotent
    via an outbox job keyed ``ea<id>`` + the occurrence date, so it is sent at
    most once even across restarts or two bot instances.
    """
    if not chat.extra_reminder_enabled:
        return
    for activity in activities:
        if not activity.reminder_enabled:
            continue
        occ = _extra_occurrence(activity, tz, now)
        if occ is None:
            continue
        occ_date, start_dt = occ
        if not _extra_should_send(now, start_dt, activity.reminder_minutes, chat):
            continue
        when = activity.start_time + (f" - {activity.end_time}" if activity.end_time else "")
        text = f"🔔 <b>Скоро занятие</b> (в {when}):\n\n" + format_extra_activity_line(activity)
        try:
            await _send_reminder(bot, chat.chat_id, f"ea{activity.id}", occ_date, text)
        except Exception as e:
            logger.warning("Extra reminder failed for chat %s activity %s: %s", chat.chat_id, activity.id, e)


# --- Housekeeping -----------------------------------------------------------

async def prune_audit_log() -> int:
    """
    Drop audit-journal rows older than ``AUDIT_RETENTION_DAYS`` (default 180).
    A retention of 0 disables pruning — history is then kept indefinitely.

    Never raises: housekeeping must not be able to abort a scheduler tick.
    Returns the number of rows removed (0 when disabled or on failure).
    """
    if AUDIT_RETENTION_DAYS <= 0:
        return 0
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=AUDIT_RETENTION_DAYS
    )
    try:
        removed = await cleanup_old_audit_logs(cutoff.isoformat())
    except Exception as e:
        logger.warning("Audit-log cleanup failed: %s", e)
        return 0
    if removed:
        logger.info("Pruned %s audit entries older than %s days.", removed, AUDIT_RETENTION_DAYS)
    return removed


# --- Sweep ------------------------------------------------------------------

def _due(enabled: bool, last_date, time_str: str, current_hm, today) -> bool:
    if not enabled or last_date == today:
        return False
    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return False
    return current_hm >= (h, m)


class ChatClock:
    """
    One chat's local view of time for this tick: its timezone plus the local
    ``now`` / ``today`` / ``tomorrow`` derived from it.

    Computed once per chat per tick so every decision and every message about
    that chat uses the same instant — a sweep that read the clock repeatedly
    could otherwise straddle midnight and disagree with itself.
    """
    __slots__ = ("tz", "now", "today", "tomorrow")

    def __init__(self, chat):
        self.tz = ts.chat_tz(chat)
        # Always via the time service, never a bare datetime.now(): that keeps
        # the whole app on one clock (and one seam for tests).
        self.now = ts.now(chat)
        self.today = self.now.date()
        self.tomorrow = self.today + datetime.timedelta(days=1)

    @property
    def hm(self):
        return (self.now.hour, self.now.minute)


async def _nightly_housekeeping():
    """
    Prune old outbox rows and audit entries once a day.

    Triggered on the *UTC* clock, not any chat's, so it runs exactly once per day
    regardless of how many timezones the chats span.
    """
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    if (utc_now.hour, utc_now.minute) != HOUSEKEEPING_UTC_HM:
        return
    try:
        cutoff = utc_now.date() - datetime.timedelta(days=REMINDER_JOB_RETENTION_DAYS)
        removed = await cleanup_old_reminder_jobs(cutoff)
        if removed:
            logger.info("Pruned %s old reminder jobs.", removed)
    except Exception as e:
        logger.warning("Reminder-job cleanup failed: %s", e)
    await prune_audit_log()


async def check_and_send_reminders(bot: Bot):
    """
    One sweep over every onboarded chat.

    Each chat is evaluated in **its own** timezone: two chats in different zones
    can be on different calendar dates within the same tick, and each gets its
    own "today"/"tomorrow". Date-dependent batch fetches are therefore grouped by
    the distinct local "tomorrow" dates present (in practice two or three), so
    the sweep still issues a handful of queries rather than one per chat.
    """
    _touch_heartbeat()
    await _nightly_housekeeping()

    chats = [c for c in await get_all_chats() if c.is_onboarded]
    if not chats:
        return

    # Which time-based categories are due this tick. Non-urgent categories are
    # deferred while the chat is inside its quiet hours (they fire once quiet
    # hours end and the clock is still past the configured time).
    clocks: dict = {}
    hw_due, sched_due, changes_due, duetoday_due = [], [], [], []
    extra_chats = []
    for chat in chats:
        try:
            clock = ChatClock(chat)
            clocks[chat.chat_id] = clock
            today, current_hm = clock.today, clock.hm
            quiet = ts.in_quiet_hours(clock.now.time(), chat.quiet_start, chat.quiet_end)
            if not quiet:
                if _due(chat.hw_reminder_enabled, chat.last_hw_reminder_date, chat.hw_reminder_time, current_hm, today):
                    hw_due.append(chat.chat_id)
                if _due(chat.schedule_reminder_enabled, chat.last_sch_reminder_date, chat.schedule_reminder_time, current_hm, today):
                    sched_due.append(chat.chat_id)
                if _due(chat.changes_reminder_enabled, chat.last_changes_reminder_date, chat.schedule_reminder_time, current_hm, today):
                    changes_due.append(chat.chat_id)
                if _due(chat.hw_duetoday_enabled, chat.last_duetoday_reminder_date, chat.hw_duetoday_time, current_hm, today):
                    duetoday_due.append(chat.chat_id)
            if chat.extra_reminder_enabled:
                extra_chats.append(chat.chat_id)
        except Exception as e:
            logger.exception(f"Bad reminder config for chat {chat.chat_id}: {e}")

    # Batch-fetch everything the due categories need — one query per resource
    # for the union of relevant chats (no per-chat N+1).
    hw_ids = list(set(hw_due) | set(duetoday_due))
    sched_ids = list(set(hw_due) | set(sched_due) | set(changes_due))
    homework_by_chat = await get_incomplete_homework_for_chats(hw_ids)
    slots_by_chat = await get_lesson_slots_for_chats(sched_ids)
    extra_by_chat = await get_extra_activities_for_chats(list(set(extra_chats) | set(sched_due)))

    # Date-dependent fetches: one round per distinct local "tomorrow".
    chats_by_tomorrow: dict = defaultdict(list)
    for cid in sched_ids:
        chats_by_tomorrow[clocks[cid].tomorrow].append(cid)

    schedule_by_chat: dict = {}
    day_ovr_by_chat: dict = {}
    lesson_ovr_by_chat: dict = {}
    for tomorrow, group in chats_by_tomorrow.items():
        schedule_by_chat.update(await get_schedule_for_chats(group, tomorrow.weekday()))
        day_ovr_by_chat.update(await get_day_overrides_for_chats(group, tomorrow))
        lesson_ovr_by_chat.update(await get_lesson_overrides_for_chats(group, tomorrow))

    chat_by_id = {c.chat_id: c for c in chats}
    effective_by_chat = {}
    for cid in sched_ids:
        chat = chat_by_id.get(cid)
        tomorrow = clocks[cid].tomorrow
        week_type = resolve_week_type(
            bool(chat and chat.week_mode),
            chat.week_anchor_monday if chat else None,
            tomorrow,
        )
        sched_rows = [r for r in schedule_by_chat.get(cid, []) if r.week_type == week_type]
        effective_by_chat[cid] = compute_effective_day(
            tomorrow, slots_by_chat.get(cid, []), sched_rows,
            day_ovr_by_chat.get(cid), lesson_ovr_by_chat.get(cid, []),
        )

    for chat in chats:
        # Isolate each chat: a failure for one must not abort the whole sweep.
        try:
            cid = chat.chat_id
            clock = clocks.get(cid)
            if clock is None:
                continue  # its config blew up above; already logged
            tz, today = clock.tz, clock.today

            if cid in hw_due:
                if await send_hw_reminder(
                    bot, cid, tz,
                    incomplete_homework=homework_by_chat.get(cid, []),
                    effective_day=effective_by_chat.get(cid),
                    today=today,
                ):
                    await update_last_hw_reminder_date(cid, today)

            if cid in sched_due:
                if await send_schedule_reminder(
                    bot, cid, tz,
                    effective_day=effective_by_chat.get(cid),
                    extra_activities=extra_by_chat.get(cid, []),
                    today=today,
                ):
                    await update_last_sch_reminder_date(cid, today)

            if cid in changes_due:
                if await send_changes_reminder(
                    bot, cid, tz, effective_day=effective_by_chat.get(cid), today=today
                ):
                    await update_last_changes_reminder_date(cid, today)

            if cid in duetoday_due:
                if await send_duetoday_reminder(
                    bot, cid, tz,
                    incomplete_homework=homework_by_chat.get(cid, []), today=today,
                ):
                    await update_last_duetoday_reminder_date(cid, today)

            if chat.extra_reminder_enabled:
                await send_extra_activity_reminders(
                    bot, chat, extra_by_chat.get(cid, []), tz, clock.now
                )
        except Exception as e:
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
