"""
Stage: a timezone per chat.

Covers: the default and backfill, validation/normalisation of a zone name, the
settings flow (popular picker, manual entry, local-time preview, admin gate),
that a broken stored zone can't stop the scheduler, DST (a local time that
doesn't exist, one that happens twice, and no double reminder), that "today"
differs between chats in different zones, and that one sweep serves chats in
Europe/Kyiv, UTC and America/New_York correctly.
"""
import datetime
from types import SimpleNamespace

import pytz

from config import TIMEZONE
from database.db import (
    add_homework, get_chat, get_or_create_chat, migrate_chat, save_lesson_slots,
    save_schedule_day, set_chat_timezone, set_onboarded,
    update_chat_reminder_times,
)
import handlers.settings as settings_handlers
from services import scheduler
import services.timeservice as ts

KYIV = "Europe/Kyiv"
NEW_YORK = "America/New_York"

CHAT_KYIV = -100_900_001
CHAT_UTC = -100_900_002
CHAT_NY = -100_900_003
PRIVATE_ID = 900_100

ADMIN_ID = 11
MEMBER_ID = 22

# One fixed instant, viewed from three zones:
#   Europe/Kyiv  (UTC+3) → 2026-05-11 02:30  (already tomorrow)
#   UTC                  → 2026-05-10 23:30
#   America/New_York(-4) → 2026-05-10 19:30
FIXED_UTC = datetime.datetime(2026, 5, 10, 23, 30, tzinfo=datetime.timezone.utc)


class FakeBot:
    def __init__(self, admins=None):
        self.admins = admins or set()
        self.sent = []

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(
            status="administrator" if user_id in self.admins else "member"
        )

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class FakeMessage:
    def __init__(self, chat_id=PRIVATE_ID, text=None, chat_type="private",
                 user_id=ADMIN_ID, bot=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name="Аня", first_name="Аня")
        self.bot = bot or FakeBot({ADMIN_ID})
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    @property
    def texts(self):
        return [a[0] for a in self.answers]


class FakeCallback:
    def __init__(self, data, chat_id=PRIVATE_ID, chat_type="private", user_id=ADMIN_ID):
        self.message = FakeMessage(chat_id, chat_type=chat_type, user_id=user_id)
        self.data = data
        self.from_user = self.message.from_user
        self.bot = self.message.bot
        self.alerts = []
        self.notices = []

    async def answer(self, *args, **kwargs):
        text = args[0] if args else kwargs.get("text")
        (self.alerts if kwargs.get("show_alert") else self.notices).append(text)

    @property
    def replies(self):
        return [t for t in self.alerts + self.notices if t]


def _state(chat_id=PRIVATE_ID):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


async def _onboarded(chat_id, tz_name, hw_time="20:00"):
    """A chat with lessons on every weekday, its own zone and a reminder time."""
    await get_or_create_chat(chat_id, "group")
    await set_onboarded(chat_id, True)
    assert await set_chat_timezone(chat_id, tz_name)
    await save_lesson_slots(chat_id, [(1, "08:30", "09:15")])
    for weekday in range(7):
        await save_schedule_day(chat_id, weekday, [(1, "Математика")])
    await update_chat_reminder_times(chat_id, hw_time=hw_time)


# --- Default and backfill ---------------------------------------------------

async def test_new_chat_gets_the_configured_default(db):
    chat = await get_or_create_chat(PRIVATE_ID, "private")
    assert chat.timezone == TIMEZONE
    assert ts.chat_tz(chat).zone == TIMEZONE


async def test_existing_chats_are_never_left_without_a_zone(db):
    """
    The column is NOT NULL with the process default, so a chat created before
    per-chat zones existed keeps running on exactly the zone it had implicitly.
    """
    await get_or_create_chat(CHAT_UTC, "group")
    stored = await get_chat(CHAT_UTC)
    assert stored.timezone == TIMEZONE


async def test_migrate_chat_carries_the_timezone(db):
    await _onboarded(CHAT_KYIV, NEW_YORK)
    assert await migrate_chat(CHAT_KYIV, CHAT_NY) is True
    assert (await get_chat(CHAT_NY)).timezone == NEW_YORK


# --- Validation / normalisation ---------------------------------------------

def test_valid_and_invalid_zone_names():
    assert ts.is_valid_timezone(KYIV) is True
    assert ts.is_valid_timezone("UTC") is True
    assert ts.is_valid_timezone("Mars/Olympus") is False
    assert ts.is_valid_timezone("") is False
    assert ts.is_valid_timezone(None) is False


def test_normalize_accepts_common_typing_mistakes():
    assert ts.normalize_timezone("europe/kyiv") == KYIV
    assert ts.normalize_timezone("  Europe/Kyiv  ") == KYIV
    assert ts.normalize_timezone("America/New York") == NEW_YORK
    assert ts.normalize_timezone("utc") == "UTC"


def test_normalize_refuses_to_guess():
    for value in ("Kyiv", "GMT+3", "Europe", "", None, "Mars/Olympus", 17):
        assert ts.normalize_timezone(value) is None


async def test_storing_an_invalid_zone_is_refused(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    assert await set_chat_timezone(PRIVATE_ID, "Mars/Olympus") is False
    assert (await get_chat(PRIVATE_ID)).timezone == TIMEZONE
    # ...and a valid one is stored canonically.
    assert await set_chat_timezone(PRIVATE_ID, "america/new_york") is True
    assert (await get_chat(PRIVATE_ID)).timezone == NEW_YORK


def test_unknown_stored_zone_falls_back_instead_of_raising():
    """A retired/hand-edited value must not be able to break one chat, let alone
    stop the scheduler sweep for the others."""
    broken = SimpleNamespace(timezone="Mars/Olympus")
    assert ts.chat_tz(broken).zone == TIMEZONE
    assert ts.chat_tz(SimpleNamespace(timezone=None)).zone == TIMEZONE
    assert ts.chat_tz(None).zone == TIMEZONE


async def test_sweep_survives_a_chat_with_a_broken_zone(db):
    await _onboarded(CHAT_KYIV, KYIV)
    await _onboarded(CHAT_UTC, "UTC")
    # Bypass the validating setter the way a hand-edited DB would.
    from sqlalchemy import update as sa_update
    import database.db as db_module
    from database.models import Chat
    async with db_module.AsyncSessionLocal() as session:
        await session.execute(
            sa_update(Chat).where(Chat.chat_id == CHAT_KYIV).values(timezone="Mars/Olympus")
        )
        await session.commit()

    bot = FakeBot()
    await scheduler.check_and_send_reminders(bot)  # must not raise
    assert (await get_chat(CHAT_KYIV)).timezone == "Mars/Olympus"


# --- DST --------------------------------------------------------------------

def test_nonexistent_local_time_is_shifted_past_the_gap():
    """
    New York skips 02:00 → 03:00 on 2026-03-08, so 02:30 never happens. A
    reminder set for it must still fire that day, just after the gap.
    """
    tz = pytz.timezone(NEW_YORK)
    moment = ts.combine(tz, datetime.date(2026, 3, 8), datetime.time(2, 30))
    assert moment.date() == datetime.date(2026, 3, 8)
    assert moment.strftime("%H:%M") == "03:30"
    assert moment.utcoffset() == datetime.timedelta(hours=-4)  # already EDT


def test_ambiguous_local_time_resolves_to_the_first_occurrence():
    """
    New York repeats 01:00 → 02:00 on 2026-11-01. We take the earlier (EDT) one
    so a reminder is never an hour late.
    """
    tz = pytz.timezone(NEW_YORK)
    moment = ts.combine(tz, datetime.date(2026, 11, 1), datetime.time(1, 30))
    assert moment.strftime("%H:%M") == "01:30"
    assert moment.utcoffset() == datetime.timedelta(hours=-4)  # EDT, the first pass
    # The later, standard-time pass is a genuinely different instant.
    later = tz.localize(datetime.datetime(2026, 11, 1, 1, 30), is_dst=False)
    assert later > moment


def test_localize_is_a_noop_for_unambiguous_times():
    tz = pytz.timezone(KYIV)
    naive = datetime.datetime(2026, 5, 10, 12, 0)
    assert ts.combine(tz, naive.date(), naive.time()) == tz.localize(naive)


def test_localize_tolerates_a_non_pytz_tzinfo():
    fixed = datetime.timezone(datetime.timedelta(hours=2))
    moment = ts.localize(fixed, datetime.datetime(2026, 5, 10, 12, 0))
    assert moment.utcoffset() == datetime.timedelta(hours=2)


async def test_no_double_reminder_when_the_wall_clock_repeats(db, monkeypatch):
    """
    On a fall-back night the local clock passes the reminder time twice. The
    per-date "already sent" stamp means the second pass sends nothing.
    """
    await _onboarded(CHAT_NY, NEW_YORK, hw_time="01:30")
    tz = pytz.timezone(NEW_YORK)
    local_today = datetime.date(2026, 11, 1)
    await add_homework(CHAT_NY, "Математика", local_today + datetime.timedelta(days=1), "стр. 1")

    bot = FakeBot()
    for is_dst in (True, False):  # the same wall-clock minute, both passes
        moment = tz.localize(datetime.datetime(2026, 11, 1, 1, 30), is_dst=is_dst)
        monkeypatch.setattr(scheduler.ts, "now", lambda chat=None, _m=moment: _m)
        await scheduler.check_and_send_reminders(bot)

    hw_messages = [t for cid, t, _ in bot.sent if cid == CHAT_NY]
    assert len(hw_messages) == 1, "the repeated hour must not send a second reminder"


async def test_reminder_inside_a_spring_forward_gap_still_fires(db, monkeypatch):
    """
    A reminder set for 02:30 on a day when 02:30 doesn't exist: the sweep's
    "wall clock is now at or past the configured time" comparison means it fires
    at 03:00 rather than being skipped for the day.
    """
    await _onboarded(CHAT_NY, NEW_YORK, hw_time="02:30")
    tz = pytz.timezone(NEW_YORK)
    await add_homework(CHAT_NY, "Математика", datetime.date(2026, 3, 9), "стр. 1")

    moment = tz.localize(datetime.datetime(2026, 3, 8, 3, 0))  # first minute after the gap
    monkeypatch.setattr(scheduler.ts, "now", lambda chat=None: moment)

    bot = FakeBot()
    await scheduler.check_and_send_reminders(bot)
    assert [t for cid, t, _ in bot.sent if cid == CHAT_NY], "must not be skipped for the day"


# --- "Today" differs between chats -----------------------------------------

def test_today_differs_between_zones_at_the_same_instant():
    kyiv = FIXED_UTC.astimezone(pytz.timezone(KYIV))
    utc = FIXED_UTC.astimezone(pytz.utc)
    ny = FIXED_UTC.astimezone(pytz.timezone(NEW_YORK))
    assert kyiv.date() == datetime.date(2026, 5, 11)
    assert utc.date() == datetime.date(2026, 5, 10)
    assert ny.date() == datetime.date(2026, 5, 10)


async def test_one_sweep_serves_three_zones_with_their_own_dates(db, monkeypatch):
    """
    A single tick, three chats, three zones. Each must get *its own* "tomorrow":
    Kyiv is already on 11 May (so tomorrow is the 12th) while UTC and New York
    are still on 10 May (tomorrow is the 11th).
    """
    # Reminder times chosen so all three are due at FIXED_UTC in local terms
    # (Kyiv 02:30, UTC 23:30, New York 19:30).
    await _onboarded(CHAT_KYIV, KYIV, hw_time="02:00")
    await _onboarded(CHAT_UTC, "UTC", hw_time="23:00")
    await _onboarded(CHAT_NY, NEW_YORK, hw_time="19:00")

    # Each homework is due on that chat's own local tomorrow.
    await add_homework(CHAT_KYIV, "Киев-ДЗ", datetime.date(2026, 5, 12), "к 12-му")
    await add_homework(CHAT_UTC, "UTC-ДЗ", datetime.date(2026, 5, 11), "к 11-му")
    await add_homework(CHAT_NY, "NY-ДЗ", datetime.date(2026, 5, 11), "к 11-му")

    monkeypatch.setattr(
        scheduler.ts, "now",
        lambda chat=None: FIXED_UTC.astimezone(ts.chat_tz(chat)),
    )

    bot = FakeBot()
    await scheduler.check_and_send_reminders(bot)

    by_chat = {}
    for chat_id, text, _ in bot.sent:
        by_chat.setdefault(chat_id, []).append(text)

    assert "Киев-ДЗ" in "".join(by_chat.get(CHAT_KYIV, [])), by_chat
    assert "12.05" in "".join(by_chat[CHAT_KYIV]), "Kyiv's tomorrow is 12 May"
    assert "UTC-ДЗ" in "".join(by_chat.get(CHAT_UTC, []))
    assert "11.05" in "".join(by_chat[CHAT_UTC]), "UTC's tomorrow is 11 May"
    assert "NY-ДЗ" in "".join(by_chat.get(CHAT_NY, []))
    assert "11.05" in "".join(by_chat[CHAT_NY]), "New York's tomorrow is 11 May"

    # Each chat's "already sent today" stamp uses its own local date.
    assert (await get_chat(CHAT_KYIV)).last_hw_reminder_date == datetime.date(2026, 5, 11)
    assert (await get_chat(CHAT_UTC)).last_hw_reminder_date == datetime.date(2026, 5, 10)
    assert (await get_chat(CHAT_NY)).last_hw_reminder_date == datetime.date(2026, 5, 10)


async def test_chat_in_another_zone_is_not_yet_due(db, monkeypatch):
    """
    The same configured time is a *different* instant per zone: at FIXED_UTC the
    New York chat's local 19:30 has not yet reached a 21:00 reminder.
    """
    await _onboarded(CHAT_NY, NEW_YORK, hw_time="21:00")
    await add_homework(CHAT_NY, "NY-ДЗ", datetime.date(2026, 5, 11), "к 11-му")
    monkeypatch.setattr(
        scheduler.ts, "now",
        lambda chat=None: FIXED_UTC.astimezone(ts.chat_tz(chat)),
    )
    bot = FakeBot()
    await scheduler.check_and_send_reminders(bot)
    assert not bot.sent
    assert (await get_chat(CHAT_NY)).last_hw_reminder_date is None


async def test_extra_activity_reminder_uses_the_chats_zone(db):
    """
    A weekly activity at 18:00 is "in an hour" at 17:00 *local*. Two chats with
    the same activity in different zones must therefore trigger at different
    absolute instants.
    """
    from database.db import add_extra_activity, set_extra_activity_reminder
    await _onboarded(CHAT_NY, NEW_YORK)
    activity = await add_extra_activity(
        CHAT_NY, title="Английский", kind="weekly", start_time="18:00", day_of_week=0
    )
    await set_extra_activity_reminder(CHAT_NY, activity.id, enabled=True, minutes=60)

    ny = pytz.timezone(NEW_YORK)
    monday = datetime.date(2026, 5, 11)  # a Monday
    occ_date, start_dt = scheduler._extra_occurrence(
        await _reload_activity(CHAT_NY, activity.id),
        ny, ny.localize(datetime.datetime.combine(monday, datetime.time(9, 0))),
    )
    assert occ_date == monday
    assert start_dt.strftime("%H:%M") == "18:00"
    assert start_dt.tzinfo.zone == NEW_YORK


async def _reload_activity(chat_id, activity_id):
    from database.db import get_extra_activity_by_id
    return await get_extra_activity_by_id(chat_id, activity_id)


# --- Settings UI ------------------------------------------------------------

async def test_timezone_menu_shows_current_zone_and_local_time(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    cb = FakeCallback("set_tz")
    await settings_handlers.show_timezone(cb, _state())
    text = cb.message.texts[-1]
    assert TIMEZONE in text
    assert "Местное время" in text


async def test_picking_a_popular_zone_previews_before_saving(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    cb = FakeCallback(f"set_tz_pick:{NEW_YORK}")
    await settings_handlers.pick_timezone(cb, _state())
    assert "Местное время сейчас" in cb.message.texts[-1]
    # Nothing is stored until the user confirms.
    assert (await get_chat(PRIVATE_ID)).timezone == TIMEZONE

    save = FakeCallback(f"set_tz_save:{NEW_YORK}")
    await settings_handlers.save_timezone(save, _state())
    assert (await get_chat(PRIVATE_ID)).timezone == NEW_YORK


async def test_manual_entry_accepts_a_valid_zone(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    state = _state()
    await settings_handlers.ask_timezone_manually(FakeCallback("set_tz_manual"), state)
    assert await state.get_state() == settings_handlers.SettingStates.waiting_for_timezone.state

    msg = FakeMessage(text="america/new york")
    await settings_handlers.process_timezone_input(msg, state)
    assert NEW_YORK in msg.texts[-1]
    assert await state.get_state() is None
    # Still only a preview.
    assert (await get_chat(PRIVATE_ID)).timezone == TIMEZONE


async def test_manual_entry_rejects_nonsense_and_keeps_the_step_open(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    state = _state()
    await settings_handlers.ask_timezone_manually(FakeCallback("set_tz_manual"), state)
    msg = FakeMessage(text="Марс/Олимп")
    await settings_handlers.process_timezone_input(msg, state)
    assert "Не знаю такого часового пояса" in msg.texts[-1]
    assert await state.get_state() == settings_handlers.SettingStates.waiting_for_timezone.state
    assert (await get_chat(PRIVATE_ID)).timezone == TIMEZONE


async def test_saving_a_forged_zone_is_refused(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    cb = FakeCallback("set_tz_save:Mars/Olympus")
    await settings_handlers.save_timezone(cb, _state())
    assert cb.alerts and "Неизвестный" in cb.alerts[0]
    assert (await get_chat(PRIVATE_ID)).timezone == TIMEZONE


async def test_group_timezone_change_is_admin_only(db):
    await get_or_create_chat(CHAT_KYIV, "group")
    for handler, data in (
        (settings_handlers.show_timezone, "set_tz"),
        (settings_handlers.pick_timezone, f"set_tz_pick:{NEW_YORK}"),
        (settings_handlers.save_timezone, f"set_tz_save:{NEW_YORK}"),
    ):
        cb = FakeCallback(data, chat_id=CHAT_KYIV, chat_type="group", user_id=MEMBER_ID)
        await handler(cb, _state(CHAT_KYIV))
        assert cb.replies, f"{data} must be refused for a non-admin"
    assert (await get_chat(CHAT_KYIV)).timezone == TIMEZONE


async def test_private_chat_user_may_change_the_timezone(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    cb = FakeCallback(f"set_tz_save:{NEW_YORK}", chat_type="private", user_id=MEMBER_ID)
    await settings_handlers.save_timezone(cb, _state())
    assert (await get_chat(PRIVATE_ID)).timezone == NEW_YORK


async def test_settings_screen_reports_the_zone(db):
    await get_or_create_chat(PRIVATE_ID, "private")
    assert await set_chat_timezone(PRIVATE_ID, NEW_YORK)
    text = await settings_handlers.format_general_settings_message(PRIVATE_ID, "private")
    assert NEW_YORK in text
    kb = await settings_handlers.get_general_settings_keyboard_for_chat(PRIVATE_ID, "private")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any(NEW_YORK in label for label in labels)


def test_tz_label_includes_the_offset():
    label = ts.tz_label(pytz.utc)
    assert "UTC" in label and "+00:00" in label
