from __future__ import annotations

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.services_agent_service import AgentService


class FakeCursorClient:
    def __init__(self) -> None:
        self.agents = [
            Agent.model_validate(
                {
                    "id": "agent-1",
                    "name": "Agent One",
                    "status": "RUNNING",
                    "source": {"repository": "https://github.com/acme/repo-a", "ref": "main"},
                    "target": {"url": "https://cursor.com/agent-1", "branchName": "cursor/a"},
                    "createdAt": "2024-01-01T00:00:00Z",
                }
            ),
            Agent.model_validate(
                {
                    "id": "agent-2",
                    "name": "Agent Two",
                    "status": "RUNNING",
                    "source": {"repository": "https://github.com/acme/repo-b", "ref": "dev"},
                    "target": {"url": "https://cursor.com/agent-2", "branchName": "cursor/b"},
                    "createdAt": "2024-01-01T00:00:00Z",
                }
            ),
        ]

    async def list_agents(self) -> list[Agent]:
        return self.agents

    async def get_agent(self, agent_id: str) -> Agent:
        return next(agent for agent in self.agents if agent.id == agent_id)

    async def get_conversation(self, agent_id: str) -> AgentConversation:
        if agent_id == "agent-1":
            messages = [
                {"id": "m1", "type": "assistant_message", "text": "hello"},
                {"id": "m2", "type": "assistant_message", "text": "world"},
            ]
        else:
            messages = [{"id": "m3", "type": "assistant_message", "text": "other"}]
        return AgentConversation.model_validate({"id": agent_id, "messages": messages})


@pytest.mark.asyncio
async def test_list_running_agents_includes_unread_counts(state_repo) -> None:
    client = FakeCursorClient()
    service = AgentService(client, state_repo)

    session = await state_repo.get_session(1234)
    session.active_agent_id = "agent-2"
    await state_repo.upsert_session(session)
    await state_repo.mark_messages_delivered("agent-1", ["m1"])

    items = await service.list_running_agents_with_unread_counts(1234)

    item_by_id = {item.agent_id: item for item in items}
    assert item_by_id["agent-1"].unread_count == 1
    assert item_by_id["agent-2"].unread_count == 1
    assert item_by_id["agent-2"].is_active is True
