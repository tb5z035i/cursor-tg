from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cursor_tg_connector.telegram_bot_callbacks import callback_router
from cursor_tg_connector.telegram_bot_commands import (
    agents_command,
    cancel_command,
    clear_command,
    current_command,
    help_command,
    new_agent_command,
    start_command,
    unread_command,
)
from cursor_tg_connector.telegram_bot_common import BOT_COMMANDS, AppServices
from cursor_tg_connector.telegram_bot_messages import message_handler

logger = logging.getLogger(__name__)


async def register_commands(application: Application) -> None:
    """Register bot commands with Telegram so the slash menu auto-completes."""
    await application.bot.set_my_commands(
        [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
    )
    logger.info("Registered %d bot commands with Telegram", len(BOT_COMMANDS))


def build_application(services: AppServices) -> Application:
    application = ApplicationBuilder().token(services.settings.telegram_bot_token).build()
    application.bot_data["services"] = services

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("current", current_command))
    application.add_handler(CommandHandler("agents", agents_command))
    application.add_handler(CommandHandler("unread", unread_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("newagent", new_agent_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO, message_handler)
    )
    application.add_error_handler(error_handler)

    application.job_queue.run_repeating(
        poll_job,
        interval=services.settings.poll_interval_seconds,
        first=5,
        name="poll-cursor-agents",
    )
    return application


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    services: AppServices = context.application.bot_data["services"]
    from cursor_tg_connector.services_notification import TelegramNotifier

    notifier = TelegramNotifier(context.bot)
    await services.polling_service.poll_once(notifier)


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram error", exc_info=context.error)
