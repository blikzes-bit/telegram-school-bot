"""Running migrations must not silence the application's logging.

``alembic/env.py`` configures logging from ``alembic.ini`` via ``fileConfig``,
whose ``disable_existing_loggers`` defaults to True. Because the bot applies
migrations inside its own process at startup (``bot.py`` -> ``run_migrations``),
that default would switch off every logger created earlier — the bot then runs
for the rest of its life without polling logs, scheduler diagnostics or
tracebacks from the error handler.
"""
import logging

from database.migrate import run_migrations


async def test_run_migrations_keeps_existing_loggers_alive(tmp_path, monkeypatch):
    # A logger created before migrations, exactly like bot.py's module logger.
    log = logging.getLogger("test.pre_existing_logger")
    assert not log.disabled

    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}"
    )
    # alembic/env.py reads config.DATABASE_URL at import time of the env module,
    # which happens inside the upgrade, so patch the already-imported module too.
    import config

    monkeypatch.setattr(
        config, "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}"
    )

    await run_migrations()

    assert not log.disabled, (
        "alembic's fileConfig disabled a pre-existing logger; pass "
        "disable_existing_loggers=False in alembic/env.py"
    )


async def test_logger_still_emits_after_migrations(tmp_path, monkeypatch):
    """Not just `disabled` — a record written afterwards must still be delivered.

    The handler is attached *before* the migration, mirroring bot.py, which
    configures logging and only then upgrades the schema. pytest's ``caplog`` is
    no use here: ``fileConfig`` replaces the root handlers, taking caplog's own
    handler with them, so it would report an empty log even when the record was
    emitted perfectly well.
    """
    received: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            received.append(record.getMessage())

    log = logging.getLogger("test.emits_after_migrations")
    log.setLevel(logging.INFO)
    handler = Collect()
    log.addHandler(handler)

    import config

    monkeypatch.setattr(
        config, "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'emit.db'}"
    )
    try:
        await run_migrations()
        log.info("still here")
    finally:
        log.removeHandler(handler)

    assert "still here" in received
