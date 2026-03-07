from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.services_notification import TelegramNotifier
from cursor_tg_connector.telegram_bot_common import (
    BRANCH_PAGE_PREFIX,
    BRANCH_SELECT_PREFIX,
    MODEL_PAGE_PREFIX,
    MODEL_SELECT_PREFIX,
    REPO_PAGE_PREFIX,
    REPO_SELECT_PREFIX,
    RESET_DB_CANCEL_PREFIX,
    RESET_DB_CONFIRM_PREFIX,
    SWITCH_AGENT_PREFIX,
    get_services,
    render_branch_keyboard,
    render_model_keyboard,
    render_repository_keyboard,
)
from cursor_tg_connector.telegram_threads import ensure_agent_thread
from cursor_tg_connector.utils_formatting import (
    build_reset_db_cancelled,
    build_reset_db_success,
    build_thread_opened_message,
    build_thread_ready_message,
)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    services = get_services(context)
    if (
        update.effective_user is None
        or update.effective_user.id != services.settings.telegram_allowed_user_id
    ):
        await query.answer("Unauthorized", show_alert=True)
        return

    data = query.data or ""
    if data == "noop":
        await query.answer()
        return

    if data.startswith(SWITCH_AGENT_PREFIX):
        await _switch_agent(update, context, data[len(SWITCH_AGENT_PREFIX) :])
        return
    if data == RESET_DB_CONFIRM_PREFIX:
        await _confirm_reset_db(update, context)
        return
    if data == RESET_DB_CANCEL_PREFIX:
        await _cancel_reset_db(update)
        return
    if data.startswith(MODEL_PAGE_PREFIX):
        await _show_model_page(update, context, int(data[len(MODEL_PAGE_PREFIX) :]))
        return
    if data.startswith(MODEL_SELECT_PREFIX):
        await _select_model(update, context, data[len(MODEL_SELECT_PREFIX) :])
        return
    if data.startswith(REPO_PAGE_PREFIX):
        await _show_repository_page(update, context, int(data[len(REPO_PAGE_PREFIX) :]))
        return
    if data.startswith(REPO_SELECT_PREFIX):
        await _select_repository(update, context, int(data[len(REPO_SELECT_PREFIX) :]))
        return
    if data.startswith(BRANCH_PAGE_PREFIX):
        await _show_branch_page(update, context, int(data[len(BRANCH_PAGE_PREFIX) :]))
        return
    if data.startswith(BRANCH_SELECT_PREFIX):
        await _select_branch(update, context, int(data[len(BRANCH_SELECT_PREFIX) :]))
        return

    await query.answer()


async def _switch_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: str) -> None:
    query = update.callback_query
    services = get_services(context)
    notifier = TelegramNotifier(context.bot)
    session = await services.create_agent_service.state_repo.get_session(
        services.settings.telegram_allowed_user_id
    )
    if not session.thread_mode_enabled:
        agent = await services.agent_service.switch_active_agent(
            services.settings.telegram_allowed_user_id,
            update.effective_chat.id,
            agent_id,
        )
        await query.answer("Active agent switched")
        await query.edit_message_text(f"Active agent: {agent.name or agent.id}")
        await services.agent_service.deliver_active_agent_unread(
            agent_id=agent_id,
            notifier=notifier,
            chat_id=update.effective_chat.id,
            limit=10,
        )
        return

    await services.create_agent_service.state_repo.update_chat_context(
        services.settings.telegram_allowed_user_id,
        update.effective_chat.id,
    )
    agent = await services.agent_service.cursor_client.get_agent(agent_id)
    try:
        binding, created = await ensure_agent_thread(
            bot=context.bot,
            state_repo=services.create_agent_service.state_repo,
            agent=agent,
            chat_id=update.effective_chat.id,
        )
    except Exception:
        await query.answer("Failed to create thread", show_alert=True)
        await query.edit_message_text(
            "Unable to create or open an agent thread. Make sure Telegram "
            "threaded mode is enabled for this chat."
        )
        return

    await query.answer("Agent thread ready")
    await query.edit_message_text(build_thread_opened_message(agent, created))
    await notifier.send_text(
        binding.telegram_chat_id,
        build_thread_ready_message(agent),
        message_thread_id=binding.message_thread_id,
    )
    await services.agent_service.deliver_active_agent_unread(
        agent_id=agent_id,
        notifier=notifier,
        chat_id=binding.telegram_chat_id,
        message_thread_id=binding.message_thread_id,
        limit=10,
    )


async def _confirm_reset_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    services = get_services(context)
    await services.database.reset()
    await services.create_agent_service.state_repo.update_chat_context(
        services.settings.telegram_allowed_user_id,
        update.effective_chat.id,
    )
    await query.answer("DB reset")
    await query.edit_message_text(build_reset_db_success())


async def _cancel_reset_db(update: Update) -> None:
    query = update.callback_query
    await query.answer("Cancelled")
    await query.edit_message_text(build_reset_db_cancelled())


async def _show_model_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    query = update.callback_query
    services = get_services(context)
    page_data = await services.create_agent_service.get_model_page(
        services.settings.telegram_allowed_user_id,
        page,
    )
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=render_model_keyboard(page_data))


async def _select_model(update: Update, context: ContextTypes.DEFAULT_TYPE, model_id: str) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        page_data = await services.create_agent_service.choose_model(
            services.settings.telegram_allowed_user_id,
            model_id,
        )
        session = await services.create_agent_service.get_session(
            services.settings.telegram_allowed_user_id
        )
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    repositories = session.wizard_payload["repositories"]
    await query.answer("Model selected")
    await query.edit_message_text(
        "Step 2/4: Select a repository URL.",
        reply_markup=render_repository_keyboard(page_data, repositories),
    )


async def _show_repository_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        page_data = await services.create_agent_service.get_repository_page(
            services.settings.telegram_allowed_user_id,
            page,
        )
        session = await services.create_agent_service.get_session(
            services.settings.telegram_allowed_user_id
        )
        repositories = session.wizard_payload["repositories"]
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    await query.edit_message_reply_markup(
        reply_markup=render_repository_keyboard(page_data, repositories)
    )


async def _select_repository(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    repository_index: int,
) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        repository, branches = await services.create_agent_service.choose_repository(
            services.settings.telegram_allowed_user_id,
            repository_index,
        )
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    page_data = services.create_agent_service.get_branch_page_from_payload(branches, 0)
    await query.answer("Repository selected")
    await query.edit_message_text(
        f"Step 3/4: Select a base branch for {repository}, or type a branch name.",
        reply_markup=render_branch_keyboard(page_data, branches),
    )


async def _show_branch_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        page_data = await services.create_agent_service.get_branch_page(
            services.settings.telegram_allowed_user_id,
            page,
        )
        session = await services.create_agent_service.get_session(
            services.settings.telegram_allowed_user_id
        )
        branches = session.wizard_payload["branches"]
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    await query.edit_message_reply_markup(
        reply_markup=render_branch_keyboard(page_data, branches)
    )


async def _select_branch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    branch_index: int,
) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        await services.create_agent_service.choose_branch(
            services.settings.telegram_allowed_user_id,
            branch_index,
        )
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer("Branch selected")
    await query.edit_message_text("Step 4/4: Send the prompt text for the new agent.")
