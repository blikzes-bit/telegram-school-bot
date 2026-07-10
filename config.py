import os
from dotenv import load_dotenv

# Load env variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kiev")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///school_bot.db")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment or .env file")
