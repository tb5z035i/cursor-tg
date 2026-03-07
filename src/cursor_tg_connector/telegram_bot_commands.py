from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.cursor_api_client import CursorApiError
from cursor_tg_connector.services_agent_service import AgentStopError
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.telegram_bot_common import (
    BOT_COMMANDS,
    get_message_thread_id,
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
    render_reset_db_keyboard,
)
from cursor_tg_connector.utils_formatting import (
    build_agent_info_message,
    build_reset_db_prompt,
    build_thread_command_guidance,
    build_thread_mode_status,
    format_command_list,
)

_HELP_TEXT = (
    "Cursor Telegram connector — manage Cursor Cloud agents from Telegram.\n"
    "\n"
    "Commands:\n"
    + "\n".join(f"/{cmd} — {desc}" for cmd, desc in BOT_COMMANDS)
    + "\n"
    "\n"
    "Usage:\n"
    "• Send /agents to see agents and open/create their thread.\n"
    "• Send /unfocus to clear the current active agent selection.\n"
    "• Send /stop to stop the currently selected running agent.\n"
    "• Send /threadmode on to route each agent into its own Telegram thread.\n"
    "• Send /newagent to create a new agent (model → repo → branch → prompt).\n"
    "• Send any text message to follow up with the active agent or from inside an agent thread.\n"
    "• Unread messages from the active agent are delivered automatically.\n"
    "• In thread mode, root-chat notices tell you when to use /agents to create/open a thread.\n"
    "• Use /resetdb if you want to wipe and reinitialize local bot state."
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
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if session.thread_mode_enabled:
        agent_id = await services.agent_service.resolve_context_agent_id(
            telegram_user_id=services.settings.telegram_allowed_user_id,
            chat_id=update.effective_chat.id,
            message_thread_id=get_message_thread_id(update),
        )
        if agent_id is None:
            await update.effective_message.reply_text(build_thread_command_guidance())
            return
        agent = await services.agent_service.cursor_client.get_agent(agent_id)
    else:
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
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if session.thread_mode_enabled:
        agent_id = await services.agent_service.resolve_context_agent_id(
            telegram_user_id=services.settings.telegram_allowed_user_id,
            chat_id=update.effective_chat.id,
            message_thread_id=get_message_thread_id(update),
        )
        if agent_id is None:
            await update.effective_message.reply_text(build_thread_command_guidance())
            return
        agent_name = await services.agent_service.clear_unread_for_agent(agent_id)
    else:
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
        session = await services.create_agent_service.state_repo.get_session(
            services.settings.telegram_allowed_user_id
        )
        if session.thread_mode_enabled:
            agent_id = await services.agent_service.resolve_context_agent_id(
                telegram_user_id=services.settings.telegram_allowed_user_id,
                chat_id=update.effective_chat.id,
                message_thread_id=get_message_thread_id(update),
            )
            if agent_id is None:
                await update.effective_message.reply_text(build_thread_command_guidance())
                return
            agent = await services.agent_service.stop_agent_by_id(agent_id)
        else:
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
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    summary = format_command_list(
        "Agents (tap to create/open a thread)" if session.thread_mode_enabled else "Agents",
        [item.label for item in items],
    )
    await update.effective_message.reply_text(summary, reply_markup=keyboard)


async def new_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if session.thread_mode_enabled:
        thread_id = get_message_thread_id(update)
        if thread_id is not None:
            binding = await services.create_agent_service.state_repo.get_thread_binding(
                update.effective_chat.id,
                thread_id,
            )
            if binding is not None:
                await update.effective_message.reply_text(
                    "Run /newagent from the root chat, not from inside an agent thread."
                )
                return
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


async def threadmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    args = [arg.lower() for arg in getattr(context, "args", [])]
    current = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )

    if not args or args[0] == "status":
        await update.effective_message.reply_text(
            build_thread_mode_status(current.thread_mode_enabled)
        )
        return

    if args[0] == "on":
        updated = await services.create_agent_service.state_repo.set_thread_mode_enabled(
            services.settings.telegram_allowed_user_id,
            True,
        )
        await update.effective_message.reply_text(
            build_thread_mode_status(updated.thread_mode_enabled)
        )
        return

    if args[0] == "off":
        updated = await services.create_agent_service.state_repo.set_thread_mode_enabled(
            services.settings.telegram_allowed_user_id,
            False,
        )
        await update.effective_message.reply_text(
            build_thread_mode_status(updated.thread_mode_enabled)
            + "\n\nExisting thread bindings were preserved."
        )
        return

    await update.effective_message.reply_text("Usage: /threadmode [on|off|status]")


async def resetdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    await update.effective_message.reply_text(
        build_reset_db_prompt(),
        reply_markup=render_reset_db_keyboard(),
    )


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
