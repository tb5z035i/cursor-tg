from __future__ import annotations

from collections import deque

import pytest

from cursor_tg_connector.config import Settings
from cursor_tg_connector.cursor_api_models import Agent, ConversationMessage
from cursor_tg_connector.domain_types import WizardStep
from cursor_tg_connector.services_agent_service import AgentConversationSnapshot
from cursor_tg_connector.services_followup_service import FollowupService


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, chat_id: int, text: str) -> None:
        self.messages.append(f"{chat_id}:{text}")


class FakeCursorClient:
    def __init__(self) -> None:
        self.followups: list[tuple[str, str]] = []

    async def add_followup(self, agent_id: str, prompt_text: str) -> str:
        self.followups.append((agent_id, prompt_text))
        return agent_id


class FakeAgentService:
    def __init__(self) -> None:
        self.deliver_calls: list[str] = []
        self.snapshots = deque(
            [
                AgentConversationSnapshot(
                    agent=self._agent(),
                    unread_messages=[],
                    delivered_count=1,
                ),
                AgentConversationSnapshot(
                    agent=self._agent(),
                    unread_messages=[
                        ConversationMessage.model_validate(
                            {
                                "id": "new-assistant",
                                "type": "assistant_message",
                                "text": "New result",
                            }
                        ),
                    ],
                    delivered_count=1,
                ),
            ]
        )

    async def deliver_active_agent_unread(
        self,
        *,
        agent_id: str,
        notifier,
        chat_id: int,
        limit: int,
    ) -> int:
        self.deliver_calls.append(agent_id)
        return 1

    async def get_unread_snapshot(self, agent_id: str) -> AgentConversationSnapshot:
        assert agent_id == "agent-1"
        return self.snapshots.popleft()

    @staticmethod
    def _agent() -> Agent:
        return Agent.model_validate(
            {
                "id": "agent-1",
                "name": "Active Agent",
                "status": "RUNNING",
                "source": {"repository": "https://github.com/acme/repo", "ref": "main"},
                "target": {"url": "https://cursor.com/agent-1", "branchName": "cursor/test"},
                "createdAt": "2024-01-01T00:00:00Z",
            }
        )


@pytest.mark.asyncio
async def test_followup_service_only_relays_new_messages(settings: Settings, state_repo) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-1"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    notifier = FakeNotifier()
    service = FollowupService(
        settings=settings,
        cursor_client=FakeCursorClient(),
        state_repo=state_repo,
        agent_service=FakeAgentService(),
    )

    delivered_count = await service.send_followup(1234, 5678, "Please continue", notifier)

    assert delivered_count == 1
    assert notifier.messages == ["5678:> **Active Agent**\nNew result"]


@pytest.mark.asyncio
async def test_followup_service_registers_active_followup(settings: Settings, state_repo) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-1"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    active_followups: set[str] = set()
    notifier = FakeNotifier()
    service = FollowupService(
        settings=settings,
        cursor_client=FakeCursorClient(),
        state_repo=state_repo,
        agent_service=FakeAgentService(),
        active_followups=active_followups,
    )

    await service.send_followup(1234, 5678, "Please continue", notifier)

    assert "agent-1" not in active_followups


@pytest.mark.asyncio
async def test_followup_service_clears_flag_on_error(settings: Settings, state_repo) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-1"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    active_followups: set[str] = set()

    class FailingAgentService(FakeAgentService):
        async def deliver_active_agent_unread(self, **kwargs) -> int:
            raise RuntimeError("boom")

    notifier = FakeNotifier()
    service = FollowupService(
        settings=settings,
        cursor_client=FakeCursorClient(),
        state_repo=state_repo,
        agent_service=FailingAgentService(),
        active_followups=active_followups,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await service.send_followup(1234, 5678, "Please continue", notifier)

    assert "agent-1" not in active_followups
