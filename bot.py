import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, FSM_STORAGE
from database.migrate import run_migrations
from services.scheduler import setup_scheduler
from middleware.access import ChatContextMiddleware, OnboardingGuardMiddleware

# Import routers
from handlers import common, onboarding, today, schedule, homework, settings, migration

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def _build_storage():
    if FSM_STORAGE == "memory":
        return MemoryStorage()
    from database.fsm_storage import SQLAlchemyStorage
    return SQLAlchemyStorage()


async def _on_error(event, exception):
    logger.exception("Unhandled error while processing update", exc_info=exception)
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(
                "⚠️ Произошла ошибка, попробуйте ещё раз.", show_alert=True
            )
        elif update.message is not None:
            await update.message.answer("⚠️ Произошла ошибка, попробуйте ещё раз.")
    except Exception:
        logger.exception("Failed to notify user about the error")
    return True


async def main():
    # Bring the production schema up to date (Alembic migrations) instead of
    # a bare create_all — see database/migrate.py.
    logger.info("Running database migrations...")
    run_migrations()

    # Initialize Bot and Dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=_build_storage())

    dp.update.outer_middleware(ChatContextMiddleware())

    # Onboarding must be completed before these routers' handlers may run —
    # this also blocks stale inline keyboards left over from before a reset.
    guard = OnboardingGuardMiddleware()
    for gated_router in (today.router, schedule.router, homework.router, settings.router):
        gated_router.message.outer_middleware(guard)
        gated_router.callback_query.outer_middleware(guard)

    dp.errors.register(_on_error)

    # Register Routers
    # Note: Onboarding and schedule/homework need to be registered in correct order.
    # Onboarding has state handlers, common has fallback start commands.
    dp.include_router(common.router)
    dp.include_router(migration.router)
    dp.include_router(onboarding.router)
    dp.include_router(today.router)
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
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")
