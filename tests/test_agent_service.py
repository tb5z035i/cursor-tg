from __future__ import annotations

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.services_agent_service import AgentService, AgentStopError


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
                    "status": "FINISHED",
                    "source": {"repository": "https://github.com/acme/repo-b", "ref": "dev"},
                    "target": {"url": "https://cursor.com/agent-2", "branchName": "cursor/b"},
                    "createdAt": "2024-01-01T00:00:00Z",
                }
            ),
        ]
        self.stopped_agent_ids: list[str] = []

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

    async def stop_agent(self, agent_id: str) -> str:
        self.stopped_agent_ids.append(agent_id)
        return agent_id


@pytest.mark.asyncio
async def test_list_agents_includes_unread_counts(state_repo) -> None:
    client = FakeCursorClient()
    service = AgentService(client, state_repo)

    session = await state_repo.get_session(1234)
    session.active_agent_id = "agent-2"
    await state_repo.upsert_session(session)
    await state_repo.set_delivery_cursor("agent-1", 1)
    await state_repo.set_delivery_cursor("agent-2", 0)

    items = await service.list_agents_with_unread_counts(1234)

    item_by_id = {item.agent_id: item for item in items}
    assert item_by_id["agent-1"].unread_count == 1
    assert item_by_id["agent-2"].unread_count == 1
    assert item_by_id["agent-2"].is_active is True


@pytest.mark.asyncio
async def test_stop_active_agent_clears_active_selection(state_repo) -> None:
    client = FakeCursorClient()
    service = AgentService(client, state_repo)

    await state_repo.set_active_agent(1234, "agent-1")
    await state_repo.update_notice_state("agent-1", unread_count=2, last_message_id="m2")

    agent = await service.stop_active_agent(1234)
    session = await state_repo.get_session(1234)
    notice_state = await state_repo.get_notice_state("agent-1")

    assert agent is not None
    assert agent.id == "agent-1"
    assert client.stopped_agent_ids == ["agent-1"]
    assert session.active_agent_id is None
    assert notice_state.last_notified_unread_count == 0
    assert notice_state.last_notified_message_id is None


@pytest.mark.asyncio
async def test_stop_active_agent_requires_running_agent(state_repo) -> None:
    client = FakeCursorClient()
    service = AgentService(client, state_repo)

    await state_repo.set_active_agent(1234, "agent-2")

    with pytest.raises(AgentStopError, match="Agent Two is not running"):
        await service.stop_active_agent(1234)

    session = await state_repo.get_session(1234)
    assert session.active_agent_id == "agent-2"
    assert client.stopped_agent_ids == []
