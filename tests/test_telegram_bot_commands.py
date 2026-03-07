from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.telegram_bot_commands import stop_command


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_: object) -> None:
        self.replies.append(text)


class FakeCursorClient:
    def __init__(self) -> None:
        self.stopped_agent_ids: list[str] = []
        self.agent = Agent.model_validate(
            {
                "id": "agent-1",
                "name": "Agent One",
                "status": "RUNNING",
                "source": {"repository": "https://github.com/acme/repo-a", "ref": "main"},
                "target": {"url": "https://cursor.com/agent-1", "branchName": "cursor/a"},
                "createdAt": "2024-01-01T00:00:00Z",
            }
        )

    async def get_agent(self, agent_id: str) -> Agent:
        assert agent_id == self.agent.id
        return self.agent

    async def get_conversation(self, agent_id: str) -> AgentConversation:
        assert agent_id == self.agent.id
        return AgentConversation.model_validate({"id": agent_id, "messages": []})

    async def stop_agent(self, agent_id: str) -> str:
        self.stopped_agent_ids.append(agent_id)
        return agent_id

    async def list_agents(self) -> list[Agent]:
        return [self.agent]


def build_context(*, settings, state_repo, agent_service: AgentService) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        agent_service=agent_service,
        create_agent_service=SimpleNamespace(state_repo=state_repo),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=application)


@pytest.mark.asyncio
async def test_stop_command_shows_help_when_no_active_agent(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)

    await stop_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    assert message.replies == [
        (
            "No active agent selected.\n\n"
            "Use /agents to pick a running agent, then send /stop to stop it."
        )
    ]


@pytest.mark.asyncio
async def test_stop_command_stops_selected_agent(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await stop_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)

    assert message.replies == ["Stopped Agent One."]
    assert client.stopped_agent_ids == ["agent-1"]
    assert session.active_agent_id is None
