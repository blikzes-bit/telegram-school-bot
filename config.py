import os
from dotenv import load_dotenv

# Load env variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kiev")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///school_bot.db")
# "sqlite" (default, persistent, production-safe) or "memory" (dev-only, lost on restart).
FSM_STORAGE = os.getenv("FSM_STORAGE", "sqlite")
# Touched once per scheduler tick; the Docker HEALTHCHECK checks its mtime.
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", ".heartbeat")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment or .env file")

if not DATABASE_URL.startswith("sqlite"):
    raise ValueError(
        "Only SQLite is supported by this project (DATABASE_URL must start with "
        "'sqlite'). Other backends are not tested/migrated and are not accepted."
    )

if FSM_STORAGE not in ("sqlite", "memory"):
    raise ValueError("FSM_STORAGE must be 'sqlite' or 'memory'")
