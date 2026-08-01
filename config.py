import os
from dotenv import load_dotenv

# Load env variables from .env file
load_dotenv()

# Application version, surfaced by the admin ``/status`` diagnostics command.
# Bump on releases; kept here (not derived from git) so it works in any deploy.
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")

# BOT_TOKEN is intentionally NOT validated at import time. The models,
# database and web-API layers must stay importable (and unit-testable) without
# a real bot token; only the components that actually talk to Telegram or verify
# Mini App ``initData`` (the bot process, the web-auth router) require it and
# call ``require_bot_token()`` at startup / on first use.
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kiev")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///school_bot.db")
# "sqlite" (default, persistent, production-safe) or "memory" (dev-only, lost on restart).
FSM_STORAGE = os.getenv("FSM_STORAGE", "sqlite")
# Touched once per scheduler tick; the Docker HEALTHCHECK checks its mtime.
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", ".heartbeat")
# How long the "📜 История" audit journal is kept. Rows older than this are
# pruned by the nightly scheduler housekeeping. 0 disables pruning entirely.
try:
    AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "180"))
except ValueError:
    raise ValueError("AUDIT_RETENTION_DAYS must be an integer number of days")
if AUDIT_RETENTION_DAYS < 0:
    raise ValueError("AUDIT_RETENTION_DAYS must not be negative")


def require_bot_token() -> str:
    """Return the configured bot token or raise if it is missing.

    Called by the bot process at startup and by the web-auth component the
    first time it needs to verify Telegram ``initData``. Keeping the check here
    (instead of at import) lets every other layer import ``config`` — and thus
    the ORM models — without a token being present.
    """
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in environment or .env file")
    return BOT_TOKEN


if not DATABASE_URL.startswith("sqlite"):
    raise ValueError(
        "Only SQLite is supported by this project (DATABASE_URL must start with "
        "'sqlite'). Other backends are not tested/migrated and are not accepted."
    )

if FSM_STORAGE not in ("sqlite", "memory"):
    raise ValueError("FSM_STORAGE must be 'sqlite' or 'memory'")
