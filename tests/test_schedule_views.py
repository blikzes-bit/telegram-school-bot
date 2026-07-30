"""
Tests for the schedule viewing/editing entry points in handlers/schedule.py:
the day view (``show_schedule``), day selection (valid + stale callback), and
the edit menu (with and without configured lesson slots). Private chat, so the
single user passes the admin gate.
"""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import handlers.schedule as sch
from database.db import (
    get_or_create_chat, set_onboarded, save_lesson_slots, save_schedule_day,
)

CHAT_ID = 991_200


class FakeBot:
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")


class FakeMessage:
    def __init__(self, chat_id=CHAT_ID, chat_type="private", user_id=1, text=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name="Аня")
        self.bot = FakeBot()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(self, data, chat_id=CHAT_ID, chat_type="private", user_id=1):
        self.message = FakeMessage(chat_id, chat_type, user_id)
        self.data = data
        self.from_user = self.message.from_user
        self.bot = self.message.bot
        self.alerts = []
        self.notices = []

    async def answer(self, *args, **kwargs):
        text = args[0] if args else kwargs.get("text")
        (self.alerts if kwargs.get("show_alert") else self.notices).append(text)


def _state():
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))


async def _seed():
    await get_or_create_chat(CHAT_ID, "private")
    await set_onboarded(CHAT_ID, True)
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45"), (2, "08:55", "09:40")])
    await save_schedule_day(CHAT_ID, 0, [(1, "Математика"), (2, "Физика")])


async def test_show_schedule_renders(db):
    await _seed()
    msg = FakeMessage()
    await sch.show_schedule(msg, _state())
    text, kwargs = msg.answers[-1]
    assert "Расписание" in text
    assert "reply_markup" in kwargs


async def test_process_day_select_valid(db):
    await _seed()
    cb = FakeCallback("sch_day:0")
    await sch.process_day_select(cb, _state())
    text, _ = cb.message.answers[-1]
    assert "Математика" in text


async def test_process_day_select_stale(db):
    await _seed()
    cb = FakeCallback("sch_day:99")
    await sch.process_day_select(cb, _state())
    assert cb.alerts and "устарел" in cb.alerts[0].lower()


async def test_edit_schedule_day_lists_lessons(db):
    await _seed()
    cb = FakeCallback("sch_edit:0")
    await sch.edit_schedule_day(cb)
    text, kwargs = cb.message.answers[-1]
    assert "Редактирование" in text
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(c.startswith("se_slot:0:") for c in callbacks)


async def test_edit_schedule_day_without_slots_alerts(db):
    await get_or_create_chat(CHAT_ID, "private")
    await set_onboarded(CHAT_ID, True)
    cb = FakeCallback("sch_edit:0")
    await sch.edit_schedule_day(cb)
    assert cb.alerts and "не настроено" in cb.alerts[0]
