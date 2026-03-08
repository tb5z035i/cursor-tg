from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.cursor_api_models import Agent, ConversationMessage
from cursor_tg_connector.domain_types import AgentListItem
from cursor_tg_connector.persistence_state_repo import DeliveryCursorState, StateRepository
from cursor_tg_connector.utils_formatting import (
    build_active_agent_message,
    build_agent_label,
    shorten_repository_name,
)


@dataclass(slots=True)
class AgentConversationSnapshot:
    agent: Agent
    unread_messages: list[ConversationMessage | PendingConversationMessage]
    delivered_count: int = 0


@dataclass(slots=True)
class PendingConversationMessage:
    id: str
    text: str
    delivered_count_after: int
    delivered_text_length_after: int


class Notifier(Protocol):
    async def send_text(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: object | None = None,
    ) -> None: ...


async def deliver_snapshot_messages(
    *,
    snapshot: AgentConversationSnapshot,
    state_repo: StateRepository,
    notifier: Notifier,
    chat_id: int,
    message_thread_id: int | None = None,
    limit: int = 10,
    reply_markup_first: object | None = None,
) -> int:
    delivered = 0
    for index, message in enumerate(snapshot.unread_messages[:limit]):
        send_kwargs: dict[str, object] = {"message_thread_id": message_thread_id}
        if reply_markup_first is not None and index == 0:
            send_kwargs["reply_markup"] = reply_markup_first
        await notifier.send_text(
            chat_id,
            build_active_agent_message(snapshot.agent, message.text),
            **send_kwargs,
        )
        delivered += 1
        delivered_count_after = getattr(
            message,
            "delivered_count_after",
            snapshot.delivered_count + delivered,
        )
        delivered_text_length_after = getattr(
            message,
            "delivered_text_length_after",
            len(message.text),
        )
        await state_repo.set_delivery_state(
            snapshot.agent.id,
            delivered_count_after,
            last_message_id=message.id,
            last_message_text_length=delivered_text_length_after,
        )
    return delivered


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
            agent = snapshot.agent
            unread_count = len(snapshot.unread_messages)
            items.append(
                AgentListItem(
                    agent_id=agent.id,
                    name=agent.name.strip() or agent.id,
                    status=agent.status,
                    repository=shorten_repository_name(agent.source.repository),
                    branch=agent.source.ref or "unknown-branch",
                    label=build_agent_label(agent, unread_count),
                    unread_count=unread_count,
                    is_active=(
                        False
                        if session.thread_mode_enabled
                        else agent.id == session.active_agent_id
                    ),
                )
            )
        return items

    async def clear_unread(self, telegram_user_id: int) -> str | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        return await self.clear_unread_for_agent(session.active_agent_id)

    async def clear_unread_for_agent(self, agent_id: str) -> str:
        conversation = await self.cursor_client.get_conversation(agent_id)
        total = sum(1 for m in conversation.messages if m.type == "assistant_message")
        assistant_messages = [
            message for message in conversation.messages if message.type == "assistant_message"
        ]
        last_message = assistant_messages[-1] if assistant_messages else None
        await self.state_repo.set_delivery_state(
            agent_id,
            total,
            last_message_id=last_message.id if last_message else None,
            last_message_text_length=len(last_message.text) if last_message else 0,
        )
        agent = await self.cursor_client.get_agent(agent_id)
        return agent.name or agent_id

    async def get_recent_history(
        self,
        agent_id: str,
        limit: int,
    ) -> tuple[Agent, list[ConversationMessage], int]:
        agent = await self.cursor_client.get_agent(agent_id)
        conversation = await self.cursor_client.get_conversation(agent_id)
        assistant_total = sum(
            1 for message in conversation.messages if message.type == "assistant_message"
        )
        return agent, conversation.messages[-limit:], assistant_total

    async def mark_history_delivered(self, agent_id: str, assistant_total: int) -> None:
        await self.state_repo.set_delivery_cursor(agent_id, assistant_total)
        await self.state_repo.clear_notice_state(agent_id)

    async def stop_active_agent(self, telegram_user_id: int) -> Agent | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        agent = await self.stop_agent_by_id(session.active_agent_id)
        await self.state_repo.set_active_agent(telegram_user_id, None)
        return agent

    async def stop_agent_by_id(self, agent_id: str) -> Agent:
        agent = await self.cursor_client.get_agent(agent_id)
        if agent.status != "RUNNING":
            raise AgentStopError(
                f"{agent.name or agent.id} is not running. Use /focus to select it, "
                "or /agents in threaded mode."
            )

        await self.cursor_client.stop_agent(agent.id)
        await self.state_repo.clear_notice_state(agent.id)
        return agent

    async def ensure_active_agent_exists(self, telegram_user_id: int) -> Agent | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.active_agent_id:
            return None
        return await self.cursor_client.get_agent(session.active_agent_id)

    async def clear_active_agent(self, telegram_user_id: int) -> bool:
        session = await self.state_repo.get_session(telegram_user_id)
        if session.active_agent_id is None:
            return False
        await self.state_repo.set_active_agent(telegram_user_id, None)
        return True

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
        message_thread_id: int | None = None,
        limit: int = 10,
    ) -> int:
        snapshot = await self.get_unread_snapshot(agent_id)
        return await deliver_snapshot_messages(
            snapshot=snapshot,
            state_repo=self.state_repo,
            notifier=notifier,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            limit=limit,
        )

    async def resolve_context_agent_id(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        message_thread_id: int | None,
    ) -> str | None:
        session = await self.state_repo.get_session(telegram_user_id)
        if not session.thread_mode_enabled:
            return session.active_agent_id
        if message_thread_id is None:
            return None
        binding = await self.state_repo.get_thread_binding(chat_id, message_thread_id)
        return binding.agent_id if binding else None

    async def get_unread_snapshot(self, agent_id: str) -> AgentConversationSnapshot:
        agent = await self.cursor_client.get_agent(agent_id)
        conversation = await self.cursor_client.get_conversation(agent_id)
        assistant_messages = [
            message for message in conversation.messages if message.type == "assistant_message"
        ]
        state = await self.state_repo.get_delivery_state(agent_id)
        if state is None:
            last_message = assistant_messages[-1] if assistant_messages else None
            await self.state_repo.set_delivery_state(
                agent_id,
                len(assistant_messages),
                last_message_id=last_message.id if last_message else None,
                last_message_text_length=len(last_message.text) if last_message else 0,
            )
            return AgentConversationSnapshot(
                agent=agent,
                unread_messages=[],
                delivered_count=len(assistant_messages),
            )

        delivered_count = min(state.delivered_count, len(assistant_messages))
        if delivered_count > 0:
            state = await self._ensure_last_message_state(
                agent_id,
                state,
                assistant_messages[delivered_count - 1],
                delivered_count,
            )

        unread_messages: list[PendingConversationMessage] = []
        if (
            delivered_count > 0
            and state.last_message_id == assistant_messages[delivered_count - 1].id
            and len(assistant_messages[delivered_count - 1].text) > state.last_message_text_length
        ):
            current_last = assistant_messages[delivered_count - 1]
            unread_messages.append(
                PendingConversationMessage(
                    id=current_last.id,
                    text=current_last.text[state.last_message_text_length :],
                    delivered_count_after=delivered_count,
                    delivered_text_length_after=len(current_last.text),
                )
            )

        for index, message in enumerate(
            assistant_messages[delivered_count:],
            start=delivered_count + 1,
        ):
            unread_messages.append(
                PendingConversationMessage(
                    id=message.id,
                    text=message.text,
                    delivered_count_after=index,
                    delivered_text_length_after=len(message.text),
                )
            )
        return AgentConversationSnapshot(
            agent=agent,
            unread_messages=unread_messages,
            delivered_count=delivered_count,
        )

    async def list_running_snapshots(self) -> list[AgentConversationSnapshot]:
        agents = await self._list_agents({"RUNNING"})
        return await asyncio.gather(
            *(self.get_unread_snapshot(agent.id) for agent in agents)
        )

    async def _list_agents(self, statuses: set[str]) -> list[Agent]:
        agents = await self.cursor_client.list_agents()
        return [agent for agent in agents if agent.status in statuses]

    async def _ensure_last_message_state(
        self,
        agent_id: str,
        state: DeliveryCursorState,
        current_last_message: ConversationMessage,
        delivered_count: int,
    ) -> DeliveryCursorState:
        if (
            state.last_message_id == current_last_message.id
            and state.last_message_text_length <= len(current_last_message.text)
        ):
            return state

        updated_state = DeliveryCursorState(
            agent_id=agent_id,
            delivered_count=delivered_count,
            last_message_id=current_last_message.id,
            last_message_text_length=len(current_last_message.text),
        )
        await self.state_repo.set_delivery_state(
            agent_id,
            delivered_count,
            last_message_id=current_last_message.id,
            last_message_text_length=len(current_last_message.text),
        )
        return updated_state
