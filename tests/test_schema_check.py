"""The bot refuses to start against a schema that is not at head.

Migrations are an explicit step (the image's ``migrate`` command), not a side
effect of the bot starting, so the bot and the web API can come up in any order.
The cost of that split is a database nobody upgraded, which would otherwise
surface much later as an opaque "no such table" inside a handler.
"""
import pytest

import bot as bot_module
from bot import SchemaOutOfDate, check_schema


async def test_passes_when_schema_is_at_head(monkeypatch):
    monkeypatch.setattr(bot_module, "get_script_head", lambda: "abc123")

    async def _revision():
        return "abc123"

    monkeypatch.setattr(bot_module, "get_db_revision", _revision)

    await check_schema()  # must not raise


async def test_refuses_when_schema_is_behind(monkeypatch):
    monkeypatch.setattr(bot_module, "get_script_head", lambda: "newrev")

    async def _revision():
        return "oldrev"

    monkeypatch.setattr(bot_module, "get_db_revision", _revision)

    with pytest.raises(SchemaOutOfDate) as excinfo:
        await check_schema()

    message = str(excinfo.value)
    # The message has to name both revisions and the way out, otherwise it is
    # just a different kind of unexplained crash.
    assert "oldrev" in message and "newrev" in message
    assert "migrate" in message


async def test_refuses_when_database_is_uninitialised(monkeypatch):
    monkeypatch.setattr(bot_module, "get_script_head", lambda: "newrev")

    async def _revision():
        return None

    monkeypatch.setattr(bot_module, "get_db_revision", _revision)

    with pytest.raises(SchemaOutOfDate) as excinfo:
        await check_schema()

    assert "not initialised" in str(excinfo.value)


async def test_unreadable_history_does_not_block_startup(monkeypatch, caplog):
    """A missing alembic/ directory must not be a hard stop.

    The check exists to catch a forgotten upgrade, not to become a new way for
    the bot to fail to start.
    """
    monkeypatch.setattr(bot_module, "get_script_head", lambda: None)

    async def _revision():
        return "whatever"

    monkeypatch.setattr(bot_module, "get_db_revision", _revision)

    await check_schema()  # must not raise


async def test_schema_out_of_date_is_not_a_systemexit():
    """The entrypoint treats SystemExit as a clean manual stop.

    Raising SystemExit here would be logged as "Bot stopped manually" and exit
    0, telling an orchestrator the deploy went fine.
    """
    assert not issubclass(SchemaOutOfDate, SystemExit)
    assert issubclass(SchemaOutOfDate, RuntimeError)
