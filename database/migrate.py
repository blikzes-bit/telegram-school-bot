import os

from alembic import command
from alembic.config import Config

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALEMBIC_INI = os.path.join(_PROJECT_ROOT, "alembic.ini")


def run_migrations() -> None:
    """
    Brings the production database schema up to date via Alembic
    (``alembic upgrade head``), run programmatically at process startup
    instead of relying on ad-hoc ``ALTER TABLE`` calls.
    """
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "alembic"))
    command.upgrade(cfg, "head")
