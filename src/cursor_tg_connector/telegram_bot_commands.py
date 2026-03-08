from __future__ import annotations

import html

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
from cursor_tg_connector.services_notification import TelegramNotifier
from cursor_tg_connector.services_pull_request_service import PullRequestActionError
from cursor_tg_connector.telegram_bot_common import (
    BOT_COMMANDS,
    get_message_thread_id,
    get_services,
    render_agent_keyboard,
    render_model_keyboard,
    render_pull_request_keyboard,
    render_reset_db_keyboard,
    render_thread_mode_keyboard,
    render_unread_mode_keyboard,
)
from cursor_tg_connector.telegram_threads import close_agent_thread
from cursor_tg_connector.utils_formatting import (
    build_active_agent_message,
    build_agent_info_message,
    build_agents_summary_message,
    build_reset_db_prompt,
    build_thread_command_guidance,
    build_thread_mode_guidance,
    build_thread_mode_status,
    build_user_history_message,
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
    "• Send /history <count> to replay the last N conversation messages.\n"
    "• Send /focus to choose the active agent from clickable options in non-thread mode.\n"
    "• Send /configure_unread full|count|none to control non-active agent notices.\n"
    "• Send /unfocus to clear the current active agent selection.\n"
    "• Send /stop to stop the currently selected running agent.\n"
    "• Send /close from inside an agent thread to close and unbind that Telegram thread.\n"
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
_HISTORY_USAGE_TEXT = "Usage: /history <count> (count must be a positive integer)"


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


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    services = get_services(context)
    state_repo = services.create_agent_service.state_repo
    session = await state_repo.get_session(services.settings.telegram_allowed_user_id)
    if not session.thread_mode_enabled:
        await update.effective_message.reply_text(build_thread_command_guidance())
        return

    thread_id = get_message_thread_id(update)
    if thread_id is None:
        await update.effective_message.reply_text(build_thread_command_guidance())
        return

    binding = await state_repo.get_thread_binding(update.effective_chat.id, thread_id)
    if binding is None:
        await update.effective_message.reply_text(build_thread_command_guidance())
        return

    await update.effective_message.reply_text(
        "Closing this Telegram thread. Use /agents in the root chat to create a new one later."
    )
    try:
        await close_agent_thread(bot=context.bot, binding=binding)
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Couldn't close this Telegram thread: {exc}"
        )
        return

    await state_repo.delete_agent_thread_binding(binding.agent_id)
    await state_repo.clear_notice_state(binding.agent_id)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorize_and_record_chat(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    count = _parse_history_count(getattr(context, "args", []))
    if count is None:
        await message.reply_text(_HISTORY_USAGE_TEXT)
        return

    services = get_services(context)
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    message_thread_id = get_message_thread_id(update)
    if session.thread_mode_enabled:
        agent_id = await services.agent_service.resolve_context_agent_id(
            telegram_user_id=services.settings.telegram_allowed_user_id,
            chat_id=update.effective_chat.id,
            message_thread_id=message_thread_id,
        )
        if agent_id is None:
            await message.reply_text(build_thread_command_guidance())
            return
    else:
        agent = await services.agent_service.ensure_active_agent_exists(
            services.settings.telegram_allowed_user_id,
        )
        if agent is None:
            await message.reply_text(
                "No active agent selected. Use /focus to pick one."
            )
            return
        agent_id = agent.id

    agent, history_messages, assistant_total = await services.agent_service.get_recent_history(
        agent_id,
        count,
    )
    if not history_messages:
        await message.reply_text(f"No conversation history found for {agent.name or agent.id}.")
        await services.agent_service.mark_history_delivered(agent_id, assistant_total)
        return

    notifier = TelegramNotifier(context.bot)
    for history_message in history_messages:
        rendered = (
            build_user_history_message(history_message.text)
            if history_message.type == "user_message"
            else build_active_agent_message(agent, history_message.text)
        )
        await notifier.send_text(
            update.effective_chat.id,
            rendered,
            message_thread_id=message_thread_id if session.thread_mode_enabled else None,
        )

    await services.agent_service.mark_history_delivered(agent_id, assistant_total)


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

    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if not context.args:
        await _reply_with_unread_configuration(
            message,
            session.unselected_agent_unread_mode,
        )
        return

    mode = _parse_unread_mode(context.args[0])
    if mode is None:
        await _reply_with_unread_configuration(
            message,
            session.unselected_agent_unread_mode,
            intro="Unknown unread mode. Choose one below or use /configure_unread full|count|none.",
        )
        return

    await services.create_agent_service.state_repo.set_unselected_agent_unread_mode(
        services.settings.telegram_allowed_user_id,
        mode,
    )
    await _reply_with_unread_configuration(
        message,
        mode,
        intro=(
            "Unread handling for unselected agents is now set to "
            f"{_describe_unread_mode(mode)}."
        ),
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
        summary = build_agents_summary_message(
            items,
            threaded=True,
        )
        await update.effective_message.reply_text(
            summary,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    summary = build_agents_summary_message(items, threaded=False)
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

    message = update.effective_message
    if message is None:
        return

    services = get_services(context)
    args = [arg.lower() for arg in getattr(context, "args", [])]
    current = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )

    if not args or args[0] == "status":
        await _reply_with_thread_mode_configuration(
            message,
            current.thread_mode_enabled,
        )
        return

    if args[0] == "on":
        text, enabled = await _set_thread_mode_enabled(update, context, True)
        await _reply_with_thread_mode_configuration(
            message,
            enabled,
            intro=text,
        )
        return

    if args[0] == "off":
        text, enabled = await _set_thread_mode_enabled(update, context, False)
        await _reply_with_thread_mode_configuration(
            message,
            enabled,
            intro=text,
        )
        return

    await _reply_with_thread_mode_configuration(
        message,
        current.thread_mode_enabled,
        intro="Unknown thread mode option. Choose one below or use /threadmode on|off|status.",
    )


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
            text = f"{text}\n\n{html.escape(_PR_ACTIONS_DISABLED_TEXT)}"
    await message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


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
        bot_user = await context.bot.get_me()
    except TelegramError as exc:
        return f"Couldn't verify the bot's Threaded Mode setting: {exc}"

    has_topics_enabled = getattr(bot_user, "has_topics_enabled", None)
    if has_topics_enabled is None:
        api_kwargs = getattr(bot_user, "api_kwargs", None)
        if isinstance(api_kwargs, dict):
            has_topics_enabled = api_kwargs.get("has_topics_enabled")
    if not bool(has_topics_enabled):
        return (
            "Thread mode requires Telegram Threaded Mode to be enabled for this bot in "
            "@BotFather."
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


def _parse_history_count(args: list[str]) -> int | None:
    if len(args) != 1:
        return None
    try:
        count = int(args[0])
    except ValueError:
        return None
    return count if count > 0 else None


def _describe_unread_mode(mode: UnselectedAgentUnreadMode) -> str:
    descriptions = {
        UnselectedAgentUnreadMode.FULL: "full text delivery",
        UnselectedAgentUnreadMode.COUNT: "unread count notices",
        UnselectedAgentUnreadMode.NONE: "no notifications",
    }
    return descriptions[mode]


def _build_unread_command_text(
    current_mode: UnselectedAgentUnreadMode | None,
    intro: str | None = None,
) -> str:
    sections: list[str] = []
    if intro:
        sections.append(intro)
    if current_mode is not None:
        sections.append(f"Current setting: {_describe_unread_mode(current_mode)}.")
    sections.append(
        "Choose how unread messages from unselected agents are delivered.\n"
        "• full — deliver unread messages from unselected agents in full.\n"
        "• count — send only unread-count notices (default).\n"
        "• none — send nothing until you switch to that agent."
    )
    return "\n\n".join(sections)


def _build_thread_mode_command_text(enabled: bool, intro: str | None = None) -> str:
    sections = [intro] if intro else []
    sections.append(build_thread_mode_status(enabled))
    sections.append("Choose the routing mode below.")
    return "\n\n".join(sections)


async def _reply_with_unread_configuration(
    message,
    current_mode: UnselectedAgentUnreadMode,
    *,
    intro: str | None = None,
) -> None:
    await message.reply_text(
        _build_unread_command_text(current_mode, intro=intro),
        reply_markup=render_unread_mode_keyboard(current_mode),
    )


async def _reply_with_thread_mode_configuration(
    message,
    enabled: bool,
    *,
    intro: str | None = None,
) -> None:
    await message.reply_text(
        _build_thread_mode_command_text(enabled, intro=intro),
        reply_markup=render_thread_mode_keyboard(enabled),
    )


async def _set_thread_mode_enabled(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    enabled: bool,
) -> tuple[str, bool]:
    services = get_services(context)
    current = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if current.thread_mode_enabled == enabled:
        status = "enabled" if enabled else "disabled"
        return f"Thread mode is already {status}.", enabled

    if enabled:
        prerequisite_error = await _get_thread_mode_prerequisite_error(update, context)
        if prerequisite_error is not None:
            return prerequisite_error, current.thread_mode_enabled

    updated = await services.create_agent_service.state_repo.set_thread_mode_enabled(
        services.settings.telegram_allowed_user_id,
        enabled,
    )
    text = f"Thread mode is now {'enabled' if enabled else 'disabled'}."
    if not enabled:
        text = f"{text}\n\nExisting thread bindings were preserved."
    return text, updated.thread_mode_enabled


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
