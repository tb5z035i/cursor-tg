from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.telegram_bot_common import (
    MODEL_PAGE_PREFIX,
    MODEL_SELECT_PREFIX,
    REPO_PAGE_PREFIX,
    REPO_SELECT_PREFIX,
    SWITCH_AGENT_PREFIX,
    get_services,
    render_model_keyboard,
    render_repository_keyboard,
)
from cursor_tg_connector.utils_formatting import build_agent_created_message


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    services = get_services(context)
    if update.effective_user is None or update.effective_user.id != services.settings.telegram_allowed_user_id:
        await query.answer("Unauthorized", show_alert=True)
        return

    data = query.data or ""
    if data == "noop":
        await query.answer()
        return

    if data.startswith(SWITCH_AGENT_PREFIX):
        await _switch_agent(update, context, data[len(SWITCH_AGENT_PREFIX) :])
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

    await query.answer()


async def _switch_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_id: str) -> None:
    query = update.callback_query
    services = get_services(context)
    agent = await services.agent_service.switch_active_agent(
        services.settings.telegram_allowed_user_id,
        update.effective_chat.id,
        agent_id,
    )
    await query.answer("Active agent switched")
    await query.edit_message_text(f"Active agent: {agent.name or agent.id}")


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
        session = await services.create_agent_service.get_session(services.settings.telegram_allowed_user_id)
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    repositories = session.wizard_payload["repositories"]
    await query.answer("Model selected")
    await query.edit_message_text(
        "Step 2/4: Select a repository URL.",
        reply_markup=render_repository_keyboard(page_data, repositories),
    )


async def _show_repository_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        page_data = await services.create_agent_service.get_repository_page(
            services.settings.telegram_allowed_user_id,
            page,
        )
        session = await services.create_agent_service.get_session(services.settings.telegram_allowed_user_id)
        repositories = session.wizard_payload["repositories"]
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=render_repository_keyboard(page_data, repositories))


async def _select_repository(update: Update, context: ContextTypes.DEFAULT_TYPE, repository_index: int) -> None:
    query = update.callback_query
    services = get_services(context)
    try:
        repository = await services.create_agent_service.choose_repository(
            services.settings.telegram_allowed_user_id,
            repository_index,
        )
    except CreateAgentError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer("Repository selected")
    await query.edit_message_text(
        f"Step 3/4: Send the base branch name for {repository}.",
    )
