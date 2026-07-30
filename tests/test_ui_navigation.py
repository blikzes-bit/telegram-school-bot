"""
Stage: interface overhaul (prompt 9).

Covers the split of "⏰ Напоминания" (notifications only) and "⚙️ Настройки"
(general), the back-and-forth navigation callbacks between the two screens, the
homework due-date calendar (month navigation, stale token, inert cells) and
backward compatibility of the old ``set_cancel`` callback.
"""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import handlers.homework as hw
import handlers.settings as settings_handlers
from database.db import get_or_create_chat

CHAT_ID = 770_001


class FakeBot:
    def __init__(self, admins=None):
        self.admins = admins or {1}

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(
            status="administrator" if user_id in self.admins else "member"
        )


class FakeMessage:
    def __init__(self, chat_id=CHAT_ID, chat_type="private", user_id=1, text=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name="Аня", first_name="Аня")
        self.bot = FakeBot()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def delete(self):
        pass


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


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


def _labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _callbacks(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


# --- Menu split -------------------------------------------------------------

async def test_reminders_screen_has_only_notifications(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage()
    await settings_handlers.show_reminders(msg, _state())
    text, kwargs = msg.answers[-1]
    assert "Напоминания" in text
    callbacks = _callbacks(kwargs["reply_markup"])
    assert "set_toggle:hw" in callbacks
    assert "set_general" in callbacks
    # General-only entries must not appear on the reminders screen.
    assert "set_tz" not in callbacks
    assert "au_open" not in callbacks
    assert "set_reset_all" not in callbacks


async def test_settings_screen_has_only_general(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage()
    await settings_handlers.show_settings(msg, _state())
    text, kwargs = msg.answers[-1]
    assert "Настройки" in text
    callbacks = _callbacks(kwargs["reply_markup"])
    assert "set_tz" in callbacks
    assert "au_open" in callbacks
    assert "set_reminders" in callbacks
    # Notification toggles must not appear on the general screen.
    assert not any(c.startswith("set_toggle:") for c in callbacks)


async def test_general_and_reminders_callbacks_swap_the_screen(db):
    await get_or_create_chat(CHAT_ID, "private")
    to_general = FakeCallback("set_general")
    await settings_handlers.open_general_settings(to_general, _state())
    assert "set_tz" in _callbacks(to_general.message.answers[-1][1]["reply_markup"])

    to_reminders = FakeCallback("set_reminders")
    await settings_handlers.cancel_settings_edit(to_reminders, _state())
    assert "set_toggle:hw" in _callbacks(to_reminders.message.answers[-1][1]["reply_markup"])


async def test_old_set_cancel_callback_still_returns_to_reminders(db):
    """Backward compatibility: a stale keyboard still carrying ``set_cancel``
    must keep working (it now lands on the reminders screen)."""
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback("set_cancel")
    await settings_handlers.cancel_settings_edit(cb, _state())
    assert "set_toggle:hw" in _callbacks(cb.message.answers[-1][1]["reply_markup"])


# --- Homework due-date calendar --------------------------------------------

async def test_calendar_navigation_renders_the_month(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(hw.AddHomeworkStates.waiting_for_due_date)
    cb = FakeCallback("hwa_cal:2026-09")
    await hw.navigate_due_date_calendar(cb, state)
    text, kwargs = cb.message.answers[-1]
    callbacks = _callbacks(kwargs["reply_markup"])
    # The picked days reuse the existing hwa_date: handler.
    assert any(c.startswith("hwa_date:2026-09-") for c in callbacks)
    # Month navigation stays inside the widget.
    assert any(c.startswith("hwa_cal:") for c in callbacks)


async def test_calendar_navigation_rejects_a_stale_token(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(hw.AddHomeworkStates.waiting_for_due_date)
    cb = FakeCallback("hwa_cal:not-a-month")
    await hw.navigate_due_date_calendar(cb, state)
    assert cb.alerts and "устарел" in cb.alerts[0].lower()
    assert not cb.message.answers


async def test_calendar_noop_is_answered_quietly(db):
    cb = FakeCallback("cal:noop")
    await hw.calendar_noop(cb)
    assert not cb.alerts
    assert not cb.message.answers
