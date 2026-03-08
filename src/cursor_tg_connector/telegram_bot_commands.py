from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.cursor_api_client import CursorApiError
from cursor_tg_connector.domain_types import UnselectedAgentUnreadMode
from cursor_tg_connector.services_agent_service import AgentStopError
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.telegram_bot_common import (
    BOT_COMMANDS,
    get_message_thread_id,
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
    render_reset_db_keyboard,
    render_threadmode_keyboard,
)
from cursor_tg_connector.utils_formatting import (
    build_agent_info_message,
    build_agents_summary_message,
    build_reset_db_prompt,
    build_thread_command_guidance,
    build_thread_mode_guidance,
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
    "• Send /agents to view agents. In thread mode it becomes the thread opener.\n"
    "• Send /focus to choose the active agent from clickable options in non-thread mode.\n"
    "• Send /configure_unread full|count|none to control non-active agent notices.\n"
    "• Send /unfocus to clear the current active agent selection.\n"
    "• Send /stop to stop the currently selected running agent.\n"
    "• Send /threadmode to toggle per-agent Telegram threads with a button.\n"
    "• Send /newagent to create a new agent (model → repo → branch → prompt).\n"
    "• Send any text message to follow up with the active agent or from inside an agent thread.\n"
    "• In thread mode, root-chat notices can still appear for unbound agents based on "
    "/configure_unread.\n"
    "• Use /resetdb if you want to wipe and reinitialize local bot state."
)

_STOP_HELP_TEXT = (
    "No active agent selected.\n"
    "\n"
    "Use /focus to pick a running agent, then send /stop to stop it."
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
            "No active agent selected. Use /focus to pick one."
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
            "No active agent selected. Use /focus to pick one."
        )
        return

    await update.effective_message.reply_text(
        f"Cleared all unread messages for {agent_name}."
    )


async def configure_unread_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    message = update.effective_message
    if message is None:
        return

    if not context.args:
        session = await services.create_agent_service.state_repo.get_session(
            services.settings.telegram_allowed_user_id
        )
        await message.reply_text(_build_unread_command_text(session.unselected_agent_unread_mode))
        return

    mode = _parse_unread_mode(context.args[0])
    if mode is None:
        await message.reply_text(_build_unread_command_text(None))
        return

    await services.create_agent_service.state_repo.set_unselected_agent_unread_mode(
        services.settings.telegram_allowed_user_id,
        mode,
    )
    await message.reply_text(
        "Unread handling for unselected agents is now set to "
        f"{_describe_unread_mode(mode)}."
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
        "Cleared the active agent selection. Use /focus to pick one again."
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


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    message = update.effective_message
    if message is None:
        return

    if session.thread_mode_enabled:
        await message.reply_text(build_thread_mode_guidance())
        return

    items = await _list_agent_selection_items(context)
    if not items:
        await message.reply_text("No agents found.")
        return

    await message.reply_text("Select an agent:", reply_markup=render_agent_keyboard(items))


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

    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if session.thread_mode_enabled:
        keyboard = render_agent_keyboard(
            [
                (
                    item.agent_id,
                    item.label,
                )
                for item in items
            ]
        )
        summary = format_command_list(
            "Agents (tap to create/open a thread)",
            [item.label for item in items],
        )
        await update.effective_message.reply_text(summary, reply_markup=keyboard)
        return

    summary = build_agents_summary_message(items)
    await update.effective_message.reply_text(summary, parse_mode="HTML")


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
    current = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    await update.effective_message.reply_text(
        build_thread_mode_status(current.thread_mode_enabled),
        reply_markup=render_threadmode_keyboard(current.thread_mode_enabled),
    )


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


def _parse_unread_mode(value: str) -> UnselectedAgentUnreadMode | None:
    normalized = value.strip().lower()
    aliases = {
        "full": UnselectedAgentUnreadMode.FULL,
        "text": UnselectedAgentUnreadMode.FULL,
        "count": UnselectedAgentUnreadMode.COUNT,
        "number": UnselectedAgentUnreadMode.COUNT,
        "none": UnselectedAgentUnreadMode.NONE,
        "off": UnselectedAgentUnreadMode.NONE,
        "hide": UnselectedAgentUnreadMode.NONE,
    }
    return aliases.get(normalized)


def _describe_unread_mode(mode: UnselectedAgentUnreadMode) -> str:
    descriptions = {
        UnselectedAgentUnreadMode.FULL: "full text delivery",
        UnselectedAgentUnreadMode.COUNT: "unread count notices",
        UnselectedAgentUnreadMode.NONE: "no notifications",
    }
    return descriptions[mode]


def _build_unread_command_text(
    current_mode: UnselectedAgentUnreadMode | None,
) -> str:
    current = (
        f"Current setting: {_describe_unread_mode(current_mode)}.\n\n"
        if current_mode is not None
        else ""
    )
    return (
        f"{current}"
        "Usage: /configure_unread <full|count|none>\n"
        "• full — deliver unread messages from unselected agents in full.\n"
        "• count — send only unread-count notices (default).\n"
        "• none — send nothing until you switch to that agent."
    )


async def _list_agent_selection_items(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[tuple[str, str]]:
    services = get_services(context)
    items = await services.agent_service.list_agents_with_unread_counts(
        services.settings.telegram_allowed_user_id
    )
    return [
        (
            item.agent_id,
            f"{'✅ ' if item.is_active else ''}{item.label}",
        )
        for item in items
    ]
