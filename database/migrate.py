import asyncio
import os

from alembic import command
from alembic.config import Config

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALEMBIC_INI = os.path.join(_PROJECT_ROOT, "alembic.ini")


def _upgrade_to_head() -> None:
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "alembic"))
    command.upgrade(cfg, "head")


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
