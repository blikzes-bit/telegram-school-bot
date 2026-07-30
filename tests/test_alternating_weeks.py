"""
Alternating (A/B, "чётная/нечётная") week tests: the pure week resolver
(A→B transition, year boundary, anchor date, dates before the anchor), the
DB layer (week-scoped schedule, copy into A/B, enable/disable, migration),
the effective-schedule service (right week auto-selected, legacy ``all`` rows,
date-override priority), the tomorrow reminder, and the editor handlers.
"""
import datetime
from types import SimpleNamespace

import pytz
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from config import TIMEZONE
from database.db import (
    get_or_create_chat, get_chat, set_week_mode, copy_schedule_week,
    save_lesson_slots, save_schedule_day, get_schedule, migrate_chat,
    set_lesson_override,
)
from services.effective_schedule import resolve_week_type, get_effective_day
from services.scheduler import send_schedule_reminder

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 717171
MONDAY_A = datetime.date(2026, 1, 5)  # a Monday


def test_monday_a_is_monday():
    assert MONDAY_A.weekday() == 0


# --- Pure resolver ----------------------------------------------------------

def test_mode_off_always_all():
    assert resolve_week_type(False, MONDAY_A, datetime.date(2026, 1, 7)) == "all"


def test_no_anchor_is_all():
    assert resolve_week_type(True, None, datetime.date(2026, 1, 7)) == "all"


def test_anchor_week_is_a_next_is_b():
    # Whole anchor week (Mon..Sun) → A.
    for offset in range(7):
        assert resolve_week_type(True, MONDAY_A, MONDAY_A + datetime.timedelta(days=offset)) == "A"
    # The following week → B, then A again.
    assert resolve_week_type(True, MONDAY_A, MONDAY_A + datetime.timedelta(days=7)) == "B"
    assert resolve_week_type(True, MONDAY_A, MONDAY_A + datetime.timedelta(days=14)) == "A"
    assert resolve_week_type(True, MONDAY_A, MONDAY_A + datetime.timedelta(days=21)) == "B"


def test_a_b_transition_across_year_boundary():
    anchor = datetime.date(2025, 12, 29)  # Monday
    assert anchor.weekday() == 0
    assert resolve_week_type(True, anchor, datetime.date(2025, 12, 31)) == "A"  # same week
    assert resolve_week_type(True, anchor, datetime.date(2026, 1, 5)) == "B"    # next week (new year)
    assert resolve_week_type(True, anchor, datetime.date(2026, 1, 12)) == "A"


def test_dates_before_anchor_alternate_too():
    assert resolve_week_type(True, MONDAY_A, MONDAY_A - datetime.timedelta(days=7)) == "B"
    assert resolve_week_type(True, MONDAY_A, MONDAY_A - datetime.timedelta(days=14)) == "A"


def test_resolution_is_stable_within_a_week():
    # Every day of a given week must resolve to the same type (tz-independent).
    week_start = MONDAY_A + datetime.timedelta(days=7)  # a B week
    types = {resolve_week_type(True, MONDAY_A, week_start + datetime.timedelta(days=d)) for d in range(7)}
    assert types == {"B"}


# --- DB layer ---------------------------------------------------------------

async def _setup(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    await save_lesson_slots(chat_id, [(1, "08:00", "08:45")])


async def test_schedule_week_scoping(db):
    await _setup()
    await save_schedule_day(CHAT_ID, 1, [(1, "A-Math")], week_type="A")
    await save_schedule_day(CHAT_ID, 1, [(1, "B-Math")], week_type="B")
    a = await get_schedule(CHAT_ID, 1, week_type="A")
    b = await get_schedule(CHAT_ID, 1, week_type="B")
    all_rows = await get_schedule(CHAT_ID, 1, week_type="all")
    assert [r.subject_name for r in a] == ["A-Math"]
    assert [r.subject_name for r in b] == ["B-Math"]
    assert all_rows == []  # untouched


async def test_copy_schedule_week(db):
    await _setup()
    await save_schedule_day(CHAT_ID, 1, [(1, "Математика")], week_type="all")
    copied = await copy_schedule_week(CHAT_ID, "all", "A")
    assert copied == 1
    a = await get_schedule(CHAT_ID, 1, week_type="A")
    assert [r.subject_name for r in a] == ["Математика"]
    # Copy overwrites the destination each time (no duplication).
    await copy_schedule_week(CHAT_ID, "all", "A")
    a2 = await get_schedule(CHAT_ID, 1, week_type="A")
    assert len(a2) == 1


async def test_set_week_mode_and_get_chat(db):
    await _setup()
    await set_week_mode(CHAT_ID, True, anchor_monday=MONDAY_A)
    chat = await get_chat(CHAT_ID)
    assert chat.week_mode is True
    assert chat.week_anchor_monday == MONDAY_A
    await set_week_mode(CHAT_ID, False)
    chat = await get_chat(CHAT_ID)
    assert chat.week_mode is False
    # Anchor retained so re-enabling keeps the same A/B phase.
    assert chat.week_anchor_monday == MONDAY_A


async def test_migrate_chat_carries_week_settings(db):
    old_id, new_id = -7001, -8002
    await get_or_create_chat(old_id, "group")
    await set_week_mode(old_id, True, anchor_monday=MONDAY_A)
    await save_schedule_day(old_id, 1, [(1, "A-Math")], week_type="A")
    assert await migrate_chat(old_id, new_id) is True
    chat = await get_chat(new_id)
    assert chat.week_mode is True and chat.week_anchor_monday == MONDAY_A
    assert [r.subject_name for r in await get_schedule(new_id, 1, week_type="A")] == ["A-Math"]


# --- Effective schedule -----------------------------------------------------

async def test_effective_day_selects_a_then_b(db):
    await _setup()
    await set_week_mode(CHAT_ID, True, anchor_monday=MONDAY_A)
    # Tuesday of week A and the Tuesday one week later (week B).
    tue_a = MONDAY_A + datetime.timedelta(days=1)
    tue_b = tue_a + datetime.timedelta(days=7)
    await save_schedule_day(CHAT_ID, 1, [(1, "A-Math")], week_type="A")
    await save_schedule_day(CHAT_ID, 1, [(1, "B-Math")], week_type="B")

    eff_a = await get_effective_day(CHAT_ID, tue_a)
    eff_b = await get_effective_day(CHAT_ID, tue_b)
    assert eff_a.lessons[0].subject_name == "A-Math"
    assert eff_b.lessons[0].subject_name == "B-Math"


async def test_effective_day_legacy_all_when_mode_off(db):
    await _setup()
    # A/B rows exist but the chat does NOT use alternating weeks → 'all' wins.
    await save_schedule_day(CHAT_ID, 1, [(1, "A-Math")], week_type="A")
    await save_schedule_day(CHAT_ID, 1, [(1, "Обычная")], week_type="all")
    tue = MONDAY_A + datetime.timedelta(days=1)
    eff = await get_effective_day(CHAT_ID, tue)
    assert eff.lessons[0].subject_name == "Обычная"


async def test_date_override_beats_week_template(db):
    await _setup()
    await set_week_mode(CHAT_ID, True, anchor_monday=MONDAY_A)
    tue_a = MONDAY_A + datetime.timedelta(days=1)
    await save_schedule_day(CHAT_ID, 1, [(1, "A-Math")], week_type="A")
    # A per-date override on that Tuesday must win over the week-A template.
    await set_lesson_override(CHAT_ID, tue_a, 1, "set", subject_name="Замена")
    eff = await get_effective_day(CHAT_ID, tue_a)
    assert eff.lessons[0].subject_name == "Замена"
    assert eff.lessons[0].subject_changed is True


# --- Reminder ---------------------------------------------------------------

async def test_reminder_uses_correct_week(db, fake_bot):
    await get_or_create_chat(CHAT_ID, "private")
    from database.db import set_onboarded
    await set_onboarded(CHAT_ID, True)
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])

    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_monday = tomorrow - datetime.timedelta(days=tomorrow.weekday())
    # Anchor one week before tomorrow's Monday → tomorrow falls in week B.
    await set_week_mode(CHAT_ID, True, anchor_monday=tomorrow_monday - datetime.timedelta(days=7))
    await save_schedule_day(CHAT_ID, tomorrow.weekday(), [(1, "A-Урок")], week_type="A")
    await save_schedule_day(CHAT_ID, tomorrow.weekday(), [(1, "B-Урок")], week_type="B")

    handled = await send_schedule_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    text = fake_bot.sent[0][1]
    assert "B-Урок" in text
    assert "A-Урок" not in text


# --- Handlers ---------------------------------------------------------------

class FakeMessage:
    def __init__(self, chat_id, text=None, chat_type="private", user_id=1, bot=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = bot
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(self, chat_id, data, chat_type="private", user_id=1, bot=None):
        self.message = FakeMessage(chat_id, chat_type=chat_type, user_id=user_id, bot=bot)
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = bot
        self.answers = []
        self.alerts = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


async def test_enable_sets_mode_and_anchor(db):
    from handlers.schedule import enable_week_mode
    await _setup()
    await enable_week_mode(FakeCallback(CHAT_ID, "sch_wk_on"), _state())
    chat = await get_chat(CHAT_ID)
    today = datetime.datetime.now(tz).date()
    assert chat.week_mode is True
    assert chat.week_anchor_monday == today - datetime.timedelta(days=today.weekday())


async def test_copy_ab_via_handler(db):
    from handlers.schedule import copy_regular_into_ab
    await _setup()
    await save_schedule_day(CHAT_ID, 1, [(1, "Математика")], week_type="all")
    await set_week_mode(CHAT_ID, True, anchor_monday=MONDAY_A)
    await copy_regular_into_ab(FakeCallback(CHAT_ID, "sch_copy_ab"), _state())
    assert [r.subject_name for r in await get_schedule(CHAT_ID, 1, week_type="A")] == ["Математика"]
    assert [r.subject_name for r in await get_schedule(CHAT_ID, 1, week_type="B")] == ["Математика"]


async def test_edit_targets_selected_week(db):
    from handlers.schedule import initiate_slot_edit, process_new_subject_name
    await _setup()
    await set_week_mode(CHAT_ID, True, anchor_monday=MONDAY_A)
    state = _state()
    # Edit lesson 1 on Tuesday, explicitly week B.
    await initiate_slot_edit(FakeCallback(CHAT_ID, "se_slot:1:1:B"), state)
    await process_new_subject_name(FakeMessage(CHAT_ID, text="B-Only"), state)
    assert [r.subject_name for r in await get_schedule(CHAT_ID, 1, week_type="B")] == ["B-Only"]
    # Week A and the legacy 'all' template are untouched.
    assert await get_schedule(CHAT_ID, 1, week_type="A") == []
    assert await get_schedule(CHAT_ID, 1, week_type="all") == []


async def test_group_member_cannot_enable(db, fake_bot):
    from handlers.schedule import enable_week_mode
    fake_bot.admins = {999}
    await get_or_create_chat(-100717, "group")
    cb = FakeCallback(-100717, "sch_wk_on", chat_type="group", user_id=222, bot=fake_bot)
    await enable_week_mode(cb, _state(-100717))
    assert cb.answers  # admin-only rejection
    chat = await get_chat(-100717)
    assert chat.week_mode is False
