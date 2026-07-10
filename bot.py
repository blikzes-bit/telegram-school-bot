import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.db import init_db
from services.scheduler import setup_scheduler

# Import routers
from handlers import common, onboarding, schedule, homework, settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def main():
    # Initialize DB
    logger.info("Initializing database...")
    await init_db()
    
    # Initialize Bot and Dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Register Routers
    # Note: Onboarding and schedule/homework need to be registered in correct order.
    # Onboarding has state handlers, common has fallback start commands.
    dp.include_router(common.router)
    dp.include_router(onboarding.router)
    dp.include_router(schedule.router)
    dp.include_router(homework.router)
    dp.include_router(settings.router)
    
    # Setup background reminder scheduler
    scheduler = setup_scheduler(bot)
    
    # Start polling
    logger.info("Starting bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Stopping scheduler...")
        scheduler.shutdown()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")
