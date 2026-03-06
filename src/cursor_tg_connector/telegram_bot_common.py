from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from cursor_tg_connector.config import Settings
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.services_create_agent_service import CreateAgentService, RepositoryPage
from cursor_tg_connector.services_followup_service import FollowupService
from cursor_tg_connector.services_polling_service import PollingService

SWITCH_AGENT_PREFIX = "agent:switch:"
MODEL_SELECT_PREFIX = "wizard:model:"
MODEL_PAGE_PREFIX = "wizard:model_page:"
REPO_SELECT_PREFIX = "wizard:repo:"
REPO_PAGE_PREFIX = "wizard:repo_page:"


@dataclass(slots=True)
class AppServices:
    settings: Settings
    agent_service: AgentService
    create_agent_service: CreateAgentService
    followup_service: FollowupService
    polling_service: PollingService


def get_services(context: ContextTypes.DEFAULT_TYPE) -> AppServices:
    return context.application.bot_data["services"]


async def ensure_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    services = get_services(context)
    allowed_user_id = services.settings.telegram_allowed_user_id
    user = update.effective_user
    if user and user.id == allowed_user_id:
        return True

    if update.callback_query:
        await update.callback_query.answer("Unauthorized", show_alert=True)
    return False


def render_model_keyboard(page_data: RepositoryPage) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(model, callback_data=f"{MODEL_SELECT_PREFIX}{model}")]
        for model in page_data.repositories
    ]
    rows.extend(_pagination_rows(page_data.page, page_data.total_pages, MODEL_PAGE_PREFIX))
    return InlineKeyboardMarkup(rows)


def render_repository_keyboard(
    page_data: RepositoryPage,
    all_repositories: list[str],
) -> InlineKeyboardMarkup:
    start_index = page_data.page * 8
    rows = [
        [
            InlineKeyboardButton(
                repository,
                callback_data=f"{REPO_SELECT_PREFIX}{start_index + index}",
            )
        ]
        for index, repository in enumerate(page_data.repositories)
    ]
    rows.extend(_pagination_rows(page_data.page, page_data.total_pages, REPO_PAGE_PREFIX))
    return InlineKeyboardMarkup(rows)


def render_agent_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{SWITCH_AGENT_PREFIX}{agent_id}")]
        for agent_id, label in items
    ]
    return InlineKeyboardMarkup(rows)


def _pagination_rows(page: int, total_pages: int, prefix: str) -> list[list[InlineKeyboardButton]]:
    if total_pages <= 1:
        return []

    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{prefix}{page - 1}"))
    buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"{prefix}{page + 1}"))
    return [buttons]
