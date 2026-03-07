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
    delivered_count: int = 0


class AgentStopError(RuntimeError):
    pass


class AgentService:
    def __init__(self, cursor_client: CursorApiClient, state_repo: StateRepository) -> None:
        self.cursor_client = cursor_client
        self.state_repo = state_repo

    async def list_agents_with_unread_counts(
        self,
        telegram_user_id: int,
    ) -> list[AgentListItem]:
        session = await self.state_repo.get_session(telegram_user_id)
        agents = await self._list_agents({"RUNNING", "FINISHED"})
        snapshots = await asyncio.gather(
            *(self.get_unread_snapshot(agent.id) for agent in agents),
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

    async def clear_unread(self, telegram_user_id: int) -> str | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        agent_id = session.active_agent_id
        conversation = await self.cursor_client.get_conversation(agent_id)
        total = sum(1 for m in conversation.messages if m.type == "assistant_message")
        await self.state_repo.set_delivery_cursor(agent_id, total)
        agent = await self.cursor_client.get_agent(agent_id)
        return agent.name or agent_id

    async def stop_active_agent(self, telegram_user_id: int) -> Agent | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None

        agent = await self.cursor_client.get_agent(session.active_agent_id)
        if agent.status != "RUNNING":
            raise AgentStopError(
                f"{agent.name or agent.id} is not running. Use /agents to select a running agent."
            )

        await self.cursor_client.stop_agent(agent.id)
        await self.state_repo.set_active_agent(telegram_user_id, None)
        await self.state_repo.clear_notice_state(agent.id)
        return agent

    async def ensure_active_agent_exists(self, telegram_user_id: int) -> Agent | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        return await self.cursor_client.get_agent(session.active_agent_id)

    async def switch_active_agent(
        self,
        telegram_user_id: int,
        chat_id: int,
        agent_id: str,
    ) -> Agent:
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
        cursor = snapshot.delivered_count
        for message in to_send:
            await notifier.send_text(
                chat_id,
                build_active_agent_message(snapshot.agent, message.text),
            )
            cursor += 1
            await self.state_repo.set_delivery_cursor(agent_id, cursor)
        return len(to_send)

    async def get_unread_snapshot(self, agent_id: str) -> AgentConversationSnapshot:
        agent = await self.cursor_client.get_agent(agent_id)
        conversation = await self.cursor_client.get_conversation(agent_id)
        assistant_messages = [
            message for message in conversation.messages if message.type == "assistant_message"
        ]
        cursor = await self.state_repo.get_delivery_cursor(agent_id)
        if cursor is None:
            await self.state_repo.set_delivery_cursor(agent_id, len(assistant_messages))
            return AgentConversationSnapshot(
                agent=agent,
                unread_messages=[],
                delivered_count=len(assistant_messages),
            )
        unread_messages = assistant_messages[cursor:]
        return AgentConversationSnapshot(
            agent=agent,
            unread_messages=unread_messages,
            delivered_count=cursor,
        )

    async def list_running_snapshots(self) -> list[AgentConversationSnapshot]:
        agents = await self._list_agents({"RUNNING"})
        return await asyncio.gather(
            *(self.get_unread_snapshot(agent.id) for agent in agents)
        )

    async def _list_agents(self, statuses: set[str]) -> list[Agent]:
        agents = await self.cursor_client.list_agents()
        return [agent for agent in agents if agent.status in statuses]
