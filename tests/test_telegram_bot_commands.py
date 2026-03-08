from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.error import TelegramError

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.domain_types import AgentListItem, AgentThreadBinding
from cursor_tg_connector.github_api_models import GitHubMergeResult, GitHubPullRequest
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.telegram_bot_commands import (
    agents_command,
    close_command,
    current_command,
    help_command,
    history_command,
    merge_command,
    new_agent_command,
    pr_command,
    ready_command,
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
                "target": {
                    "url": "https://cursor.com/agent-1",
                    "branchName": "cursor/a",
                    "prUrl": "https://github.com/acme/repo-a/pull/123",
                },
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


class FakePullRequestService:
    def __init__(self, *, enabled: bool = True, state: str = "open", draft: bool = True) -> None:
        self.enabled = enabled
        self.state = state
        self.draft = draft
        self.ready_calls: list[str] = []
        self.merge_calls: list[tuple[str, str]] = []
        self.pull_request = self._build_pull_request(state=state, draft=draft, merged=False)

    async def get_pull_request(self, agent: Agent) -> GitHubPullRequest:
        return self.pull_request

    async def mark_ready_for_review(self, agent: Agent) -> GitHubPullRequest:
        self.ready_calls.append(agent.id)
        self.draft = False
        self.pull_request = self._build_pull_request(state="open", draft=False, merged=False)
        return self.pull_request

    async def merge_pull_request(self, agent: Agent, *, merge_method: str) -> GitHubMergeResult:
        self.merge_calls.append((agent.id, merge_method))
        self.pull_request = self._build_pull_request(state="closed", draft=False, merged=True)
        return GitHubMergeResult.model_validate(
            {"merged": True, "message": "Pull Request successfully merged", "sha": "abc123"}
        )

    def _build_pull_request(
        self,
        *,
        state: str,
        draft: bool,
        merged: bool,
    ) -> GitHubPullRequest:
        return GitHubPullRequest.model_validate(
            {
                "number": 123,
                "title": "Improve bot PR actions",
                "state": state,
                "draft": draft,
                "merged": merged,
                "html_url": "https://github.com/acme/repo-a/pull/123",
                "mergeable": True,
                "mergeable_state": "clean",
                "head": {"ref": "cursor/a"},
                "base": {"ref": "main"},
            }
        )


class FakeThreadModeBot:
    def __init__(
        self,
        *,
        chat_type: str = "supergroup",
        is_forum: bool = True,
        users_can_create_threads: bool = False,
        bot_status: str = "administrator",
        bot_can_manage_topics: bool = True,
        close_error: Exception | None = None,
    ) -> None:
        self.chat_type = chat_type
        self.is_forum = is_forum
        self.users_can_create_threads = users_can_create_threads
        self.bot_status = bot_status
        self.bot_can_manage_topics = bot_can_manage_topics
        self.close_error = close_error
        self.closed_topics: list[tuple[int, int]] = []

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

    async def close_forum_topic(self, chat_id: int, message_thread_id: int) -> bool:
        self.closed_topics.append((chat_id, message_thread_id))
        if self.close_error is not None:
            raise self.close_error
        return True

    async def send_message(self, **kwargs: object) -> None:
        return None


def build_context(
    *,
    settings,
    state_repo,
    agent_service: object,
    create_agent_service: FakeCreateAgentService | None = None,
    pull_request_service: object | None = None,
    args: list[str] | None = None,
    bot: object | None = None,
) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        database=state_repo.database,
        agent_service=agent_service,
        create_agent_service=create_agent_service or FakeCreateAgentService(state_repo),
    )
    if pull_request_service is not None:
        services.pull_request_service = pull_request_service
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(
        application=application,
        args=args or [],
        bot=bot or FakeThreadModeBot(),
    )


@pytest.mark.asyncio
async def test_help_command_includes_project_github_url(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await help_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=FakeListAgentService([]),
        ),
    )

    assert len(message.replies) == 1
    assert "GitHub: https://github.com/tb5z035i/cursor-tg" in message.replies[0][0]


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
    text, kwargs = message.replies[-1]
    assert "Thread mode is now enabled." in text
    assert "Thread mode is enabled." in text
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_threadmode_command_shows_clickable_options_by_default(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await threadmode_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=AgentService(FakeCursorClient(), state_repo),
        ),
    )

    text, kwargs = message.replies[-1]
    assert "Thread mode is disabled." in text
    assert "Choose the routing mode below." in text
    markup = kwargs["reply_markup"]
    assert markup is not None
    assert [button.text for button in markup.inline_keyboard[0]] == ["On", "✓ Off"]


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
    text, kwargs = message.replies[-1]
    assert "Thread mode can only be enabled in a Telegram supergroup with Topics turned on." in text
    assert kwargs["reply_markup"] is not None


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
    text, kwargs = message.replies[-1]
    assert (
        'Thread mode requires the Telegram chat setting "Disallow users to create new '
        'threads" to be enabled.'
        in text
    )
    assert kwargs["reply_markup"] is not None


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
    text, kwargs = message.replies[-1]
    assert (
        "Thread mode requires the bot to have the Telegram Manage Topics "
        "administrator permission."
        in text
    )
    assert kwargs["reply_markup"] is not None


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
async def test_close_command_closes_bound_thread_and_removes_binding(
    settings,
    state_repo,
) -> None:
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
    await state_repo.update_notice_state("agent-1", unread_count=2, last_message_id=None)
    agent_service = AgentService(FakeCursorClient(), state_repo)
    bot = FakeThreadModeBot()

    await close_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            bot=bot,
        ),
    )

    assert [text for text, _ in message.replies] == [
        "Closing this Telegram thread. Use /agents in the root chat to create a new one later."
    ]
    assert bot.closed_topics == [(999, 77)]
    assert await state_repo.get_agent_thread_binding("agent-1") is None
    notice_state = await state_repo.get_notice_state("agent-1")
    assert notice_state.last_notified_unread_count == 0


@pytest.mark.asyncio
async def test_close_command_in_thread_mode_outside_thread_shows_guidance(
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

    await close_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    assert [text for text, _ in message.replies] == [
        "This command only works inside a bound agent thread while thread mode is enabled. "
        "Use /agents in the root chat to create or open the correct thread."
    ]


@pytest.mark.asyncio
async def test_close_command_reports_telegram_failure_and_keeps_binding(
    settings,
    state_repo,
) -> None:
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
    bot = FakeThreadModeBot(close_error=TelegramError("boom"))

    await close_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            bot=bot,
        ),
    )

    assert [text for text, _ in message.replies] == [
        "Closing this Telegram thread. Use /agents in the root chat to create a new one later.",
        "Couldn't close this Telegram thread: boom",
    ]
    assert await state_repo.get_agent_thread_binding("agent-1") == AgentThreadBinding(
        agent_id="agent-1",
        telegram_chat_id=999,
        message_thread_id=77,
    )


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


@pytest.mark.asyncio
async def test_current_command_adds_pr_status_and_buttons_when_github_enabled(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await current_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            pull_request_service=FakePullRequestService(),
        ),
    )

    text, kwargs = message.replies[0]
    assert "PR status: draft" in text
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_pr_command_escapes_pr_title_when_rendering_html(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    pull_request_service = FakePullRequestService()
    pull_request_service.pull_request = pull_request_service._build_pull_request(
        state="open",
        draft=True,
        merged=False,
    ).model_copy(
        update={"title": "Fix <b>bold</b> & [link](https://example.com)"}
    )
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await pr_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            pull_request_service=pull_request_service,
        ),
    )

    text, kwargs = message.replies[0]
    assert kwargs["parse_mode"] == "HTML"
    assert "PR title: Fix &lt;b&gt;bold&lt;/b&gt; &amp; [link](https://example.com)" in text


@pytest.mark.asyncio
async def test_pr_command_explains_when_github_actions_are_not_configured(
    settings,
    state_repo,
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await pr_command(
        update,
        build_context(settings=settings, state_repo=state_repo, agent_service=agent_service),
    )

    assert "Set GITHUB_TOKEN (or GITHUB_PAT)" in message.replies[0][0]


@pytest.mark.asyncio
async def test_ready_command_marks_pull_request_ready_for_review(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    pull_request_service = FakePullRequestService()
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await ready_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            pull_request_service=pull_request_service,
        ),
    )

    assert pull_request_service.ready_calls == ["agent-1"]
    assert message.replies[0][0] == (
        "Marked PR #123 ready for review.\nhttps://github.com/acme/repo-a/pull/123"
    )


@pytest.mark.asyncio
async def test_merge_command_accepts_explicit_merge_method(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    client = FakeCursorClient()
    agent_service = AgentService(client, state_repo)
    pull_request_service = FakePullRequestService(draft=False)
    await state_repo.set_active_agent(settings.telegram_allowed_user_id, "agent-1")

    await merge_command(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            agent_service=agent_service,
            pull_request_service=pull_request_service,
            args=["rebase"],
        ),
    )

    assert pull_request_service.merge_calls == [("agent-1", "rebase")]
    assert message.replies[0][0] == (
        "Merged https://github.com/acme/repo-a/pull/123 using rebase.\n"
        "Pull Request successfully merged"
    )
