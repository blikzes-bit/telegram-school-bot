"""
Covers: stale/past due-date callback buttons, February 29th date math,
deleting a homework from the archive stays in the archive, and malformed /
stale callback_data never crashes a handler.
"""
import datetime
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import get_or_create_chat, add_homework, get_homework, get_homework_by_id
from handlers.homework import (
    process_due_date_callback, process_hw_delete_ask, process_hw_delete_confirm,
    process_hw_page, process_hw_view_actions, process_hw_complete,
    AddHomeworkStates,
)
from utils import next_occurrence, safe_parse_int, safe_callback_ints

CHAT_ID = 800001


class FakeMessage:
    def __init__(self, chat_id, text=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.answers = []
        self.deleted = False

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class FakeCallback:
    def __init__(self, message, data):
        self.message = message
        self.data = data
        self.alerts = []
        self.acks = []

    async def answer(self, *args, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))
        else:
            self.acks.append(args[0] if args else None)


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


# --- Stale "due date" quick-pick button ---

async def test_stale_past_due_date_button_is_rejected(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(AddHomeworkStates.waiting_for_due_date)
    await state.update_data(hw_subject="Math", hw_description="p.1")

    yesterday = (datetime.datetime.now().date() - datetime.timedelta(days=1)).isoformat()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, f"hwa_date:{yesterday}")
    await process_due_date_callback(cb, state)

    assert cb.alerts, "a stale button pointing at a past date must be rejected"
    homeworks = await get_homework(CHAT_ID)
    assert homeworks == [], "no overdue homework should have been created"


async def test_malformed_due_date_callback_does_not_crash(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(AddHomeworkStates.waiting_for_due_date)
    await state.update_data(hw_subject="Math", hw_description="p.1")

    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, "hwa_date:not-a-date")
    await process_due_date_callback(cb, state)  # must not raise

    assert cb.alerts
    assert await get_homework(CHAT_ID) == []


# --- February 29th ---

def test_next_occurrence_finds_next_leap_year():
    # 2026-07-20 is not a leap year context for Feb; next Feb 29 is 2028.
    today = datetime.date(2026, 7, 20)
    result = next_occurrence(2, 29, today)
    assert result == datetime.date(2028, 2, 29)


def test_next_occurrence_same_year_if_still_ahead():
    today = datetime.date(2028, 1, 1)
    result = next_occurrence(2, 29, today)
    assert result == datetime.date(2028, 2, 29)


def test_next_occurrence_regular_date_next_year_if_passed():
    today = datetime.date(2026, 12, 25)
    result = next_occurrence(3, 1, today)
    assert result == datetime.date(2027, 3, 1)


# --- Delete from archive returns to archive ---

async def test_delete_from_archive_returns_to_archive(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 1), "p.1")
    from database.db import mark_homework_completed
    await mark_homework_completed(CHAT_ID, hw.id, is_completed=True)

    msg = FakeMessage(CHAT_ID)
    ask_cb = FakeCallback(msg, f"hw_delete_ask:{hw.id}:1:0")
    await process_hw_delete_ask(ask_cb)

    confirm_cb = FakeCallback(msg, f"hw_delete_confirm:{hw.id}:1:0")
    await process_hw_delete_confirm(confirm_cb)

    assert await get_homework_by_id(CHAT_ID, hw.id) is None
    # The re-rendered list text must be the archive view, not the active one.
    last_text = msg.answers[-1][0]
    assert "Архив" in last_text
    assert "Актуальные" not in last_text


async def test_delete_confirm_on_already_deleted_reports_gone(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 1), "p.1")

    msg = FakeMessage(CHAT_ID)
    confirm_cb = FakeCallback(msg, f"hw_delete_confirm:{hw.id}:0:0")
    await process_hw_delete_confirm(confirm_cb)  # first delete succeeds
    assert not confirm_cb.alerts

    confirm_cb2 = FakeCallback(msg, f"hw_delete_confirm:{hw.id}:0:0")
    await process_hw_delete_confirm(confirm_cb2)  # already gone
    assert confirm_cb2.alerts, "must not silently claim success for a non-existent row"


async def test_complete_already_deleted_reports_gone(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 1), "p.1")
    from database.db import delete_homework
    await delete_homework(CHAT_ID, hw.id)

    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, f"hw_complete:{hw.id}:0")
    await process_hw_complete(cb)
    assert cb.alerts, "completing an already-deleted homework must not report success"


# --- Malformed / stale callback_data across the board ---

async def test_stale_hw_page_callback_data_does_not_crash(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage(CHAT_ID)

    for bad_data in ("hw_page:act:not-a-number", "hw_page:bogus:0", "hw_page:act"):
        cb = FakeCallback(msg, bad_data)
        await process_hw_page(cb)  # must not raise
        assert cb.alerts


async def test_stale_view_actions_callback_data_does_not_crash(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, "hw_view_actions:not-an-id:0:0")
    await process_hw_view_actions(cb, state)  # must not raise
    assert cb.alerts


def test_unicode_superscript_digit_is_rejected_not_crashed():
    """
    '²' (superscript two) looks numeric but int('²') raises ValueError —
    safe_parse_int/safe_callback_ints must swallow that, not propagate it.
    """
    assert safe_parse_int(["5", "²"], 1) is None
    assert safe_callback_ints("hw_page:act:²", 2) is None
    # A well-formed neighbor field is still fine.
    assert safe_callback_ints("hw_complete:5:0", 1, 2) == (5, 0)
