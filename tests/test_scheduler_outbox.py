"""
Covers: no empty-header schedule reminder, resumable multi-chunk delivery
after a partial failure (outbox job), TelegramRetryAfter handling, and
TelegramForbiddenError marking a chat as blocked (and no longer polled).
"""
import datetime
import pytz

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from database.db import (
    get_or_create_chat, save_lesson_slots, save_schedule_day, set_onboarded,
    get_all_chats, mark_chat_seen,
)
from services.scheduler import send_schedule_reminder, _send_reminder
from config import TIMEZONE

CHAT_ID = 600001
tz = pytz.timezone(TIMEZONE)


async def _onboarded_chat(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)


async def test_schedule_reminder_skips_when_lesson_numbers_dont_overlap(db, fake_bot):
    """
    Schedule entries exist and lesson slots exist, but their lesson_numbers
    never intersect — there is nothing real to show, so no header-only,
    contentless message should be sent.
    """
    await _onboarded_chat()
    tomorrow_weekday = (datetime.datetime.now(tz).date() + datetime.timedelta(days=1)).weekday()

    # Only lesson slot #1 configured...
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    # ...but the schedule references lesson #5, which has no matching slot.
    await save_schedule_day(CHAT_ID, tomorrow_weekday, [(5, "Math")])

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert fake_bot.sent == []


async def test_schedule_reminder_sends_when_overlap_exists(db, fake_bot):
    await _onboarded_chat()
    tomorrow_weekday = (datetime.datetime.now(tz).date() + datetime.timedelta(days=1)).weekday()
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, tomorrow_weekday, [(1, "Math")])

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) == 1
    assert "Math" in fake_bot.sent[0][1]


async def test_partial_multipart_failure_resumes_without_resending(db):
    """
    A long reminder split into 3 chunks fails on chunk 2. On retry, chunk 1
    (already delivered) must NOT be sent again — only chunks 2 and 3.
    """
    from tests.conftest import FakeBot, FakeTelegramError

    await get_or_create_chat(CHAT_ID, "private")
    long_text = "\n\n".join(f"Block {i}" * 500 for i in range(3))
    bot = FakeBot(fail_sequence=[None, FakeTelegramError("boom"), None])

    ok = await _send_reminder(bot, CHAT_ID, "hw", datetime.date(2026, 1, 1), long_text)
    assert ok is False
    first_attempt_sent = len(bot.sent)
    assert first_attempt_sent >= 1

    # Retry: the FakeBot's fail_sequence is exhausted, so this attempt would
    # succeed for any chunk it's asked to send. The already-sent chunk(s)
    # from the first attempt must not be sent again.
    bot.fail_sequence = None
    ok2 = await _send_reminder(bot, CHAT_ID, "hw", datetime.date(2026, 1, 1), long_text)
    assert ok2 is True

    # Every message actually delivered must be a distinct chunk of the text —
    # no chunk should appear twice across both attempts.
    texts = [t for _, t, _ in bot.sent]
    assert len(texts) == len(set(texts))


async def test_telegram_retry_after_is_retried_once_then_succeeds(db):
    from tests.conftest import FakeBot

    await get_or_create_chat(CHAT_ID, "private")
    retry_error = TelegramRetryAfter(method=None, message="flood", retry_after=0)
    bot = FakeBot(fail_sequence=[retry_error])  # the retry (2nd attempt) succeeds

    ok = await _send_reminder(bot, CHAT_ID, "sched", datetime.date(2026, 1, 2), "hello")
    assert ok is True
    assert len(bot.sent) == 1


async def test_telegram_forbidden_marks_chat_blocked_and_excluded(db):
    from tests.conftest import FakeBot

    await get_or_create_chat(CHAT_ID, "private")
    bot = FakeBot(fail_sequence=[TelegramForbiddenError(method=None, message="kicked")])

    ok = await _send_reminder(bot, CHAT_ID, "hw", datetime.date(2026, 1, 3), "hello")
    assert ok is False

    chats = await get_all_chats()
    assert all(c.chat_id != CHAT_ID for c in chats), "blocked chat must be excluded from the sweep"

    chats_incl = await get_all_chats(include_blocked=True)
    blocked_chat = next(c for c in chats_incl if c.chat_id == CHAT_ID)
    assert blocked_chat.is_blocked is True

    # Once the chat talks to the bot again, it should be un-blocked.
    await mark_chat_seen(CHAT_ID)
    chats_after = await get_all_chats()
    assert any(c.chat_id == CHAT_ID for c in chats_after)


async def test_sweep_batches_queries_instead_of_per_chat(db, fake_bot, monkeypatch):
    """
    With N due chats, the homework/schedule/lesson-slot data must be fetched
    via ONE batched query each (not N queries) — see
    database.db.get_incomplete_homework_for_chats & friends.
    """
    import database.db as db_module
    from services.scheduler import check_and_send_reminders
    from database.db import update_chat_reminder_times

    n_chats = 5
    for i in range(n_chats):
        await _onboarded_chat(700000 + i)
        await update_chat_reminder_times(700000 + i, hw_time="00:00", schedule_time="00:00")

    call_counts = {"hw": 0, "sched": 0, "slots": 0}
    orig_hw = db_module.get_incomplete_homework_for_chats
    orig_sched = db_module.get_schedule_for_chats
    orig_slots = db_module.get_lesson_slots_for_chats

    async def counted_hw(chat_ids):
        call_counts["hw"] += 1
        return await orig_hw(chat_ids)

    async def counted_sched(chat_ids, day):
        call_counts["sched"] += 1
        return await orig_sched(chat_ids, day)

    async def counted_slots(chat_ids):
        call_counts["slots"] += 1
        return await orig_slots(chat_ids)

    monkeypatch.setattr(db_module, "get_incomplete_homework_for_chats", counted_hw)
    monkeypatch.setattr(db_module, "get_schedule_for_chats", counted_sched)
    monkeypatch.setattr(db_module, "get_lesson_slots_for_chats", counted_slots)
    # scheduler.py imported these names directly, so patch its references too.
    import services.scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module, "get_incomplete_homework_for_chats", counted_hw)
    monkeypatch.setattr(scheduler_module, "get_schedule_for_chats", counted_sched)
    monkeypatch.setattr(scheduler_module, "get_lesson_slots_for_chats", counted_slots)

    await check_and_send_reminders(fake_bot)

    assert call_counts == {"hw": 1, "sched": 1, "slots": 1}, (
        "each batch fetch must run exactly once per sweep regardless of chat count"
    )
