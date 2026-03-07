from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.cursor_api_client import CursorApiError
from cursor_tg_connector.services_agent_service import AgentStopError
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.telegram_bot_common import (
    BOT_COMMANDS,
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
)
from cursor_tg_connector.utils_formatting import build_agent_info_message, format_command_list

_HELP_TEXT = (
    "Cursor Telegram connector — manage Cursor Cloud agents from Telegram.\n"
    "\n"
    "Commands:\n"
    + "\n".join(f"/{cmd} — {desc}" for cmd, desc in BOT_COMMANDS)
    + "\n"
    "\n"
    "Usage:\n"
    "• Send /agents to see running agents and switch the active one.\n"
    "• Send /unfocus to clear the current active agent selection.\n"
    "• Send /stop to stop the currently selected running agent.\n"
    "• Send /newagent to create a new agent (model → repo → branch → prompt).\n"
    "• Send any text message to follow up with the active agent.\n"
    "• Unread messages from the active agent are delivered automatically.\n"
    "• Non-active agents show unread counts; use /agents to switch."
)

_STOP_HELP_TEXT = (
    "No active agent selected.\n"
    "\n"
    "Use /agents to pick a running agent, then send /stop to stop it."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    await update.effective_message.reply_text(_HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    await update.effective_message.reply_text(_HELP_TEXT)


async def current_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    agent = await services.agent_service.ensure_active_agent_exists(
        services.settings.telegram_allowed_user_id,
    )
    if agent is None:
        await update.effective_message.reply_text(
            "No active agent selected. Use /agents to pick one."
        )
        return

    await update.effective_message.reply_text(build_agent_info_message(agent))


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    agent_name = await services.agent_service.clear_unread(
        services.settings.telegram_allowed_user_id,
    )
    if agent_name is None:
        await update.effective_message.reply_text(
            "No active agent selected. Use /agents to pick one."
        )
        return

    await update.effective_message.reply_text(
        f"Cleared all unread messages for {agent_name}."
    )


async def unfocus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    cleared = await services.agent_service.clear_active_agent(
        services.settings.telegram_allowed_user_id,
    )
    if not cleared:
        await update.effective_message.reply_text("No active agent is currently selected.")
        return

    await update.effective_message.reply_text(
        "Cleared the active agent selection. Use /agents to pick one again."
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    try:
        agent = await services.agent_service.stop_active_agent(
            services.settings.telegram_allowed_user_id,
        )
    except (AgentStopError, CursorApiError) as exc:
        await update.effective_message.reply_text(str(exc))
        return

    if agent is None:
        await update.effective_message.reply_text(_STOP_HELP_TEXT)
        return

    await update.effective_message.reply_text(f"Stopped {agent.name or agent.id}.")


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    items = await services.agent_service.list_agents_with_unread_counts(
        services.settings.telegram_allowed_user_id
    )
    if not items:
        await update.effective_message.reply_text("No agents found.")
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
        "Agents",
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
