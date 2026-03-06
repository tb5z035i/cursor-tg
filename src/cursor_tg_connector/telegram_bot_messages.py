from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from cursor_tg_connector.domain_types import WizardStep
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.services_followup_service import FollowupError
from cursor_tg_connector.services_notification import TelegramNotifier
from cursor_tg_connector.telegram_bot_common import get_services
from cursor_tg_connector.utils_formatting import build_agent_created_message


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    services = get_services(context)
    if update.effective_user.id != services.settings.telegram_allowed_user_id:
        return

    notifier = TelegramNotifier(context.bot)
    await services.create_agent_service.state_repo.update_chat_context(
        services.settings.telegram_allowed_user_id,
        update.effective_chat.id,
    )
    session = await services.create_agent_service.get_session(
        services.settings.telegram_allowed_user_id
    )
    text = update.effective_message.text or ""

    if session.wizard_state == WizardStep.WAITING_MODEL:
        await update.effective_message.reply_text("Use the inline buttons to select a model.")
        return

    if session.wizard_state == WizardStep.WAITING_REPOSITORY:
        await update.effective_message.reply_text("Use the inline buttons to select a repository.")
        return

    if session.wizard_state == WizardStep.WAITING_BRANCH:
        try:
            await services.create_agent_service.save_branch(
                services.settings.telegram_allowed_user_id,
                text,
            )
        except CreateAgentError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        await update.effective_message.reply_text(
            "Step 4/4: Send the prompt text for the new agent."
        )
        return

    if session.wizard_state == WizardStep.WAITING_PROMPT:
        try:
            agent = await services.create_agent_service.finish_prompt(
                services.settings.telegram_allowed_user_id,
                text,
            )
        except CreateAgentError as exc:
            await update.effective_message.reply_text(str(exc))
            return

        await update.effective_message.reply_text(build_agent_created_message(agent))
        return

    try:
        delivered_count = await services.followup_service.send_followup(
            services.settings.telegram_allowed_user_id,
            update.effective_chat.id,
            text,
            notifier,
        )
    except FollowupError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    if delivered_count == 0:
        await update.effective_message.reply_text(
            "Follow-up sent. No new agent response is available yet; polling will keep checking."
        )
