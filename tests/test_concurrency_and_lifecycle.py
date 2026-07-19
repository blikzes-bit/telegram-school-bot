"""
Covers: concurrent get_or_create_chat, calling functionality before /start,
behavior after a full reset, and the group -> supergroup migration.
"""
import asyncio
import datetime

from database.db import (
    get_or_create_chat, add_homework, get_homework, delete_chat,
    migrate_chat,
)
from database.models import Chat
from middleware.access import OnboardingGuardMiddleware

CHAT_ID = 900001


async def test_concurrent_get_or_create_chat_no_integrity_error(tmp_path):
    """
    Two truly-concurrent calls for a brand-new chat_id must not raise an
    uncaught IntegrityError — the loser should transparently observe the
    winner's row instead of crashing.

    This needs its own file-backed engine (with real, independent
    connections) rather than the shared ``db`` fixture's StaticPool
    ``:memory:`` engine, which forces every session onto one literal DBAPI
    connection and can't exhibit a genuine two-transaction race.
    """
    import database.db as db_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import select

    db_path = tmp_path / "concurrency_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    old_engine, old_session = db_module.engine, db_module.AsyncSessionLocal
    db_module.engine, db_module.AsyncSessionLocal = engine, Session
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Chat.metadata.create_all)

        results = await asyncio.gather(
            get_or_create_chat(CHAT_ID, "private"),
            get_or_create_chat(CHAT_ID, "private"),
        )
        assert results[0].chat_id == CHAT_ID
        assert results[1].chat_id == CHAT_ID

        async with Session() as session:
            rows = (await session.execute(select(Chat).where(Chat.chat_id == CHAT_ID))).scalars().all()
        assert len(rows) == 1
    finally:
        db_module.engine, db_module.AsyncSessionLocal = old_engine, old_session
        await engine.dispose()


async def test_onboarding_guard_blocks_before_start():
    """
    A chat that has never onboarded must be blocked from reaching a gated
    handler — even if it somehow has a stale inline button pointing at one.
    """
    class FakeEvent:
        def __init__(self):
            self.answered = []

        async def answer(self, text, **kwargs):
            self.answered.append(text)

    guard = OnboardingGuardMiddleware()
    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True

    not_onboarded_chat = Chat(chat_id=CHAT_ID, chat_type="private", is_onboarded=False)
    event = FakeEvent()
    await guard(handler, event, {"chat": not_onboarded_chat})

    assert not handler_called
    assert event.answered, "should have told the user to /start first"


async def test_onboarding_guard_allows_onboarded_chat():
    guard = OnboardingGuardMiddleware()
    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True

    onboarded_chat = Chat(chat_id=CHAT_ID, chat_type="private", is_onboarded=True)

    class FakeEvent:
        async def answer(self, *a, **k):
            pass

    await guard(handler, FakeEvent(), {"chat": onboarded_chat})
    assert handler_called


async def test_full_reset_clears_onboarded_flag_and_data(db):
    await get_or_create_chat(CHAT_ID, "private")
    await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    await delete_chat(CHAT_ID)

    # Re-creating returns a fresh, not-onboarded chat with no leftover data.
    fresh = await get_or_create_chat(CHAT_ID, "private")
    assert fresh.is_onboarded is False
    homeworks = await get_homework(CHAT_ID)
    assert homeworks == []


async def test_migrate_chat_moves_all_data(db):
    old_id = 900010
    new_id = -1009000010

    await get_or_create_chat(old_id, "group")
    await add_homework(old_id, "Math", datetime.date(2026, 1, 10), "p.1")

    moved = await migrate_chat(old_id, new_id)
    assert moved is True

    old_after = await get_homework(old_id)
    assert old_after == []

    new_after = await get_homework(new_id)
    assert len(new_after) == 1
    assert new_after[0].subject_name == "Math"

    # Old chat row itself is gone (not just orphaned).
    from sqlalchemy import select
    async with db() as session:
        rows = (await session.execute(select(Chat).where(Chat.chat_id == old_id))).scalars().all()
    assert rows == []


async def test_migrate_chat_missing_old_chat_is_noop(db):
    moved = await migrate_chat(999999, 888888)
    assert moved is False
