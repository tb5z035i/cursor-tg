from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.domain_types import AgentListItem, AgentThreadBinding
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.telegram_bot_commands import (
    agents_command,
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


class FakeThreadModeBot:
    def __init__(
        self,
        *,
        chat_type: str = "supergroup",
        is_forum: bool = True,
        users_can_create_threads: bool = False,
        bot_status: str = "administrator",
        bot_can_manage_topics: bool = True,
    ) -> None:
        self.chat_type = chat_type
        self.is_forum = is_forum
        self.users_can_create_threads = users_can_create_threads
        self.bot_status = bot_status
        self.bot_can_manage_topics = bot_can_manage_topics

    async def get_chat(self, chat_id: int):
        assert chat_id == 999
        return SimpleNamespace(
            id=chat_id,
            type=self.chat_type,
            is_forum=self.is_forum,
            permissions=SimpleNamespace(
                can_manage_topics=self.users_can_create_threads,
            ),
        )

    async def get_me(self):
        return SimpleNamespace(id=4321)

    async def get_chat_member(self, chat_id: int, user_id: int):
        assert chat_id == 999
        assert user_id == 4321
        return SimpleNamespace(
            status=self.bot_status,
            can_manage_topics=self.bot_can_manage_topics,
        )


def build_context(
    *,
    settings,
    state_repo,
    agent_service: object,
    create_agent_service: FakeCreateAgentService | None = None,
    args: list[str] | None = None,
    bot: FakeThreadModeBot | None = None,
) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        database=state_repo.database,
        agent_service=agent_service,
        create_agent_service=create_agent_service or FakeCreateAgentService(state_repo),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(
        application=application,
        args=args or [],
        bot=bot or FakeThreadModeBot(),
    )


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
async def test_threadmode_command_rejects_chat_without_topics(settings, state_repo) -> None:
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
            bot=FakeThreadModeBot(is_forum=False),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.thread_mode_enabled is False
    assert message.replies[-1][0] == (
        "Thread mode can only be enabled in a Telegram supergroup with Topics turned on."
    )


@pytest.mark.asyncio
async def test_threadmode_command_rejects_when_users_can_create_threads(
    settings,
    state_repo,
) -> None:
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
            bot=FakeThreadModeBot(users_can_create_threads=True),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.thread_mode_enabled is False
    assert message.replies[-1][0] == (
        'Thread mode requires the Telegram chat setting "Disallow users to create new '
        'threads" to be enabled.'
    )


@pytest.mark.asyncio
async def test_threadmode_command_rejects_when_bot_cannot_manage_topics(
    settings,
    state_repo,
) -> None:
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
            bot=FakeThreadModeBot(bot_can_manage_topics=False),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.thread_mode_enabled is False
    assert message.replies[-1][0] == (
        "Thread mode requires the bot to have the Telegram Manage Topics "
        "administrator permission."
    )


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
