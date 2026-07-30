"""Tests for the persistent SQLite FSM storage (database/fsm_storage.py)."""
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey

from database.fsm_storage import SQLAlchemyStorage, _key


def _sk(user_id=1):
    return StorageKey(bot_id=10, chat_id=20, user_id=user_id)


def test_key_is_stable_and_scoped():
    assert _key(_sk(5)) == "10:20:5"


async def test_set_and_get_state_insert_then_update(db):
    storage = SQLAlchemyStorage()
    key = _sk()
    assert await storage.get_state(key) is None
    await storage.set_state(key, "waiting")           # insert branch
    assert await storage.get_state(key) == "waiting"
    await storage.set_state(key, "done")              # update branch
    assert await storage.get_state(key) == "done"


async def test_set_state_accepts_state_object(db):
    storage = SQLAlchemyStorage()
    key = _sk()
    await storage.set_state(key, State("x", group_name="G"))
    assert await storage.get_state(key) == "G:x"


async def test_set_and_get_data_insert_then_update(db):
    storage = SQLAlchemyStorage()
    key = _sk()
    assert await storage.get_data(key) == {}          # missing -> {}
    await storage.set_data(key, {"a": 1})             # insert branch
    assert await storage.get_data(key) == {"a": 1}
    await storage.set_data(key, {"a": 2, "b": 3})     # update branch
    assert await storage.get_data(key) == {"a": 2, "b": 3}


async def test_close_is_noop(db):
    await SQLAlchemyStorage().close()
