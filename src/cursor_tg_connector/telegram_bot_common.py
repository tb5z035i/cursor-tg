from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from cursor_tg_connector.config import Settings
from cursor_tg_connector.github_api_models import GitHubMergeMethod, GitHubPullRequest
from cursor_tg_connector.persistence_db import Database
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.services_create_agent_service import CreateAgentService, RepositoryPage
from cursor_tg_connector.services_followup_service import FollowupService
from cursor_tg_connector.services_polling_service import PollingService
from cursor_tg_connector.services_pull_request_service import PullRequestService
from cursor_tg_connector.telegram_bot_constants import (
    BRANCH_PAGE_PREFIX,
    BRANCH_SELECT_PREFIX,
    MODEL_PAGE_PREFIX,
    MODEL_SELECT_PREFIX,
    PR_MERGE_PREFIX,
    PR_READY_PREFIX,
    PR_SHOW_PREFIX,
    REPO_PAGE_PREFIX,
    REPO_SELECT_PREFIX,
    RESET_DB_CANCEL_PREFIX,
    RESET_DB_CONFIRM_PREFIX,
    SWITCH_AGENT_PREFIX,
)

BOT_COMMANDS: list[tuple[str, str]] = [
    ("current", "Show info about the current active agent"),
    ("agents", "List agents or open their thread in threaded mode"),
    ("focus", "Choose the active agent from clickable options"),
    ("configure_unread", "Configure unread messages for unselected agents"),
    ("unfocus", "Clear the active agent selection"),
    ("stop", "Stop the current running active agent"),
    ("clear", "Mark all unread messages as read for the active agent"),
    ("threadmode", "Toggle per-agent Telegram thread routing"),
    ("newagent", "Create a new Cursor cloud agent"),
    ("pr", "Show the current agent pull request and actions"),
    ("ready", "Mark the current agent pull request ready for review"),
    ("merge", "Merge the current agent pull request"),
    ("cancel", "Cancel an in-progress create-agent wizard"),
    ("resetdb", "Reset local SQLite state after confirmation"),
    ("help", "Show available commands and usage"),
]


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    agent_service: AgentService
    create_agent_service: CreateAgentService
    followup_service: FollowupService
    polling_service: PollingService
    pull_request_service: PullRequestService


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


def render_branch_keyboard(
    page_data: RepositoryPage,
    all_branches: list[str],
) -> InlineKeyboardMarkup:
    start_index = page_data.page * 8
    rows = [
        [
            InlineKeyboardButton(
                branch,
                callback_data=f"{BRANCH_SELECT_PREFIX}{start_index + index}",
            )
        ]
        for index, branch in enumerate(page_data.repositories)
    ]
    rows.extend(_pagination_rows(page_data.page, page_data.total_pages, BRANCH_PAGE_PREFIX))
    return InlineKeyboardMarkup(rows)


def render_agent_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{SWITCH_AGENT_PREFIX}{agent_id}")]
        for agent_id, label in items
    ]
    return InlineKeyboardMarkup(rows)


def render_reset_db_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Reset DB", callback_data=RESET_DB_CONFIRM_PREFIX),
                InlineKeyboardButton("Cancel", callback_data=RESET_DB_CANCEL_PREFIX),
            ]
        ]
    )


def render_pull_request_keyboard(
    *,
    agent_id: str,
    pull_request: GitHubPullRequest,
    default_merge_method: GitHubMergeMethod,
) -> InlineKeyboardMarkup | None:
    if pull_request.merged or pull_request.state.lower() != "open":
        return None

    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [InlineKeyboardButton("Refresh PR", callback_data=f"{PR_SHOW_PREFIX}{agent_id}")]
    )
    if pull_request.draft:
        rows.append(
            [InlineKeyboardButton("Ready for review", callback_data=f"{PR_READY_PREFIX}{agent_id}")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                f"Merge ({default_merge_method})",
                callback_data=f"{PR_MERGE_PREFIX}{default_merge_method}:{agent_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def get_message_thread_id(update: Update) -> int | None:
    effective_message = getattr(update, "effective_message", None)
    if effective_message and effective_message.message_thread_id is not None:
        return update.effective_message.message_thread_id
    callback_query = getattr(update, "callback_query", None)
    if callback_query and callback_query.message:
        return callback_query.message.message_thread_id
    return None


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
