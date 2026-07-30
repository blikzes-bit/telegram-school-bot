"""
Full-featured reminders (PROMPT 4): quiet-hours time service, atomic outbox
claim (no double-send by two instances; busy != delivered), job cleanup, the
new reminder categories (changes / due-today / per-activity), per-activity
"minutes before" reminders and quiet-hours deferral capped at the start.
"""
import asyncio
import datetime
from types import SimpleNamespace

import pytz

from config import TIMEZONE
from database.db import (
    get_or_create_chat, get_chat, set_onboarded, add_homework,
    save_lesson_slots, save_schedule_day, set_lesson_override, add_extra_activity,
    set_extra_activity_reminder, set_quiet_hours, set_reminder_category_enabled,
    get_extra_activities, claim_reminder_job, cleanup_old_reminder_jobs,
    update_chat_reminder_times,
)
import services.timeservice as ts
from services import scheduler
from services.scheduler import (
    send_changes_reminder, send_duetoday_reminder, send_extra_activity_reminders,
    _extra_occurrence, _extra_should_send, check_and_send_reminders,
)

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 828282


# --- Quiet-hours time service (pure) ----------------------------------------

def _t(h, m=0):
    return datetime.time(h, m)


def test_quiet_hours_wrapping_midnight():
    assert ts.in_quiet_hours(_t(23), "22:00", "07:00") is True
    assert ts.in_quiet_hours(_t(6, 59), "22:00", "07:00") is True
    assert ts.in_quiet_hours(_t(7), "22:00", "07:00") is False   # half-open end
    assert ts.in_quiet_hours(_t(12), "22:00", "07:00") is False


def test_quiet_hours_same_day():
    assert ts.in_quiet_hours(_t(13), "12:00", "14:00") is True
    assert ts.in_quiet_hours(_t(14), "12:00", "14:00") is False
    assert ts.in_quiet_hours(_t(11), "12:00", "14:00") is False


def test_quiet_hours_disabled():
    assert ts.has_quiet_hours(None, None) is False
    assert ts.has_quiet_hours("22:00", "22:00") is False  # equal = off
    assert ts.in_quiet_hours(_t(23), None, None) is False


def test_next_quiet_end():
    ref = datetime.datetime(2026, 1, 1, 23, 0)
    end = ts.next_quiet_end(ref, "22:00", "07:00")
    assert end == datetime.datetime(2026, 1, 2, 7, 0)
    # Not in quiet hours → None.
    assert ts.next_quiet_end(datetime.datetime(2026, 1, 1, 12, 0), "22:00", "07:00") is None


# --- Atomic outbox claim: no double-send ------------------------------------

async def test_concurrent_claim_only_one_wins(tmp_path):
    """
    Two truly-concurrent claims for the same outbox key: exactly one must be
    'claimed' (it sends) and the other 'busy' (it must NOT send). This is what
    stops two bot instances double-messaging a chat.
    """
    import database.db as db_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from database.models import Base

    db_path = tmp_path / "claims.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    old_engine, old_session = db_module.engine, db_module.AsyncSessionLocal
    db_module.engine, db_module.AsyncSessionLocal = engine, Session
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await get_or_create_chat(CHAT_ID, "private")

        job_date = datetime.date(2026, 1, 1)
        results = await asyncio.gather(
            claim_reminder_job(CHAT_ID, "hw", job_date, ["a", "b"], ts.now_iso_utc()),
            claim_reminder_job(CHAT_ID, "hw", job_date, ["a", "b"], ts.now_iso_utc()),
        )
        statuses = sorted(r.status for r in results)
        assert statuses == ["busy", "claimed"], statuses
        # busy is NOT a delivery.
        busy = next(r for r in results if r.status == "busy")
        assert busy.job is None
    finally:
        db_module.engine, db_module.AsyncSessionLocal = old_engine, old_session
        await engine.dispose()


async def test_claim_done_is_distinct_from_busy(db):
    await get_or_create_chat(CHAT_ID, "private")
    job_date = datetime.date(2026, 1, 1)
    c1 = await claim_reminder_job(CHAT_ID, "hw", job_date, ["x"], ts.now_iso_utc())
    assert c1.status == "claimed"
    # Mark delivered.
    from database.db import advance_reminder_job
    await advance_reminder_job(c1.job.id, 1, ts.now_iso_utc(), done=True)
    c2 = await claim_reminder_job(CHAT_ID, "hw", job_date, ["x"], ts.now_iso_utc())
    assert c2.status == "done"


async def test_cleanup_old_reminder_jobs(db):
    await get_or_create_chat(CHAT_ID, "private")
    old = datetime.date(2026, 1, 1)
    recent = datetime.date(2026, 6, 1)
    await claim_reminder_job(CHAT_ID, "hw", old, ["x"], ts.now_iso_utc())
    await claim_reminder_job(CHAT_ID, "hw", recent, ["x"], ts.now_iso_utc())
    removed = await cleanup_old_reminder_jobs(datetime.date(2026, 3, 1))
    assert removed == 1
    # The recent one survives.
    again = await claim_reminder_job(CHAT_ID, "hw", recent, ["x"], ts.now_iso_utc())
    assert again.status in ("busy", "claimed", "done")


# --- New category senders ---------------------------------------------------

async def _onboarded(chat_id=CHAT_ID, day_of_week=None):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)


async def test_changes_reminder_sends_only_on_change(db, fake_bot):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, tomorrow.weekday(), [(1, "Математика")])

    # No override yet → nothing sent.
    assert await send_changes_reminder(fake_bot, CHAT_ID, tz) is True
    assert fake_bot.sent == []

    # Cancel tomorrow's lesson → a change → a heads-up is sent.
    await set_lesson_override(CHAT_ID, tomorrow, 1, "cancel")
    assert await send_changes_reminder(fake_bot, CHAT_ID, tz) is True
    assert len(fake_bot.sent) == 1
    assert "изменения в расписании" in fake_bot.sent[0][1]
    assert "(отменён)" in fake_bot.sent[0][1]


async def test_duetoday_reminder(db, fake_bot):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    await add_homework(CHAT_ID, "Химия", today, "формулы")
    assert await send_duetoday_reminder(fake_bot, CHAT_ID, tz) is True
    assert len(fake_bot.sent) == 1
    assert "сегодня" in fake_bot.sent[0][1].lower()
    assert "Химия" in fake_bot.sent[0][1]


async def test_duetoday_reminder_nothing_when_no_hw(db, fake_bot):
    await _onboarded()
    assert await send_duetoday_reminder(fake_bot, CHAT_ID, tz) is True
    assert fake_bot.sent == []


# --- Per-activity reminder logic (pure) -------------------------------------

def _activity(**kw):
    kw.setdefault("kind", "weekly")
    kw.setdefault("day_of_week", None)
    kw.setdefault("activity_date", None)
    kw.setdefault("start_time", "18:00")
    kw.setdefault("end_time", None)
    kw.setdefault("reminder_enabled", True)
    kw.setdefault("reminder_minutes", 60)
    return SimpleNamespace(id=1, title="Англ", location=None, note=None, **kw)


def _chat(quiet_start=None, quiet_end=None):
    return SimpleNamespace(chat_id=CHAT_ID, extra_reminder_enabled=True,
                           quiet_start=quiet_start, quiet_end=quiet_end)


def test_extra_occurrence_once():
    d = datetime.date(2026, 9, 14)
    act = _activity(kind="once", activity_date=d, start_time="18:00")
    now = tz.localize(datetime.datetime(2026, 9, 14, 17, 0))
    occ_date, start_dt = _extra_occurrence(act, tz, now)
    assert occ_date == d
    assert start_dt.hour == 18


def test_extra_should_send_window():
    act_start = tz.localize(datetime.datetime(2026, 9, 14, 18, 0))
    chat = _chat()
    # 30 min before, 60-min lead → inside window.
    assert _extra_should_send(tz.localize(datetime.datetime(2026, 9, 14, 17, 30)), act_start, 60, chat) is True
    # Too early (before trigger).
    assert _extra_should_send(tz.localize(datetime.datetime(2026, 9, 14, 16, 0)), act_start, 60, chat) is False
    # After start → never.
    assert _extra_should_send(tz.localize(datetime.datetime(2026, 9, 14, 18, 1)), act_start, 60, chat) is False


def test_extra_should_send_quiet_hours_defer_vs_send():
    act_start = tz.localize(datetime.datetime(2026, 9, 14, 18, 0))
    now = tz.localize(datetime.datetime(2026, 9, 14, 17, 0))
    # Quiet ends AFTER the start → deferring would miss it → send now.
    chat_send = _chat("16:00", "19:00")
    assert _extra_should_send(now, act_start, 120, chat_send) is True
    # Quiet ends BEFORE the start → safe to defer → not now.
    chat_defer = _chat("16:00", "17:30")
    assert _extra_should_send(now, act_start, 120, chat_defer) is False


# --- Per-activity reminder integration --------------------------------------

async def test_extra_reminder_sends_once_and_is_idempotent(db, fake_bot):
    await _onboarded()
    d = datetime.date(2026, 9, 14)
    a = await add_extra_activity(CHAT_ID, title="Английский", kind="once", start_time="18:00", activity_date=d)
    await set_extra_activity_reminder(CHAT_ID, a.id, enabled=True, minutes=60)
    chat = await get_chat(CHAT_ID)
    activities = await get_extra_activities(CHAT_ID)
    now = tz.localize(datetime.datetime(2026, 9, 14, 17, 30))

    await send_extra_activity_reminders(fake_bot, chat, activities, tz, now)
    assert len(fake_bot.sent) == 1
    assert "Английский" in fake_bot.sent[0][1]

    # Second sweep at the same moment must NOT resend (idempotent per occurrence).
    await send_extra_activity_reminders(fake_bot, chat, activities, tz, now)
    assert len(fake_bot.sent) == 1


async def test_extra_reminder_multiple_activities_same_day(db, fake_bot):
    await _onboarded()
    d = datetime.date(2026, 9, 14)
    a1 = await add_extra_activity(CHAT_ID, title="Англ", kind="once", start_time="18:00", activity_date=d)
    a2 = await add_extra_activity(CHAT_ID, title="Плавание", kind="once", start_time="18:30", activity_date=d)
    await set_extra_activity_reminder(CHAT_ID, a1.id, enabled=True, minutes=60)
    await set_extra_activity_reminder(CHAT_ID, a2.id, enabled=True, minutes=60)
    chat = await get_chat(CHAT_ID)
    activities = await get_extra_activities(CHAT_ID)
    now = tz.localize(datetime.datetime(2026, 9, 14, 17, 30))

    await send_extra_activity_reminders(fake_bot, chat, activities, tz, now)
    titles = " ".join(t for _, t, _ in fake_bot.sent)
    assert "Англ" in titles and "Плавание" in titles
    assert len(fake_bot.sent) == 2


async def test_extra_reminder_master_switch_off(db, fake_bot):
    await _onboarded()
    d = datetime.date(2026, 9, 14)
    a = await add_extra_activity(CHAT_ID, title="Англ", kind="once", start_time="18:00", activity_date=d)
    await set_extra_activity_reminder(CHAT_ID, a.id, enabled=True, minutes=60)
    await set_reminder_category_enabled(CHAT_ID, "extra", False)
    chat = await get_chat(CHAT_ID)
    activities = await get_extra_activities(CHAT_ID)
    now = tz.localize(datetime.datetime(2026, 9, 14, 17, 30))

    await send_extra_activity_reminders(fake_bot, chat, activities, tz, now)
    assert fake_bot.sent == []


async def test_extra_reminder_not_sent_after_start(db, fake_bot):
    await _onboarded()
    d = datetime.date(2026, 9, 14)
    a = await add_extra_activity(CHAT_ID, title="Англ", kind="once", start_time="18:00", activity_date=d)
    await set_extra_activity_reminder(CHAT_ID, a.id, enabled=True, minutes=60)
    chat = await get_chat(CHAT_ID)
    activities = await get_extra_activities(CHAT_ID)
    now = tz.localize(datetime.datetime(2026, 9, 14, 18, 30))  # already started

    await send_extra_activity_reminders(fake_bot, chat, activities, tz, now)
    assert fake_bot.sent == []


# --- Quiet hours defer + no double send in the sweep ------------------------

async def test_sweep_defers_in_quiet_hours(db, fake_bot, monkeypatch):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")
    await update_chat_reminder_times(CHAT_ID, hw_time="20:00")
    await set_quiet_hours(CHAT_ID, "22:00", "07:00")

    # Pretend "now" is 23:00 — inside quiet hours.
    fixed = tz.localize(datetime.datetime.combine(today, datetime.time(23, 0)))
    monkeypatch.setattr(scheduler.ts, "now", lambda chat=None: fixed)

    await check_and_send_reminders(fake_bot)
    assert fake_bot.sent == []  # deferred
    chat = await get_chat(CHAT_ID)
    assert chat.last_hw_reminder_date is None  # not stamped → will fire later


async def test_sweep_sends_outside_quiet_and_no_double_send(db, fake_bot, monkeypatch):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    await add_homework(CHAT_ID, "Math", tomorrow, "p.42")
    await update_chat_reminder_times(CHAT_ID, hw_time="20:00")
    await set_quiet_hours(CHAT_ID, "22:00", "07:00")

    fixed = tz.localize(datetime.datetime.combine(today, datetime.time(21, 0)))  # outside quiet
    monkeypatch.setattr(scheduler.ts, "now", lambda chat=None: fixed)

    await check_and_send_reminders(fake_bot)
    hw_msgs = [t for _, t, _ in fake_bot.sent if "Домашнее задание на завтра" in t]
    assert len(hw_msgs) == 1
    chat = await get_chat(CHAT_ID)
    assert chat.last_hw_reminder_date == today

    # A second sweep the same day must not send the HW reminder again.
    await check_and_send_reminders(fake_bot)
    hw_msgs2 = [t for _, t, _ in fake_bot.sent if "Домашнее задание на завтра" in t]
    assert len(hw_msgs2) == 1


async def test_sweep_skips_disabled_duetoday(db, fake_bot, monkeypatch):
    await _onboarded()
    today = datetime.datetime.now(tz).date()
    await add_homework(CHAT_ID, "Math", today, "due today")
    await set_reminder_category_enabled(CHAT_ID, "duetoday", False)
    # duetoday time default 07:30; pretend now is 08:00 so it *would* be due.
    fixed = tz.localize(datetime.datetime.combine(today, datetime.time(8, 0)))
    monkeypatch.setattr(scheduler.ts, "now", lambda chat=None: fixed)

    await check_and_send_reminders(fake_bot)
    duetoday_msgs = [t for _, t, _ in fake_bot.sent if "Сегодня нужно сдать" in t]
    assert duetoday_msgs == []
