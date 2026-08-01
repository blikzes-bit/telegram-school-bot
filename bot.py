import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import FSM_STORAGE, require_bot_token
from database.migrate import run_migrations
from services.scheduler import setup_scheduler
from middleware.access import ChatContextMiddleware, OnboardingGuardMiddleware

# Import routers
from handlers import (
    common, onboarding, today, schedule, homework, settings, migration, extra,
    date_overrides, history, data_backup, status, web,
)

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


# The command menu Telegram shows behind the "/" button. Kept in one place so
# the visible commands never drift from the handlers that back them.
BOT_COMMANDS = [
    BotCommand(command="today", description="📚 Сегодня"),
    BotCommand(command="schedule", description="📅 Расписание"),
    BotCommand(command="homework", description="📝 Домашнее задание"),
    BotCommand(command="extra", description="🎯 Доп. занятия"),
    BotCommand(command="web", description="🌐 Приложение"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="❓ Помощь"),
]


async def configure_commands(bot: Bot) -> None:
    """Publish the slash-command menu; a transient API hiccup must not stop the
    bot from starting, so failures are logged and swallowed."""
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.exception("Failed to set bot commands")


async def _on_error(event, exception):
    update = event.update
    # A correlation id (Telegram's update_id) ties the failure to a specific
    # update in the logs without exposing any message content or user data.
    update_id = getattr(update, "update_id", None)
    logger.exception(
        "Unhandled error while processing update (update_id=%s)", update_id,
        exc_info=exception,
    )
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(
                "⚠️ Произошла ошибка, попробуйте ещё раз.", show_alert=True
            )
        elif update.message is not None:
            await update.message.answer("⚠️ Произошла ошибка, попробуйте ещё раз.")
    except Exception:
        logger.exception("Failed to notify user about the error (update_id=%s)", update_id)
    return True


async def main():
    # Bring the production schema up to date (Alembic migrations) instead of
    # a bare create_all — see database/migrate.py.
    logger.info("Running database migrations...")
    await run_migrations()

    # Initialize Bot and Dispatcher
    bot = Bot(token=require_bot_token())
    dp = Dispatcher(storage=_build_storage())

    dp.update.outer_middleware(ChatContextMiddleware())

    # Onboarding must be completed before these routers' handlers may run —
    # this also blocks stale inline keyboards left over from before a reset.
    guard = OnboardingGuardMiddleware()
    for gated_router in (
        today.router, schedule.router, homework.router, settings.router,
        extra.router, date_overrides.router, history.router, data_backup.router,
    ):
        gated_router.message.outer_middleware(guard)
        gated_router.callback_query.outer_middleware(guard)

    dp.errors.register(_on_error)

    # Register Routers
    # Note: Onboarding and schedule/homework need to be registered in correct order.
    # Onboarding has state handlers, common has fallback start commands.
    dp.include_router(common.router)
    dp.include_router(migration.router)
    dp.include_router(status.router)
    dp.include_router(web.router)
    dp.include_router(onboarding.router)
    dp.include_router(today.router)
    dp.include_router(schedule.router)
    dp.include_router(homework.router)
    dp.include_router(settings.router)
    dp.include_router(extra.router)
    dp.include_router(date_overrides.router)
    dp.include_router(history.router)
    dp.include_router(data_backup.router)

    # Setup background reminder scheduler
    scheduler = setup_scheduler(bot)

    # Publish the slash-command menu (/today, /schedule, ...).
    await configure_commands(bot)

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
