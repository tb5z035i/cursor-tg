from __future__ import annotations

import asyncio

from cursor_tg_connector.config import Settings
from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.utils_formatting import build_active_agent_message


class FollowupError(RuntimeError):
    pass


class FollowupService:
    def __init__(
        self,
        *,
        settings: Settings,
        cursor_client: CursorApiClient,
        state_repo: StateRepository,
        agent_service: AgentService,
    ) -> None:
        self.settings = settings
        self.cursor_client = cursor_client
        self.state_repo = state_repo
        self.agent_service = agent_service

    async def send_followup(
        self,
        telegram_user_id: int,
        chat_id: int,
        text: str,
        notifier,
    ) -> int:
        text = text.strip()
        if not text:
            raise FollowupError("Message cannot be empty.")

        session = await self.state_repo.update_chat_context(telegram_user_id, chat_id)
        if not session.active_agent_id:
            raise FollowupError("No active agent selected. Use /agents first.")

        await self.agent_service.deliver_active_agent_unread(
            agent_id=session.active_agent_id,
            notifier=notifier,
            chat_id=chat_id,
            limit=10,
        )

        before = await self.cursor_client.get_conversation(session.active_agent_id)
        existing_ids = {message.id for message in before.messages}
        await self.cursor_client.add_followup(session.active_agent_id, text)

        deadline = asyncio.get_running_loop().time() + self.settings.followup_poll_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.settings.followup_poll_interval_seconds)
            snapshot = await self.agent_service.get_unread_snapshot(session.active_agent_id)
            new_messages = [
                message
                for message in snapshot.unread_messages
                if message.id not in existing_ids
            ]
            if not new_messages:
                continue

            for message in new_messages[:10]:
                await notifier.send_text(
                    chat_id,
                    build_active_agent_message(snapshot.agent, message.text),
                )
            await self.state_repo.mark_messages_delivered(
                session.active_agent_id,
                [message.id for message in new_messages[:10]],
            )
            return len(new_messages[:10])

        return 0
