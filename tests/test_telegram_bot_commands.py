from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.domain_types import AgentListItem, AgentThreadBinding
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.telegram_bot_commands import (
    agents_command,
    history_command,
    new_agent_command,
    resetdb_command,
    stop_command,
    threadmode_command,
)
from cursor_tg_connector.utils_formatting import build_reset_db_prompt


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, object]]] = []
        self.message_thread_id: int | None = None

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, dict(kwargs)))


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> None:
        self.messages.append(dict(kwargs))


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
        return AgentConversation.model_validate(
            {
                "id": agent_id,
                "messages": [
                    {"id": "m1", "type": "assistant_message", "text": "Earlier result"},
                    {
                        "id": "m2",
                        "type": "user_message",
                        "text": "Please inspect `bug`",
                    },
                    {
                        "id": "m3",
                        "type": "assistant_message",
                        "text": "I found **two** issues",
                    },
                    {"id": "m4", "type": "user_message", "text": "Fix it"},
                ],
            }
        )

    async def stop_agent(self, agent_id: str) -> str:
        self.stopped_agent_ids.append(agent_id)
        return agent_id

    async def list_agents(self) -> list[Agent]:
        return [self.agent]


class FakeCreateAgentService:
    def __init__(self, state_repo) -> None:
        self.state_repo = state_repo
        self.started: list[tuple[int, int]] = []

    async def start_wizard(self, telegram_user_id: int, chat_id: int) -> None:
        self.started.append((telegram_user_id, chat_id))

    async def get_model_page(self, telegram_user_id: int, page: int):
        return SimpleNamespace(repositories=["gpt-5"], page=page, total_pages=1)

    async def cancel(self, telegram_user_id: int) -> bool:
        return False


class FakeListAgentService:
    def __init__(self, items: list[AgentListItem]) -> None:
        self.items = items

    async def list_agents_with_unread_counts(self, telegram_user_id: int) -> list[AgentListItem]:
        assert telegram_user_id == 1234
        return self.items


def build_context(
    *,
    settings,
    state_repo,
    agent_service: object,
    create_agent_service: FakeCreateAgentService | None = None,
    args: list[str] | None = None,
    bot: FakeBot | None = None,
) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        database=state_repo.database,
        agent_service=agent_service,
        create_agent_service=create_agent_service or FakeCreateAgentService(state_repo),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=application, args=args or [], bot=bot or FakeBot())


@pytest.mark.asyncio
async def test_agents_command_renders_status_table_without_clickable_options(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
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

    await agents_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=FakeListAgentService(items),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.telegram_chat_id == 999
    assert len(message.replies) == 1

    summary, kwargs = message.replies[0]
    assert kwargs == {"parse_mode": "HTML"}
    assert "<pre>" in summary
    assert "Status" in summary
    assert "RUNNING" in summary
    assert "FINISHED" in summary
    assert "Use /focus to switch the active agent." in summary


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

    assert [text for text, _ in message.replies] == [
        "No active agent selected.\n\n"
        "Use /focus to pick a running agent, then send /stop to stop it."
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
    assert [text for text, _ in message.replies] == ["Stopped Agent One."]
    assert client.stopped_agent_ids == ["agent-1"]
    assert session.active_agent_id is None


@pytest.mark.asyncio
async def test_threadmode_command_toggles_session_flag(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)

    await threadmode_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            args=["on"],
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.thread_mode_enabled is True
    assert "Thread mode is enabled." in message.replies[-1][0]


@pytest.mark.asyncio
async def test_history_command_replays_recent_messages_and_marks_history_delivered(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")
    await state_repo.set_delivery_cursor("agent-1", 0)

    await history_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            args=["3"],
            bot=bot,
        ),
    )

    assert message.replies == []
    assert len(bot.messages) == 3
    assert "You" in str(bot.messages[0]["text"])
    assert "<code>bug</code>" in str(bot.messages[0]["text"])
    assert "Agent One" in str(bot.messages[1]["text"])
    assert "<b>two</b>" in str(bot.messages[1]["text"])
    assert "Fix it" in str(bot.messages[2]["text"])
    assert await state_repo.get_delivery_cursor("agent-1") == 2


@pytest.mark.asyncio
async def test_history_command_in_thread_mode_sends_into_bound_thread(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    message.message_thread_id = 77
    bot = FakeBot()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.thread_mode_enabled = True
    await state_repo.upsert_session(session)
    await state_repo.upsert_agent_thread_binding(
        AgentThreadBinding(
            agent_id="agent-1",
            telegram_chat_id=999,
            message_thread_id=77,
        )
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)

    await history_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            args=["2"],
            bot=bot,
        ),
    )

    assert message.replies == []
    assert len(bot.messages) == 2
    assert [entry["message_thread_id"] for entry in bot.messages] == [77, 77]
    assert "Agent One" in str(bot.messages[0]["text"])
    assert "Fix it" in str(bot.messages[1]["text"])


@pytest.mark.asyncio
async def test_history_command_requires_positive_integer_count(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await history_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=AgentService(FakeCursorClient(), state_repo),
            args=["zero"],
        ),
    )

    assert [text for text, _ in message.replies] == [
        "Usage: /history <count> (count must be a positive integer)"
    ]


@pytest.mark.asyncio
async def test_resetdb_command_shows_confirmation_keyboard(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)

    await resetdb_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    assert message.replies[0][0] == build_reset_db_prompt()
    assert message.replies[0][1]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_stop_command_in_thread_mode_outside_thread_shows_guidance(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.thread_mode_enabled = True
    await state_repo.upsert_session(session)
    agent_service = AgentService(FakeCursorClient(), state_repo)

    await stop_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    assert [text for text, _ in message.replies] == [
        "This command only works inside a bound agent thread while thread mode is enabled. "
        "Use /agents in the root chat to create or open the correct thread."
    ]


@pytest.mark.asyncio
async def test_history_command_in_thread_mode_outside_thread_shows_guidance(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.thread_mode_enabled = True
    await state_repo.upsert_session(session)

    await history_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=AgentService(FakeCursorClient(), state_repo),
            args=["2"],
        ),
    )

    assert [text for text, _ in message.replies] == [
        "This command only works inside a bound agent thread while thread mode is enabled. "
        "Use /agents in the root chat to create or open the correct thread."
    ]


@pytest.mark.asyncio
async def test_newagent_command_rejects_bound_thread_in_thread_mode(settings, state_repo) -> None:
    message = FakeMessage()
    message.message_thread_id = 77
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.thread_mode_enabled = True
    await state_repo.upsert_session(session)
    await state_repo.upsert_agent_thread_binding(
        AgentThreadBinding(
            agent_id="agent-1",
            telegram_chat_id=999,
            message_thread_id=77,
        )
    )
    agent_service = AgentService(FakeCursorClient(), state_repo)
    create_agent_service = FakeCreateAgentService(state_repo)

    await new_agent_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            create_agent_service=create_agent_service,
        ),
    )

    assert [text for text, _ in message.replies] == [
        "Run /newagent from the root chat, not from inside an agent thread."
    ]
    assert create_agent_service.started == []
