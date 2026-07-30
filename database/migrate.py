import asyncio
import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALEMBIC_INI = os.path.join(_PROJECT_ROOT, "alembic.ini")


def _config() -> Config:
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "alembic"))
    return cfg


def _upgrade_to_head() -> None:
    command.upgrade(_config(), "head")


async def run_migrations() -> None:
    """
    Brings the production database schema up to date via Alembic
    (``alembic upgrade head``), run programmatically at process startup
    instead of relying on ad-hoc ``ALTER TABLE`` calls.

    ``command.upgrade`` is synchronous and ``alembic/env.py`` drives the async
    engine with ``asyncio.run(...)``. Calling it directly from the bot's
    already-running event loop raises ``RuntimeError: asyncio.run() cannot be
    called from a running event loop``. Running the upgrade in a worker thread
    hands Alembic a thread with no active loop, so its ``asyncio.run`` works.
    """
    await asyncio.to_thread(_upgrade_to_head)


def get_script_head() -> str | None:
    """The latest revision id defined in ``alembic/versions`` (the target of an
    ``upgrade head``). Returns None if the revision history cannot be read."""
    try:
        return ScriptDirectory.from_config(_config()).get_current_head()
    except Exception:
        return None
