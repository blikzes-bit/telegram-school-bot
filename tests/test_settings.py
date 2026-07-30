"""
Behavioural tests for handlers/settings.py — the reminder-time FSM flows, the
category toggle, the timezone picker (preview + save + manual entry), the
homework-edit policy, quiet hours and the full reset. Everything runs in a
private chat, where the single user is always allowed, so the server-side
``require_admin`` gate passes and the flow itself is exercised.
"""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import handlers.settings as s
from database.db import get_chat, get_or_create_chat

CHAT_ID = 990_100


class FakeBot:
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")


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


def _state():
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID))


def _callbacks(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


# --- Reminder-time FSM flows ------------------------------------------------

async def test_process_hw_time_valid_normalizes_and_saves(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage(text="8:05")
    await s.process_hw_time(msg, _state())
    chat = await get_chat(CHAT_ID)
    assert chat.hw_reminder_time == "08:05"


async def test_process_hw_time_rejects_bad_format(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage(text="25:99")
    await s.process_hw_time(msg, _state())
    assert "Неверный формат" in msg.answers[-1][0]


async def test_process_sch_time_valid(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage(text="20:30")
    await s.process_sch_time(msg, _state())
    assert (await get_chat(CHAT_ID)).schedule_reminder_time == "20:30"


async def test_process_duetoday_time_valid(db):
    await get_or_create_chat(CHAT_ID, "private")
    msg = FakeMessage(text="7:05")
    await s.process_duetoday_time(msg, _state())
    assert (await get_chat(CHAT_ID)).hw_duetoday_time == "07:05"


async def test_process_quiet_hours_set_off_and_bad(db):
    await get_or_create_chat(CHAT_ID, "private")
    on = FakeMessage(text="22:00-07:00")
    await s.process_quiet_hours(on, _state())
    chat = await get_chat(CHAT_ID)
    assert chat.quiet_start == "22:00" and chat.quiet_end == "07:00"

    off = FakeMessage(text="выкл")
    await s.process_quiet_hours(off, _state())
    chat = await get_chat(CHAT_ID)
    assert chat.quiet_start is None

    equal = FakeMessage(text="10:00-10:00")
    await s.process_quiet_hours(equal, _state())
    assert "не могут совпадать" in equal.answers[-1][0]

    bad = FakeMessage(text="nonsense")
    await s.process_quiet_hours(bad, _state())
    assert "Неверный формат" in bad.answers[-1][0]


# --- Category toggle --------------------------------------------------------

async def test_toggle_category_flips_flag(db):
    await get_or_create_chat(CHAT_ID, "private")
    before = (await get_chat(CHAT_ID)).hw_reminder_enabled
    cb = FakeCallback("set_toggle:hw")
    await s.toggle_category(cb, _state())
    assert (await get_chat(CHAT_ID)).hw_reminder_enabled is (not before)


async def test_toggle_category_unknown_alerts(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback("set_toggle:bogus")
    await s.toggle_category(cb, _state())
    assert cb.alerts


# --- Timezone flow ----------------------------------------------------------

async def test_timezone_pick_preview_then_save(db):
    await get_or_create_chat(CHAT_ID, "private")
    pick = FakeCallback("set_tz_pick:Europe/Kyiv")
    await s.pick_timezone(pick, _state())
    # A preview (with a confirm keyboard) is shown, not yet committed.
    assert (await get_chat(CHAT_ID)).timezone != "Europe/Kyiv"

    save = FakeCallback("set_tz_save:Europe/Kyiv")
    await s.save_timezone(save, _state())
    assert (await get_chat(CHAT_ID)).timezone == "Europe/Kyiv"


async def test_timezone_save_rejects_unknown(db):
    await get_or_create_chat(CHAT_ID, "private")
    save = FakeCallback("set_tz_save:Mars/Phobos")
    await s.save_timezone(save, _state())
    assert save.alerts


async def test_timezone_manual_entry_valid_and_invalid(db):
    await get_or_create_chat(CHAT_ID, "private")
    good = FakeMessage(text="America/New_York")
    await s.process_timezone_input(good, _state())
    assert "Проверь часовой пояс" in good.answers[-1][0]

    bad = FakeMessage(text="Not/AZone")
    await s.process_timezone_input(bad, _state())
    assert "Не знаю такого" in bad.answers[-1][0]


async def test_show_timezone_menu(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback("set_tz")
    await s.show_timezone(cb, _state())
    assert cb.message.answers


# --- Homework-edit policy ---------------------------------------------------

async def test_set_hw_policy_valid_and_invalid(db):
    await get_or_create_chat(CHAT_ID, "private")
    ok = FakeCallback("set_hw_policy_set:admin_only")
    await s.set_hw_policy(ok, _state())
    assert (await get_chat(CHAT_ID)).hw_edit_policy == "admin_only"

    bad = FakeCallback("set_hw_policy_set:nope")
    await s.set_hw_policy(bad, _state())
    assert bad.alerts


async def test_show_hw_policy_menu(db):
    await get_or_create_chat(CHAT_ID, "private")
    cb = FakeCallback("set_hw_policy")
    await s.show_hw_policy(cb, _state())
    assert cb.message.answers


# --- Prompt entrypoints (FSM set + prompt shown) ----------------------------

async def test_prompt_entrypoints_set_state(db):
    await get_or_create_chat(CHAT_ID, "private")
    for handler, data in (
        (s.edit_hw_reminder, "set_hw_rem"),
        (s.edit_sch_reminder, "set_sch_rem"),
        (s.edit_duetoday_time, "set_duetoday_time"),
        (s.edit_quiet_hours, "set_quiet"),
        (s.ask_timezone_manually, "set_tz_manual"),
    ):
        cb = FakeCallback(data)
        st = _state()
        await handler(cb, st)
        assert await st.get_state() is not None
        assert cb.message.answers


# --- Reset flow -------------------------------------------------------------

async def test_reset_confirm_then_execute_wipes_chat(db):
    await get_or_create_chat(CHAT_ID, "private")
    ask = FakeCallback("set_reset_all")
    st = _state()
    await s.confirm_reset(ask, st)
    assert "set_reset_confirm" in _callbacks(ask.message.answers[-1][1]["reply_markup"])

    do = FakeCallback("set_reset_confirm")
    await s.execute_reset(do, st)
    assert await get_chat(CHAT_ID) is None
