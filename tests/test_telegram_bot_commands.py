from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.domain_types import AgentListItem
from cursor_tg_connector.telegram_bot_commands import agents_command


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, object]]] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, dict(kwargs)))


class FakeStateRepo:
    def __init__(self) -> None:
        self.chat_context_updates: list[tuple[int, int]] = []

    async def update_chat_context(self, telegram_user_id: int, chat_id: int) -> None:
        self.chat_context_updates.append((telegram_user_id, chat_id))


class FakeCreateAgentService:
    def __init__(self, state_repo: FakeStateRepo) -> None:
        self.state_repo = state_repo


class FakeAgentService:
    def __init__(self, items: list[AgentListItem]) -> None:
        self.items = items

    async def list_agents_with_unread_counts(
        self,
        telegram_user_id: int,
    ) -> list[AgentListItem]:
        assert telegram_user_id == 1234
        return self.items


def build_context(
    settings,
    items: list[AgentListItem],
    state_repo: FakeStateRepo,
) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        agent_service=FakeAgentService(items),
        create_agent_service=FakeCreateAgentService(state_repo),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=application)


@pytest.mark.asyncio
async def test_agents_command_renders_status_table_and_status_labels(settings) -> None:
    message = FakeMessage()
    state_repo = FakeStateRepo()
    items = [
        AgentListItem(
            agent_id="agent-1",
            name="Agent One",
            status="RUNNING",
            repository="acme/repo-a",
            branch="main",
            label="Agent One · RUNNING · acme/repo-a · main · unread:2",
            unread_count=2,
            is_active=True,
        ),
        AgentListItem(
            agent_id="agent-2",
            name="Agent Two",
            status="FINISHED",
            repository="acme/repo-b",
            branch="dev",
            label="Agent Two · FINISHED · acme/repo-b · dev · unread:0",
            unread_count=0,
            is_active=False,
        ),
    ]
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await agents_command(update, build_context(settings, items, state_repo))

    assert state_repo.chat_context_updates == [(settings.telegram_allowed_user_id, 999)]
    assert len(message.replies) == 1

    summary, kwargs = message.replies[0]
    assert kwargs["parse_mode"] == "HTML"
    assert "<pre>" in summary
    assert "Status" in summary
    assert "RUNNING" in summary
    assert "FINISHED" in summary
    assert "Tap a button below to switch the active agent." in summary

    keyboard = kwargs["reply_markup"]
    button_labels = [row[0].text for row in keyboard.inline_keyboard]
    assert button_labels == [
        "✅ Agent One · RUNNING · acme/repo-a · main · unread:2",
        "Agent Two · FINISHED · acme/repo-b · dev · unread:0",
    ]
