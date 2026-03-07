from __future__ import annotations

import base64
import logging

from telegram import PhotoSize, Update
from telegram.ext import ContextTypes

from cursor_tg_connector.cursor_api_models import PromptImage
from cursor_tg_connector.domain_types import WizardStep
from cursor_tg_connector.services_create_agent_service import CreateAgentError
from cursor_tg_connector.services_followup_service import FollowupError
from cursor_tg_connector.services_notification import TelegramNotifier
from cursor_tg_connector.telegram_bot_common import get_services
from cursor_tg_connector.utils_formatting import build_agent_created_message

logger = logging.getLogger(__name__)


async def _download_photo(photo: PhotoSize) -> PromptImage:
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    return PromptImage(
        data=base64.b64encode(data).decode(),
        dimension={"width": photo.width, "height": photo.height},
    )


async def _extract_images(update: Update) -> list[PromptImage]:
    msg = update.effective_message
    if not msg or not msg.photo:
        return []
    best = msg.photo[-1]
    try:
        return [await _download_photo(best)]
    except Exception:
        logger.warning("Failed to download photo from Telegram", exc_info=True)
        return []


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    msg = update.effective_message
    text = msg.text or msg.caption or ""

    if session.wizard_state == WizardStep.WAITING_MODEL:
        await msg.reply_text("Use the inline buttons to select a model.")
        return

    if session.wizard_state == WizardStep.WAITING_REPOSITORY:
        await msg.reply_text("Use the inline buttons to select a repository.")
        return

    if session.wizard_state == WizardStep.WAITING_BRANCH:
        try:
            await services.create_agent_service.save_branch(
                services.settings.telegram_allowed_user_id,
                text,
            )
        except CreateAgentError as exc:
            await msg.reply_text(str(exc))
            return
        await msg.reply_text(
            "Step 4/4: Send the prompt (text, or photo with caption) for the new agent."
        )
        return

    if session.wizard_state == WizardStep.WAITING_PROMPT:
        images = await _extract_images(update)
        try:
            agent = await services.create_agent_service.finish_prompt(
                services.settings.telegram_allowed_user_id,
                text,
                images=images or None,
            )
        except CreateAgentError as exc:
            await msg.reply_text(str(exc))
            return

        await notifier.send_text(
            update.effective_chat.id,
            build_agent_created_message(agent),
        )
        return

    images = await _extract_images(update)
    try:
        delivered_count = await services.followup_service.send_followup(
            services.settings.telegram_allowed_user_id,
            update.effective_chat.id,
            text,
            notifier,
            images=images or None,
        )
    except FollowupError as exc:
        await msg.reply_text(str(exc))
        return

    if delivered_count == 0:
        await msg.reply_text(
            "Follow-up sent. No new agent response is available yet; polling will keep checking."
        )
