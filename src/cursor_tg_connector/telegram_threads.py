from __future__ import annotations

from telegram import Bot

from cursor_tg_connector.cursor_api_models import Agent
from cursor_tg_connector.domain_types import AgentThreadBinding
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.utils_formatting import build_agent_thread_name


async def ensure_agent_thread(
    *,
    bot: Bot,
    state_repo: StateRepository,
    agent: Agent,
    chat_id: int,
) -> tuple[AgentThreadBinding, bool]:
    existing = await state_repo.get_agent_thread_binding(agent.id)
    if existing is not None and existing.telegram_chat_id == chat_id:
        return existing, False

    topic = await bot.create_forum_topic(
        chat_id=chat_id,
        name=build_agent_thread_name(agent),
    )
    binding = AgentThreadBinding(
        agent_id=agent.id,
        telegram_chat_id=chat_id,
        message_thread_id=topic.message_thread_id,
    )
    await state_repo.upsert_agent_thread_binding(binding)
    return binding, True
