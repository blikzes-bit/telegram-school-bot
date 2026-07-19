"""Handler-level tests for editing an existing homework entry.

Follows the lightweight FakeMessage/FakeCallback pattern used in
test_non_text.py and test_db_flow.py: drive handler functions directly with a
real FSMContext (MemoryStorage) and the real DB fixture, without spinning up
aiogram's Dispatcher.
"""
import datetime
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import get_or_create_chat, add_homework, get_homework_by_id
from handlers.homework import (
    show_edit_menu, initiate_edit_field, process_edit_value, EditHomeworkStates,
)

CHAT_ID = 313131
OTHER_CHAT_ID = 313132


class FakeMessage:
    def __init__(self, chat_id, text=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def delete(self):
        pass


class FakeCallback:
    def __init__(self, message, data):
        self.message = message
        self.data = data
        self.alerts = []

    async def answer(self, *args, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))


def _make_state(chat_id=CHAT_ID):
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id)
    return FSMContext(storage=storage, key=key)


async def _hw(chat_id=CHAT_ID):
    await get_or_create_chat(chat_id, "private")
    return await add_homework(chat_id, "Math", datetime.date(2026, 1, 10), "p.1")


async def _open_field(hw_id, field, chat_id=CHAT_ID, is_archive=0):
    """Drives hw_edit_menu -> hw_edit_field so the FSM state is populated."""
    state = _make_state(chat_id)
    msg = FakeMessage(chat_id)
    cb = FakeCallback(msg, f"hw_edit_field:{hw_id}:{field}:{is_archive}:0")
    await initiate_edit_field(cb, state)
    return state, msg, cb


# --- Successful edit of each field ---

async def test_edit_subject_success(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "subject")
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state

    value_msg = FakeMessage(CHAT_ID, text="Algebra")
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.subject_name == "Algebra"
    assert await state.get_state() is None
    assert any("обновлено" in a[0] for a in value_msg.answers)


async def test_edit_description_success(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "desc")

    value_msg = FakeMessage(CHAT_ID, text="exercises 5-10")
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.description == "exercises 5-10"
    assert await state.get_state() is None


async def test_edit_due_date_success(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "date")

    value_msg = FakeMessage(CHAT_ID, text="20.02")
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.due_date.day == 20
    assert updated.due_date.month == 2
    assert await state.get_state() is None


# --- Invalid date ---

async def test_edit_due_date_invalid_format_keeps_state(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "date")

    value_msg = FakeMessage(CHAT_ID, text="not-a-date")
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    # Unchanged.
    assert updated.due_date == datetime.date(2026, 1, 10)
    # Stays in the same state so the user can retry.
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state
    assert any("Неверный формат" in a[0] for a in value_msg.answers)


async def test_edit_due_date_invalid_day_rejected(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "date")

    value_msg = FakeMessage(CHAT_ID, text="31.02")  # Feb 31 does not exist
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.due_date == datetime.date(2026, 1, 10)
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state


# --- Input too long ---

async def test_edit_subject_too_long_rejected(db):
    from utils import MAX_SUBJECT_LEN
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "subject")

    value_msg = FakeMessage(CHAT_ID, text="x" * (MAX_SUBJECT_LEN + 1))
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.subject_name == "Math"
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state
    assert any("Слишком длинное" in a[0] for a in value_msg.answers)


async def test_edit_description_too_long_rejected(db):
    from utils import MAX_DESCRIPTION_LEN
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "desc")

    value_msg = FakeMessage(CHAT_ID, text="x" * (MAX_DESCRIPTION_LEN + 1))
    await process_edit_value(value_msg, state)

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.description == "p.1"
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state
    assert any("Слишком длинный" in a[0] for a in value_msg.answers)


# --- Foreign chat_id can't edit ---

async def test_foreign_chat_id_cannot_open_edit_menu(db):
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    state = _make_state(OTHER_CHAT_ID)
    msg = FakeMessage(OTHER_CHAT_ID)
    cb = FakeCallback(msg, f"hw_edit_menu:{hw.id}:0:0")
    await show_edit_menu(cb, state)

    assert cb.alerts, "should alert that homework was not found for this chat"
    unchanged = await get_homework_by_id(CHAT_ID, hw.id)
    assert unchanged.subject_name == "Math"


async def test_foreign_chat_id_edit_value_is_noop(db):
    """
    Even if a stale/forged state somehow points at another chat's homework
    id, update_homework's chat_id filter must prevent the write.
    """
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    state = _make_state(OTHER_CHAT_ID)
    await state.update_data(edit_hw_id=hw.id, edit_field="subject", edit_is_archive=0, edit_page=0)
    await state.set_state(EditHomeworkStates.waiting_for_new_value)

    value_msg = FakeMessage(OTHER_CHAT_ID, text="Hijacked")
    await process_edit_value(value_msg, state)

    unchanged = await get_homework_by_id(CHAT_ID, hw.id)
    assert unchanged.subject_name == "Math"
    assert any("не существует" in a[0] for a in value_msg.answers)


# --- Cancel ---

async def test_cancel_clears_state(db):
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "subject")
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state

    # The cancel button's callback_data points back to hw_edit_menu, which
    # must clear the FSM state.
    cancel_msg = FakeMessage(CHAT_ID)
    cancel_cb = FakeCallback(cancel_msg, f"hw_edit_menu:{hw.id}:0:0")
    await show_edit_menu(cancel_cb, state)

    assert await state.get_state() is None
    unchanged = await get_homework_by_id(CHAT_ID, hw.id)
    assert unchanged.subject_name == "Math"


# --- Missing / already-deleted homework ---

async def test_edit_menu_for_deleted_homework(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _make_state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, "hw_edit_menu:999999:0:0")
    await show_edit_menu(cb, state)

    assert cb.alerts
    assert await state.get_state() is None


async def test_edit_field_for_deleted_homework(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _make_state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg, "hw_edit_field:999999:subject:0:0")
    await initiate_edit_field(cb, state)

    assert cb.alerts
    # Must not enter the edit state for a homework that doesn't exist.
    assert await state.get_state() is None


async def test_edit_value_for_meanwhile_deleted_homework(db):
    """Homework existed when the menu opened but was deleted before the
    user typed the new value (e.g. deleted from another device)."""
    from database.db import delete_homework
    hw = await _hw()
    state, msg, cb = await _open_field(hw.id, "subject")

    await delete_homework(CHAT_ID, hw.id)

    value_msg = FakeMessage(CHAT_ID, text="Algebra")
    await process_edit_value(value_msg, state)

    assert await get_homework_by_id(CHAT_ID, hw.id) is None
    assert any("не существует" in a[0] for a in value_msg.answers)
