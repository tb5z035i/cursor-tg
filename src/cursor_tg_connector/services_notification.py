from __future__ import annotations

from typing import Protocol

from telegram import Bot

from cursor_tg_connector.utils_formatting import chunk_message


class Notifier(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_text(self, chat_id: int, text: str) -> None:
        for chunk in chunk_message(text):
            await self.bot.send_message(chat_id=chat_id, text=chunk)
