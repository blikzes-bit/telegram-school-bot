"""
Handler-level tests for "🗓 Изменения по датам": admin gating in groups, the
preview-then-save flow for cancel / replace / retime / add-lesson / day-type,
clear-with-confirmation, chat isolation, stale callbacks and non-text input.
"""
import datetime
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import (
    get_or_create_chat, set_onboarded, save_lesson_slots, save_schedule_day,
    set_lesson_override, get_lesson_overrides, get_day_override,
)
from handlers.date_overrides import (
    DateOverrideStates, date_override_non_text,
    do_menu, do_days, do_date, do_pick, do_lesson, do_add,
    process_subject, process_time, process_add_time,
    do_dtype, do_setdtype, process_day_note, do_save,
    do_clear_ask, do_clear_yes,
)

CHAT_ID = 141414
OTHER_CHAT_ID = 141415
GROUP_ID = -100141414
ADMIN_ID = 111
MEMBER_ID = 222
DATE = datetime.date(2026, 9, 14)  # Monday (weekday 0)
ISO = DATE.isoformat()


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
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id)
    return FSMContext(storage=storage, key=key)


async def _setup(chat_id=CHAT_ID, day_of_week=0):
    await get_or_create_chat(chat_id, "private")
    await set_onboarded(chat_id, True)
    await save_lesson_slots(chat_id, [(1, "08:00", "08:45"), (2, "08:55", "09:40")])
    await save_schedule_day(chat_id, day_of_week, [(1, "Математика"), (2, "Физика")])


# --- Admin gating -----------------------------------------------------------

async def test_group_member_cannot_open_menu(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await get_or_create_chat(GROUP_ID, "group")
    cb = FakeCallback(GROUP_ID, "do_menu", chat_type="group", user_id=MEMBER_ID, bot=fake_bot)
    await do_menu(cb, _state(GROUP_ID))
    assert cb.answers  # told it's admin-only
    assert cb.message.answers == []  # menu never rendered


async def test_group_admin_can_open_menu(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await get_or_create_chat(GROUP_ID, "group")
    cb = FakeCallback(GROUP_ID, "do_menu", chat_type="group", user_id=ADMIN_ID, bot=fake_bot)
    await do_menu(cb, _state(GROUP_ID))
    assert cb.message.answers  # grid rendered
    assert not cb.alerts


async def test_group_member_cannot_save(db, fake_bot):
    fake_bot.admins = {ADMIN_ID}
    await _setup(GROUP_ID)
    cb = FakeCallback(GROUP_ID, f"do_save:{ISO}", chat_type="group", user_id=MEMBER_ID, bot=fake_bot)
    await do_save(cb, _state(GROUP_ID))
    assert cb.answers  # told it's admin-only
    assert await get_lesson_overrides(GROUP_ID, DATE) == []


# --- Cancel flow ------------------------------------------------------------

async def test_cancel_flow_preview_then_save(db):
    await _setup()
    state = _state()

    await do_pick(FakeCallback(CHAT_ID, f"do_pick:{ISO}:cancel"), state)
    # pick lesson 1 → shows preview and stores the pending change
    await do_lesson(FakeCallback(CHAT_ID, f"do_lesson:{ISO}:cancel:1"), state)
    data = await state.get_data()
    assert data["pending"]["action"] == "cancel"
    # nothing persisted yet
    assert await get_lesson_overrides(CHAT_ID, DATE) == []

    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)
    rows = await get_lesson_overrides(CHAT_ID, DATE)
    assert len(rows) == 1 and rows[0].action == "cancel" and rows[0].lesson_number == 1


# --- Replace flow -----------------------------------------------------------

async def test_replace_flow(db):
    await _setup()
    state = _state()
    await do_lesson(FakeCallback(CHAT_ID, f"do_lesson:{ISO}:replace:1"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_subject.state

    await process_subject(FakeMessage(CHAT_ID, text="Химия"), state)
    assert (await state.get_data())["pending"]["subject"] == "Химия"

    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)
    rows = await get_lesson_overrides(CHAT_ID, DATE)
    assert rows[0].action == "set" and rows[0].subject_name == "Химия"


# --- Retime flow ------------------------------------------------------------

async def test_retime_flow(db):
    await _setup()
    state = _state()
    await do_lesson(FakeCallback(CHAT_ID, f"do_lesson:{ISO}:retime:1"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_time.state

    await process_time(FakeMessage(CHAT_ID, text="09:00 - 09:45"), state)
    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)
    rows = await get_lesson_overrides(CHAT_ID, DATE)
    assert (rows[0].start_time, rows[0].end_time) == ("09:00", "09:45")


async def test_retime_bad_time_keeps_state(db):
    await _setup()
    state = _state()
    await do_lesson(FakeCallback(CHAT_ID, f"do_lesson:{ISO}:retime:1"), state)
    await process_time(FakeMessage(CHAT_ID, text="25:99"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_time.state
    assert await get_lesson_overrides(CHAT_ID, DATE) == []


# --- Add one-off lesson -----------------------------------------------------

async def test_add_lesson_flow(db):
    await _setup()
    state = _state()
    await do_add(FakeCallback(CHAT_ID, f"do_add:{ISO}"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_subject.state

    await process_subject(FakeMessage(CHAT_ID, text="Кружок"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_add_time.state

    await process_add_time(FakeMessage(CHAT_ID, text="15:00 - 15:45"), state)
    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)

    rows = await get_lesson_overrides(CHAT_ID, DATE)
    assert len(rows) == 1
    added = rows[0]
    assert added.lesson_number == 3  # next after the two template slots
    assert added.subject_name == "Кружок"
    assert (added.start_time, added.end_time) == ("15:00", "15:45")


# --- Day type flow ----------------------------------------------------------

async def test_day_type_free_flow(db):
    await _setup()
    state = _state()
    await do_dtype(FakeCallback(CHAT_ID, f"do_dtype:{ISO}"), state)
    await do_setdtype(FakeCallback(CHAT_ID, f"do_setdtype:{ISO}:free"), state)
    assert await state.get_state() == DateOverrideStates.waiting_for_day_note.state

    await process_day_note(FakeMessage(CHAT_ID, text="Семейный день"), state)
    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)

    row = await get_day_override(CHAT_ID, DATE)
    assert row.day_type == "free" and row.note == "Семейный день"


async def test_day_type_note_skipped(db):
    await _setup()
    state = _state()
    await do_setdtype(FakeCallback(CHAT_ID, f"do_setdtype:{ISO}:holiday"), state)
    await process_day_note(FakeMessage(CHAT_ID, text="-"), state)
    await do_save(FakeCallback(CHAT_ID, f"do_save:{ISO}"), state)
    row = await get_day_override(CHAT_ID, DATE)
    assert row.day_type == "holiday" and row.note is None


# --- Clear with confirmation ------------------------------------------------

async def test_clear_with_confirmation(db):
    await _setup()
    await set_lesson_override(CHAT_ID, DATE, 1, "cancel")
    state = _state()

    await do_clear_ask(FakeCallback(CHAT_ID, f"do_clear_ask:{ISO}"), state)
    # Not cleared yet — only asked.
    assert len(await get_lesson_overrides(CHAT_ID, DATE)) == 1

    await do_clear_yes(FakeCallback(CHAT_ID, f"do_clear_yes:{ISO}"), state)
    assert await get_lesson_overrides(CHAT_ID, DATE) == []


# --- Chat isolation on save -------------------------------------------------

async def test_save_scoped_to_chat(db):
    await _setup(CHAT_ID)
    await _setup(OTHER_CHAT_ID)
    state = _state(OTHER_CHAT_ID)
    # Build a pending change while acting as OTHER_CHAT_ID.
    await do_lesson(FakeCallback(OTHER_CHAT_ID, f"do_lesson:{ISO}:cancel:1"), state)
    await do_save(FakeCallback(OTHER_CHAT_ID, f"do_save:{ISO}"), state)
    # Only the acting chat gets the override; CHAT_ID stays clean.
    assert len(await get_lesson_overrides(OTHER_CHAT_ID, DATE)) == 1
    assert await get_lesson_overrides(CHAT_ID, DATE) == []


# --- Stale / malformed callbacks --------------------------------------------

async def test_bad_iso_is_stale(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback(CHAT_ID, "do_date:not-a-date")
    await do_date(cb, _state())
    assert cb.alerts


async def test_bad_lesson_num_is_stale(db):
    await _setup()
    cb = FakeCallback(CHAT_ID, f"do_lesson:{ISO}:cancel:notanint")
    await do_lesson(cb, _state())
    assert cb.alerts


async def test_bad_days_offset_is_stale(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback(CHAT_ID, "do_days:notanint")
    await do_days(cb, _state())
    assert cb.alerts


async def test_save_without_pending_is_stale(db):
    await _setup()
    cb = FakeCallback(CHAT_ID, f"do_save:{ISO}")
    await do_save(cb, _state())  # fresh state → no pending
    assert cb.alerts


# --- Non-text fallback ------------------------------------------------------

async def test_non_text_fallback(db):
    msg = FakeMessage(CHAT_ID, text=None)
    await date_override_non_text(msg)
    assert msg.answers
    assert "текст" in msg.answers[0][0].lower()
