"""Connection-level SQLite pragmas.

The bot and the Mini App API are two processes writing to the same database
file, so the connect-time pragmas are what keeps them from tripping over each
other. These tests pin that behaviour against a real file-backed database —
``:memory:`` silently refuses WAL, so it cannot verify the thing that matters.
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from database.db import set_sqlite_pragma


async def _pragma(engine, name: str):
    async with engine.connect() as conn:
        result = await conn.execute(text(f"PRAGMA {name}"))
        return result.scalar()


def _engine_with_pragmas(url: str):
    engine = create_async_engine(url)
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    return engine


async def test_file_database_uses_wal(tmp_path):
    engine = _engine_with_pragmas(f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}")
    try:
        assert await _pragma(engine, "journal_mode") == "wal"
    finally:
        await engine.dispose()


async def test_busy_timeout_is_set(tmp_path):
    """Overlapping writers must wait rather than fail with "database is locked"."""
    engine = _engine_with_pragmas(f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}")
    try:
        assert await _pragma(engine, "busy_timeout") == 5000
    finally:
        await engine.dispose()


async def test_foreign_keys_still_enforced(tmp_path):
    """Regression guard: adding WAL must not drop the pre-existing FK pragma."""
    engine = _engine_with_pragmas(f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}")
    try:
        assert await _pragma(engine, "foreign_keys") == 1
    finally:
        await engine.dispose()


async def test_memory_database_still_connects():
    """The test suite itself runs on ``:memory:``, which cannot do WAL.

    SQLite answers the journal_mode request with ``memory`` instead of raising,
    so the pragma block must stay harmless there.
    """
    engine = _engine_with_pragmas("sqlite+aiosqlite:///:memory:")
    try:
        assert await _pragma(engine, "journal_mode") == "memory"
        assert await _pragma(engine, "busy_timeout") == 5000
    finally:
        await engine.dispose()
