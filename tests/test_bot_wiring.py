"""
Tests for the startup wiring helpers in bot.py: storage selection, the
slash-command menu publication (including its graceful failure), and the
top-level error handler that shields users from internal exceptions.
"""
from types import SimpleNamespace

from aiogram.fsm.storage.memory import MemoryStorage

import bot as bot_module
from database.fsm_storage import SQLAlchemyStorage


def test_build_storage_memory(monkeypatch):
    monkeypatch.setattr(bot_module, "FSM_STORAGE", "memory")
    assert isinstance(bot_module._build_storage(), MemoryStorage)


def test_build_storage_sqlite(monkeypatch):
    monkeypatch.setattr(bot_module, "FSM_STORAGE", "sqlite")
    assert isinstance(bot_module._build_storage(), SQLAlchemyStorage)


def test_bot_commands_cover_public_menu():
    names = {c.command for c in bot_module.BOT_COMMANDS}
    assert {"today", "schedule", "homework", "extra", "settings", "help"} <= names


class _RecordingBot:
    def __init__(self, fail=False):
        self.fail = fail
        self.set = None

    async def set_my_commands(self, commands):
        if self.fail:
            raise RuntimeError("telegram hiccup")
        self.set = commands


async def test_configure_commands_publishes_menu():
    bot = _RecordingBot()
    await bot_module.configure_commands(bot)
    assert bot.set == bot_module.BOT_COMMANDS


async def test_configure_commands_swallows_failures():
    bot = _RecordingBot(fail=True)
    # Must not raise: a transient API error can't stop the bot from starting.
    await bot_module.configure_commands(bot)


class _Answerable:
    def __init__(self, raises=False):
        self.raises = raises
        self.answered = []

    async def answer(self, *args, **kwargs):
        if self.raises:
            raise RuntimeError("send failed")
        self.answered.append((args, kwargs))


def _error_event(callback=None, message=None):
    update = SimpleNamespace(callback_query=callback, message=message)
    return SimpleNamespace(update=update)


async def test_on_error_notifies_callback():
    cb = _Answerable()
    result = await bot_module._on_error(_error_event(callback=cb), RuntimeError("boom"))
    assert result is True
    assert cb.answered  # user got a generic, safe message


async def test_on_error_notifies_message():
    msg = _Answerable()
    result = await bot_module._on_error(_error_event(message=msg), RuntimeError("boom"))
    assert result is True
    assert msg.answered
    # The user-facing text must not contain the internal exception detail.
    text = msg.answered[0][0][0]
    assert "boom" not in text


async def test_on_error_survives_notify_failure():
    msg = _Answerable(raises=True)
    # Even if notifying the user fails, the handler still returns True.
    result = await bot_module._on_error(_error_event(message=msg), RuntimeError("boom"))
    assert result is True
