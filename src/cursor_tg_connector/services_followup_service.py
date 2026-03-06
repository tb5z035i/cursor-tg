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
        active_followups: set[str] | None = None,
    ) -> None:
        self.settings = settings
        self.cursor_client = cursor_client
        self.state_repo = state_repo
        self.agent_service = agent_service
        self.active_followups: set[str] = (
            active_followups if active_followups is not None else set()
        )

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

        agent_id = session.active_agent_id
        self.active_followups.add(agent_id)
        try:
            await self.agent_service.deliver_active_agent_unread(
                agent_id=agent_id,
                notifier=notifier,
                chat_id=chat_id,
                limit=10,
            )

            before = await self.cursor_client.get_conversation(agent_id)
            before_assistant_count = sum(
                1 for m in before.messages if m.type == "assistant_message"
            )
            await self.cursor_client.add_followup(agent_id, text)

            timeout = self.settings.followup_poll_timeout_seconds
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(self.settings.followup_poll_interval_seconds)
                snapshot = await self.agent_service.get_unread_snapshot(agent_id)
                new_messages = snapshot.unread_messages[
                    max(0, before_assistant_count - snapshot.delivered_count) :
                ]
                if not new_messages:
                    continue

                cursor = snapshot.delivered_count + (
                    len(snapshot.unread_messages) - len(new_messages)
                )
                delivered = new_messages[:10]
                for message in delivered:
                    await notifier.send_text(
                        chat_id,
                        build_active_agent_message(snapshot.agent, message.text),
                    )
                    cursor += 1
                    await self.state_repo.set_delivery_cursor(agent_id, cursor)
                return len(delivered)

            return 0
        finally:
            self.active_followups.discard(agent_id)
