"""
Shared pytest fixtures.

Critically, this sets dummy environment variables *before* importing any
project module, so tests never touch the real BOT_TOKEN or the production
``school_bot.db`` file. All DB access is redirected to an isolated in-memory
SQLite database and the Telegram Bot API is faked.
"""
import os

# Must run before `config`/`database.db` are imported anywhere.
# load_dotenv() does not override already-set env vars, so these win over .env.
os.environ.setdefault("BOT_TOKEN", "123456:TEST-DUMMY-TOKEN")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("TIMEZONE", "Europe/Kiev")

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import database.db as db_module
from database.db import init_db
from aiogram.exceptions import TelegramAPIError


class FakeTelegramError(TelegramAPIError):
    """A TelegramAPIError we can raise without a real API method object."""

    def __init__(self, message: str = "fake telegram failure"):
        Exception.__init__(self, message)
        # aiogram's TelegramAPIError.__str__ reads these attributes.
        self.message = message
        self.method = None


class FakeBot:
    """
    Minimal stand-in for aiogram's Bot. Records every send_message call and can
    be told to fail like Telegram would on a delivery error.
    """

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent = []  # list of (chat_id, text, kwargs)

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise FakeTelegramError()
        self.sent.append((chat_id, text, kwargs))


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
def failing_bot():
    return FakeBot(fail=True)


@pytest_asyncio.fixture
async def db():
    """
    Provides an isolated in-memory database for one test.

    StaticPool keeps a single underlying connection so the ``:memory:`` DB is
    shared across sessions within the test. The ``database.db`` module globals
    are patched so every DB helper in the project uses this engine.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    old_engine = db_module.engine
    old_session = db_module.AsyncSessionLocal
    db_module.engine = engine
    db_module.AsyncSessionLocal = Session

    await init_db()  # exercises create_all + column migrations

    try:
        yield Session
    finally:
        db_module.engine = old_engine
        db_module.AsyncSessionLocal = old_session
        await engine.dispose()
