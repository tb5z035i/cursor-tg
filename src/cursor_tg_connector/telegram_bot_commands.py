from __future__ import annotations

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from cursor_tg_connector.cursor_api_client import CursorApiError
from cursor_tg_connector.cursor_api_models import Agent
from cursor_tg_connector.domain_types import UnselectedAgentUnreadMode
from cursor_tg_connector.github_api_client import GitHubApiError
from cursor_tg_connector.github_api_models import GitHubMergeMethod
from cursor_tg_connector.services_agent_service import AgentStopError
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.services_pull_request_service import PullRequestActionError
from cursor_tg_connector.telegram_bot_common import (
    BOT_COMMANDS,
    get_message_thread_id,
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
    render_pull_request_keyboard,
    render_reset_db_keyboard,
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

_PROJECT_GITHUB_URL = "https://github.com/tb5z035i/cursor-tg"

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
    "• Send /threadmode on to route each agent into its own Telegram thread.\n"
    "• Send /newagent to create a new agent (model → repo → branch → prompt).\n"
    "• Send /pr to inspect the current agent pull request and use action buttons.\n"
    "• Send /ready to mark the current agent pull request ready for review.\n"
    "• Send /merge [merge|squash|rebase] to merge the current agent pull request.\n"
    "• Send any text message to follow up with the active agent or from inside an agent thread.\n"
    "• In thread mode, root-chat notices can still appear for unbound agents based on "
    "/configure_unread.\n"
    "• Use /resetdb if you want to wipe and reinitialize local bot state.\n"
    "\n"
    f"GitHub: {_PROJECT_GITHUB_URL}"
)

_STOP_HELP_TEXT = (
    "No active agent selected.\n"
    "\n"
    "Use /focus to pick a running agent, then send /stop to stop it."
)

_PR_ACTIONS_DISABLED_TEXT = (
    "PR actions are unavailable. Set GITHUB_TOKEN (or GITHUB_PAT) to enable ready-for-review "
    "and merge actions."
)

_MERGE_USAGE_TEXT = "Usage: /merge [merge|squash|rebase]"


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

    agent = await _resolve_context_agent(update, context)
    if agent is None:
        return

    await _reply_with_agent_overview(update, context, agent)


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


async def pr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    agent = await _resolve_context_agent(update, context)
    if agent is None:
        return

    if not agent.target.pr_url:
        await update.effective_message.reply_text(
            f"{agent.name or agent.id} does not have a pull request yet."
        )
        return

    await _reply_with_agent_overview(update, context, agent, include_disabled_hint=True)


async def ready_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    agent = await _resolve_context_agent(update, context)
    if agent is None:
        return

    services = get_services(context)
    pull_request_service = getattr(services, "pull_request_service", None)
    if pull_request_service is None:
        await update.effective_message.reply_text(_PR_ACTIONS_DISABLED_TEXT)
        return

    try:
        pull_request = await pull_request_service.mark_ready_for_review(agent)
    except (PullRequestActionError, GitHubApiError) as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"Marked PR #{pull_request.number} ready for review.\n{pull_request.html_url}"
    )


async def merge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    merge_method = _parse_merge_method(context.args[0]) if context.args else None
    if context.args and merge_method is None:
        await update.effective_message.reply_text(_MERGE_USAGE_TEXT)
        return

    agent = await _resolve_context_agent(update, context)
    if agent is None:
        return

    services = get_services(context)
    pull_request_service = getattr(services, "pull_request_service", None)
    if pull_request_service is None:
        await update.effective_message.reply_text(_PR_ACTIONS_DISABLED_TEXT)
        return

    try:
        result = await pull_request_service.merge_pull_request(
            agent,
            merge_method=merge_method or services.settings.github_default_merge_method,
        )
    except (PullRequestActionError, GitHubApiError) as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"Merged {agent.target.pr_url} using "
        f"{merge_method or services.settings.github_default_merge_method}.\n{result.message}"
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
        prerequisite_error = await _get_thread_mode_prerequisite_error(update, context)
        if prerequisite_error is not None:
            await update.effective_message.reply_text(prerequisite_error)
            return
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


async def _reply_with_agent_overview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: Agent,
    *,
    include_disabled_hint: bool = False,
) -> None:
    message = update.effective_message
    if message is None:
        return

    text, reply_markup = await _build_agent_overview(update, context, agent)
    if include_disabled_hint and agent.target.pr_url and reply_markup is None:
        services = get_services(context)
        pull_request_service = getattr(services, "pull_request_service", None)
        if pull_request_service is None or not pull_request_service.enabled:
            text = f"{text}\n\n{_PR_ACTIONS_DISABLED_TEXT}"
    await message.reply_text(text, reply_markup=reply_markup)


async def _build_agent_overview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: Agent,
) -> tuple[str, object | None]:
    services = get_services(context)
    pull_request_service = getattr(services, "pull_request_service", None)
    if not agent.target.pr_url or pull_request_service is None or not pull_request_service.enabled:
        return build_agent_info_message(agent), None

    try:
        pull_request = await pull_request_service.get_pull_request(agent)
    except (PullRequestActionError, GitHubApiError) as exc:
        return f"{build_agent_info_message(agent)}\n\nPR actions unavailable: {exc}", None

    return (
        build_agent_info_message(agent, pull_request),
        render_pull_request_keyboard(
            agent_id=agent.id,
            pull_request=pull_request,
            default_merge_method=services.settings.github_default_merge_method,
        ),
    )


async def _resolve_context_agent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Agent | None:
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
            return None
        return await services.agent_service.cursor_client.get_agent(agent_id)

    agent = await services.agent_service.ensure_active_agent_exists(
        services.settings.telegram_allowed_user_id,
    )
    if agent is None:
        await update.effective_message.reply_text(
            "No active agent selected. Use /focus to pick one."
        )
    return agent


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


async def _get_thread_mode_prerequisite_error(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    chat = update.effective_chat
    if chat is None:
        return "Thread mode can only be enabled from inside a Telegram chat."

    try:
        chat_info = await context.bot.get_chat(chat.id)
    except TelegramError as exc:
        return f"Couldn't verify Telegram topic settings for this chat: {exc}"

    if getattr(chat_info, "type", None) != "supergroup" or not bool(
        getattr(chat_info, "is_forum", False)
    ):
        return (
            "Thread mode can only be enabled in a Telegram supergroup with Topics turned on."
        )

    permissions = getattr(chat_info, "permissions", None)
    if bool(getattr(permissions, "can_manage_topics", False)):
        return (
            'Thread mode requires the Telegram chat setting "Disallow users to create '
            'new threads" to be enabled.'
        )

    try:
        bot_user = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(chat.id, bot_user.id)
    except TelegramError as exc:
        return f"Couldn't verify the bot's topic permissions for this chat: {exc}"

    member_status = getattr(bot_member, "status", "")
    if member_status not in {"administrator", "creator", "owner"}:
        return (
            "Thread mode requires the bot to be a chat admin with permission to manage "
            "topics."
        )

    if member_status == "administrator" and not bool(
        getattr(bot_member, "can_manage_topics", False)
    ):
        return (
            "Thread mode requires the bot to have the Telegram Manage Topics "
            "administrator permission."
        )

    return None


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


def _parse_merge_method(value: str) -> GitHubMergeMethod | None:
    normalized = value.strip().lower()
    if normalized in {"merge", "squash", "rebase"}:
        return normalized
    return None


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
