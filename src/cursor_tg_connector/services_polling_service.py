from __future__ import annotations

import asyncio
import logging

from cursor_tg_connector.config import Settings
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.services_agent_service import (
    AgentConversationSnapshot,
    AgentService,
)
from cursor_tg_connector.utils_formatting import build_active_agent_message, build_agent_notice

logger = logging.getLogger(__name__)


class PollingService:
    def __init__(
        self,
        *,
        settings: Settings,
        state_repo: StateRepository,
        agent_service: AgentService,
        active_followups: set[str] | None = None,
    ) -> None:
        self.settings = settings
        self.state_repo = state_repo
        self.agent_service = agent_service
        self.active_followups: set[str] = (
            active_followups if active_followups is not None else set()
        )
        self._lock = asyncio.Lock()

    async def poll_once(self, notifier) -> None:
        if self._lock.locked():
            logger.info("Skipping poll because previous poll is still running")
            return

        async with self._lock:
            session = await self.state_repo.get_session(self.settings.telegram_allowed_user_id)
            chat_id = self.settings.resolve_chat_id(session.telegram_chat_id)
            if chat_id is None:
                logger.info("Skipping poll because chat_id is not known yet")
                return

            snapshots = await self.agent_service.list_running_snapshots()
            active_agent_id = session.active_agent_id
            seen_agent_ids = {snapshot.agent.id for snapshot in snapshots}

            for snapshot in snapshots:
                if snapshot.agent.id in self.active_followups:
                    continue
                if snapshot.agent.id == active_agent_id:
                    await self._handle_active_snapshot(snapshot, notifier, chat_id)
                else:
                    await self._handle_inactive_snapshot(snapshot, notifier, chat_id)

            if active_agent_id and active_agent_id not in self.active_followups:
                active = next(
                    (s for s in snapshots if s.agent.id == active_agent_id), None
                )
                if active and active.agent.status == "RUNNING":
                    await notifier.send_typing(chat_id)

            await self._clear_stale_notice_states(seen_agent_ids)

    async def _handle_active_snapshot(
        self,
        snapshot: AgentConversationSnapshot,
        notifier,
        chat_id: int,
    ) -> None:
        cursor = snapshot.delivered_count
        for message in snapshot.unread_messages[:10]:
            await notifier.send_text(
                chat_id,
                build_active_agent_message(snapshot.agent, message.text),
            )
            cursor += 1
            await self.state_repo.set_delivery_cursor(snapshot.agent.id, cursor)

        if not snapshot.unread_messages:
            await self.state_repo.clear_notice_state(snapshot.agent.id)

    async def _handle_inactive_snapshot(
        self,
        snapshot: AgentConversationSnapshot,
        notifier,
        chat_id: int,
    ) -> None:
        if not snapshot.unread_messages:
            await self.state_repo.clear_notice_state(snapshot.agent.id)
            return

        notice_state = await self.state_repo.get_notice_state(snapshot.agent.id)
        unread_count = len(snapshot.unread_messages)
        if notice_state.last_notified_unread_count == unread_count:
            return

        await notifier.send_text(chat_id, build_agent_notice(snapshot.agent, unread_count))
        await self.state_repo.update_notice_state(
            snapshot.agent.id,
            unread_count,
            None,
        )

    async def _clear_stale_notice_states(self, seen_agent_ids: set[str]) -> None:
        session = await self.state_repo.get_session(self.settings.telegram_allowed_user_id)
        if session.active_agent_id and session.active_agent_id not in seen_agent_ids:
            await self.state_repo.clear_notice_state(session.active_agent_id)
