from __future__ import annotations

import logging
from typing import Protocol

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ChatAction

from cursor_tg_connector.utils_formatting import chunk_message, markdown_to_telegram_html

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send_text(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...
    async def send_typing(self, chat_id: int, message_thread_id: int | None = None) -> None: ...


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_text(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        chunks = chunk_message(text)
        for index, chunk in enumerate(chunks):
            chunk_markup = reply_markup if index == len(chunks) - 1 else None
            try:
                html_chunk = markdown_to_telegram_html(chunk)
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=html_chunk,
                    parse_mode="HTML",
                    message_thread_id=message_thread_id,
                    reply_markup=chunk_markup,
                )
            except Exception:
                logger.debug("HTML send failed, falling back to plain text")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    message_thread_id=message_thread_id,
                    reply_markup=chunk_markup,
                )

    async def send_typing(self, chat_id: int, message_thread_id: int | None = None) -> None:
        try:
            await self.bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING,
                message_thread_id=message_thread_id,
            )
        except Exception:
            logger.debug("Failed to send typing action")
