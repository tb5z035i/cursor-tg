from __future__ import annotations

import asyncio
from dataclasses import dataclass

from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.cursor_api_models import Agent, ConversationMessage
from cursor_tg_connector.domain_types import AgentListItem
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.utils_formatting import build_active_agent_message, build_agent_label


@dataclass(slots=True)
class AgentConversationSnapshot:
    agent: Agent
    unread_messages: list[ConversationMessage]


class AgentService:
    def __init__(self, cursor_client: CursorApiClient, state_repo: StateRepository) -> None:
        self.cursor_client = cursor_client
        self.state_repo = state_repo

    async def list_running_agents_with_unread_counts(self, telegram_user_id: int) -> list[AgentListItem]:
        session = await self.state_repo.get_session(telegram_user_id)
        running_agents = await self._list_running_agents()
        snapshots = await asyncio.gather(
            *(self.get_unread_snapshot(agent.id) for agent in running_agents),
        )

        items: list[AgentListItem] = []
        for snapshot in snapshots:
            items.append(
                AgentListItem(
                    agent_id=snapshot.agent.id,
                    label=build_agent_label(snapshot.agent, len(snapshot.unread_messages)),
                    unread_count=len(snapshot.unread_messages),
                    is_active=snapshot.agent.id == session.active_agent_id,
                )
            )
        return items

    async def ensure_active_agent_exists(self, telegram_user_id: int) -> Agent | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        return await self.cursor_client.get_agent(session.active_agent_id)

    async def switch_active_agent(self, telegram_user_id: int, chat_id: int, agent_id: str) -> Agent:
        agent = await self.cursor_client.get_agent(agent_id)
        await self.state_repo.update_chat_context(telegram_user_id, chat_id)
        await self.state_repo.set_active_agent(telegram_user_id, agent_id)
        return agent

    async def deliver_active_agent_unread(
        self,
        *,
        agent_id: str,
        notifier,
        chat_id: int,
        limit: int = 10,
    ) -> int:
        snapshot = await self.get_unread_snapshot(agent_id)
        to_send = snapshot.unread_messages[:limit]
        for message in to_send:
            await notifier.send_text(chat_id, build_active_agent_message(snapshot.agent, message.text))
        await self.state_repo.mark_messages_delivered(agent_id, [message.id for message in to_send])
        return len(to_send)

    async def get_unread_snapshot(self, agent_id: str) -> AgentConversationSnapshot:
        agent = await self.cursor_client.get_agent(agent_id)
        conversation = await self.cursor_client.get_conversation(agent_id)
        assistant_messages = [
            message for message in conversation.messages if message.type == "assistant_message"
        ]
        delivered_ids = await self.state_repo.get_delivered_message_ids(
            agent_id,
            [message.id for message in assistant_messages],
        )
        unread_messages = [message for message in assistant_messages if message.id not in delivered_ids]
        return AgentConversationSnapshot(agent=agent, unread_messages=unread_messages)

    async def list_running_snapshots(self) -> list[AgentConversationSnapshot]:
        running_agents = await self._list_running_agents()
        return await asyncio.gather(*(self.get_unread_snapshot(agent.id) for agent in running_agents))

    async def _list_running_agents(self) -> list[Agent]:
        agents = await self.cursor_client.list_agents()
        return [agent for agent in agents if agent.status == "RUNNING"]
