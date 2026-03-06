from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.telegram_bot_common import (
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
)
from cursor_tg_connector.utils_formatting import format_command_list


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    await update.effective_message.reply_text(
        "\n".join(
            [
                "Cursor Telegram connector is ready.",
                "Commands:",
                "/agents - list running agents and switch active agent",
                "/newagent - create a new Cursor cloud agent",
                "/cancel - cancel an in-progress create-agent wizard",
            ]
        )
    )


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    items = await services.agent_service.list_running_agents_with_unread_counts(
        services.settings.telegram_allowed_user_id
    )
    if not items:
        await update.effective_message.reply_text("No running agents found.")
        return

    keyboard = render_agent_keyboard(
        [
            (
                item.agent_id,
                f"{'✅ ' if item.is_active else ''}{item.label}",
            )
            for item in items
        ]
    )
    summary = format_command_list(
        "Running agents",
        [item.label for item in items],
    )
    await update.effective_message.reply_text(summary, reply_markup=keyboard)


async def new_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    try:
        await services.create_agent_service.start_wizard(
            services.settings.telegram_allowed_user_id,
            update.effective_chat.id,
        )
        first_page = await services.create_agent_service.get_model_page(
            services.settings.telegram_allowed_user_id,
            0,
        )
    except CreateAgentError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        "Step 1/4: Select a model ID.",
        reply_markup=render_model_keyboard(first_page),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    cancelled = await services.create_agent_service.cancel(
        services.settings.telegram_allowed_user_id
    )
    if cancelled:
        await update.effective_message.reply_text("Create-agent wizard cancelled.")
    else:
        await update.effective_message.reply_text("No create-agent wizard is currently running.")


async def _authorize_and_record_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    services = get_services(context)
    user = update.effective_user
    if user is None or user.id != services.settings.telegram_allowed_user_id:
        return False

    if update.effective_chat:
        await services.create_agent_service.state_repo.update_chat_context(
            user.id,
            update.effective_chat.id,
        )
    return True
