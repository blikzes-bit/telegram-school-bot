"""
Handler-level tests for 🎯 Доп. занятия. Drives the handler functions directly
with lightweight fakes and a real FSMContext (MemoryStorage) over the in-memory
DB fixture — the same pattern as test_homework_edit_handler.py.

Covers: the full add flow (weekly + once), admin gating in groups, stale/broken
callback_data, non-text FSM input, editing, delete-with-confirmation and
chat_id isolation for edit/delete.
"""
import datetime
from types import SimpleNamespace

import pytz
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from config import TIMEZONE
from database.db import get_or_create_chat, add_extra_activity, get_extra_activities, get_extra_activity_by_id
from handlers.extra import (
    ExtraActivityStates, extra_non_text,
    process_extra_add, process_extra_kind, process_extra_day, process_extra_date,
    process_extra_title, process_extra_time, process_extra_location, process_extra_note,
    process_extra_view, process_extra_edit_menu, process_extra_edit_field,
    process_extra_edit_value, process_extra_set_day,
    process_extra_delete_ask, process_extra_delete_confirm,
)

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 707070
OTHER_CHAT_ID = 707071
GROUP_ID = -100700700
ADMIN_ID = 111
MEMBER_ID = 222


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

    async def delete(self):
        pass


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
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id)
    return FSMContext(storage=storage, key=key)


# --- Full add flow: weekly --------------------------------------------------

async def test_add_weekly_full_flow(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()

    await process_extra_add(FakeCallback(CHAT_ID, "ea_add"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_kind.state

    await process_extra_kind(FakeCallback(CHAT_ID, "ea_kind:weekly"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_day.state

    await process_extra_day(FakeCallback(CHAT_ID, "ea_day:1"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_title.state

    await process_extra_title(FakeMessage(CHAT_ID, text="Английский"), state)
    await process_extra_time(FakeMessage(CHAT_ID, text="18:00 - 19:00"), state)
    await process_extra_location(FakeMessage(CHAT_ID, text="Каб. 5"), state)
    await process_extra_note(FakeMessage(CHAT_ID, text="принести тетрадь"), state)

    assert await state.get_state() is None
    activities = await get_extra_activities(CHAT_ID)
    assert len(activities) == 1
    a = activities[0]
    assert (a.title, a.kind, a.day_of_week, a.start_time, a.end_time) == \
        ("Английский", "weekly", 1, "18:00", "19:00")
    assert a.location == "Каб. 5"
    assert a.note == "принести тетрадь"
    assert a.activity_date is None


# --- Full add flow: once, single time, skipped optional fields --------------

async def test_add_once_full_flow_with_skips(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()

    await process_extra_add(FakeCallback(CHAT_ID, "ea_add"), state)
    await process_extra_kind(FakeCallback(CHAT_ID, "ea_kind:once"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_date.state

    await process_extra_date(FakeMessage(CHAT_ID, text="14.10"), state)
    await process_extra_title(FakeMessage(CHAT_ID, text="Репетитор"), state)
    await process_extra_time(FakeMessage(CHAT_ID, text="16:00"), state)   # no end time
    await process_extra_location(FakeMessage(CHAT_ID, text="-"), state)   # skip
    await process_extra_note(FakeMessage(CHAT_ID, text="нет"), state)     # skip

    a = (await get_extra_activities(CHAT_ID))[0]
    assert a.kind == "once"
    assert a.day_of_week is None
    assert a.activity_date is not None and a.activity_date.day == 14 and a.activity_date.month == 10
    assert a.start_time == "16:00"
    assert a.end_time is None
    assert a.location is None
    assert a.note is None


# --- Validation inside the flow ---------------------------------------------

async def test_add_bad_time_keeps_state(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_time)
    await state.update_data(kind="weekly", day_of_week=1, title="X")

    msg = FakeMessage(CHAT_ID, text="25:99")
    await process_extra_time(msg, state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_time.state
    assert await get_extra_activities(CHAT_ID) == []


async def test_add_bad_date_keeps_state(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_date)
    await state.update_data(kind="once")

    await process_extra_date(FakeMessage(CHAT_ID, text="not-a-date"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_date.state


async def test_add_empty_title_keeps_state(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_title)
    await state.update_data(kind="weekly", day_of_week=1)

    await process_extra_title(FakeMessage(CHAT_ID, text="   "), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_title.state


async def test_add_too_long_title_keeps_state(db):
    from utils import MAX_TITLE_LEN
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_title)
    await state.update_data(kind="weekly", day_of_week=1)

    await process_extra_title(FakeMessage(CHAT_ID, text="x" * (MAX_TITLE_LEN + 1)), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_title.state


# --- Admin gating in groups -------------------------------------------------

async def test_group_member_cannot_add(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    cb = FakeCallback(GROUP_ID, "ea_add", chat_type="group", user_id=MEMBER_ID, bot=fake_bot)
    await process_extra_add(cb, state)
    assert cb.answers, "member must be told it's admin-only"
    assert await state.get_state() is None  # flow never started


async def test_group_admin_can_add(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    cb = FakeCallback(GROUP_ID, "ea_add", chat_type="group", user_id=ADMIN_ID, bot=fake_bot)
    await process_extra_add(cb, state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_kind.state


async def test_group_member_cannot_delete(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await get_or_create_chat(GROUP_ID, "group")
    a = await add_extra_activity(GROUP_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)
    cb = FakeCallback(GROUP_ID, f"ea_delete_confirm:{a.id}", chat_type="group", user_id=MEMBER_ID, bot=fake_bot)
    await process_extra_delete_confirm(cb)
    assert cb.answers
    assert await get_extra_activity_by_id(GROUP_ID, a.id) is not None  # not deleted


async def test_private_chat_can_manage_without_admin(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    cb = FakeCallback(CHAT_ID, "ea_add", chat_type="private", user_id=MEMBER_ID, bot=None)
    await process_extra_add(cb, state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_kind.state


# --- Stale / malformed callback_data ----------------------------------------

async def test_view_stale_callback(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback(CHAT_ID, "ea_view:notanint")
    await process_extra_view(cb, _state())
    assert cb.alerts


async def test_kind_invalid_value(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_kind)
    cb = FakeCallback(CHAT_ID, "ea_kind:bogus")
    await process_extra_kind(cb, state)
    assert cb.alerts


async def test_day_out_of_range(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_day)
    await state.update_data(kind="weekly")
    cb = FakeCallback(CHAT_ID, "ea_day:9")
    await process_extra_day(cb, state)
    assert cb.alerts


async def test_view_missing_activity(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback(CHAT_ID, "ea_view:999999")
    await process_extra_view(cb, _state())
    assert cb.alerts


# --- Non-text fallback ------------------------------------------------------

async def test_non_text_keeps_state(db):
    state = _state()
    await state.set_state(ExtraActivityStates.waiting_for_title)
    msg = FakeMessage(CHAT_ID, text=None)  # e.g. a photo
    await extra_non_text(msg)
    assert msg.answers
    assert "текст" in msg.answers[0][0].lower()


# --- Edit flow --------------------------------------------------------------

async def test_edit_title(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="Old", kind="weekly", start_time="10:00", day_of_week=1)
    state = _state()

    await process_extra_edit_menu(FakeCallback(CHAT_ID, f"ea_edit_menu:{a.id}"), state)
    await process_extra_edit_field(FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:title"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_edit_value.state

    await process_extra_edit_value(FakeMessage(CHAT_ID, text="New"), state)
    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).title == "New"
    assert await state.get_state() is None


async def test_edit_time_clears_end(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", end_time="11:00", day_of_week=1)
    state = _state()
    await process_extra_edit_field(FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:time"), state)
    await process_extra_edit_value(FakeMessage(CHAT_ID, text="09:00"), state)
    updated = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert updated.start_time == "09:00"
    assert updated.end_time is None


async def test_edit_weekly_when_shows_day_picker_and_sets_day(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)
    state = _state()
    # "when" for a weekly activity must NOT enter a text state (uses day picker).
    await process_extra_edit_field(FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:when"), state)
    assert await state.get_state() is None
    await process_extra_set_day(FakeCallback(CHAT_ID, f"ea_setday:{a.id}:4"))
    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).day_of_week == 4


async def test_edit_once_when_edits_date(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="once", start_time="10:00",
                                 activity_date=datetime.date(2026, 10, 14))
    state = _state()
    await process_extra_edit_field(FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:when"), state)
    assert await state.get_state() == ExtraActivityStates.waiting_for_edit_value.state
    data = await state.get_data()
    assert data["edit_field"] == "date"
    await process_extra_edit_value(FakeMessage(CHAT_ID, text="20.11"), state)
    updated = await get_extra_activity_by_id(CHAT_ID, a.id)
    assert updated.activity_date.day == 20 and updated.activity_date.month == 11


async def test_edit_clear_location(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1, location="Old")
    state = _state()
    await process_extra_edit_field(FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:location"), state)
    await process_extra_edit_value(FakeMessage(CHAT_ID, text="-"), state)
    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).location is None


async def test_edit_bad_field_is_stale(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)
    cb = FakeCallback(CHAT_ID, f"ea_edit_field:{a.id}:bogus")
    await process_extra_edit_field(cb, _state())
    assert cb.alerts


# --- Delete with confirmation -----------------------------------------------

async def test_delete_ask_then_confirm(db):
    await get_or_create_chat(CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)

    ask = FakeCallback(CHAT_ID, f"ea_delete_ask:{a.id}")
    await process_extra_delete_ask(ask)
    # Still there — only asked for confirmation.
    assert await get_extra_activity_by_id(CHAT_ID, a.id) is not None

    confirm = FakeCallback(CHAT_ID, f"ea_delete_confirm:{a.id}")
    await process_extra_delete_confirm(confirm)
    assert await get_extra_activity_by_id(CHAT_ID, a.id) is None


# --- chat_id isolation on edit / delete -------------------------------------

async def test_foreign_chat_cannot_edit_menu(db):
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)

    cb = FakeCallback(OTHER_CHAT_ID, f"ea_edit_menu:{a.id}")
    await process_extra_edit_menu(cb, _state(OTHER_CHAT_ID))
    assert cb.alerts
    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).title == "X"


async def test_foreign_chat_edit_value_noop(db):
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)

    state = _state(OTHER_CHAT_ID)
    await state.update_data(edit_id=a.id, edit_field="title")
    await state.set_state(ExtraActivityStates.waiting_for_edit_value)
    msg = FakeMessage(OTHER_CHAT_ID, text="Hijacked")
    await process_extra_edit_value(msg, state)

    assert (await get_extra_activity_by_id(CHAT_ID, a.id)).title == "X"
    assert any("не существует" in a[0] for a in msg.answers)


async def test_foreign_chat_delete_noop(db):
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    a = await add_extra_activity(CHAT_ID, title="X", kind="weekly", start_time="10:00", day_of_week=1)

    cb = FakeCallback(OTHER_CHAT_ID, f"ea_delete_confirm:{a.id}")
    await process_extra_delete_confirm(cb)
    assert await get_extra_activity_by_id(CHAT_ID, a.id) is not None
