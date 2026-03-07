from __future__ import annotations

import logging
from typing import Protocol

from telegram import Bot

from cursor_tg_connector.utils_formatting import chunk_message, markdown_to_telegram_html

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_text(self, chat_id: int, text: str) -> None:
        for chunk in chunk_message(text):
            try:
                html_chunk = markdown_to_telegram_html(chunk)
                await self.bot.send_message(
                    chat_id=chat_id, text=html_chunk, parse_mode="HTML"
                )
            except Exception:
                logger.debug("HTML send failed, falling back to plain text")
                await self.bot.send_message(chat_id=chat_id, text=chunk)
