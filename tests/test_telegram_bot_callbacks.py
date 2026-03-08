from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.cursor_api_models import Agent, AgentConversation
from cursor_tg_connector.domain_types import AgentThreadBinding
from cursor_tg_connector.github_api_models import GitHubMergeResult, GitHubPullRequest
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.telegram_bot_callbacks import callback_router
from cursor_tg_connector.telegram_bot_common import (
    PR_MERGE_PREFIX,
    PR_READY_PREFIX,
    RESET_DB_CANCEL_PREFIX,
    RESET_DB_CONFIRM_PREFIX,
    SWITCH_AGENT_PREFIX,
)


class FakeBot:
    def __init__(self) -> None:
        self.created_topics: list[tuple[int, str]] = []
        self.messages: list[tuple[int, int | None, str]] = []

    async def create_forum_topic(self, chat_id: int, name: str):
        self.created_topics.append((chat_id, name))
        return SimpleNamespace(message_thread_id=88)

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: int | None = None,
        reply_markup=None,
    ) -> None:
        self.messages.append((chat_id, message_thread_id, text))

    async def send_chat_action(self, **kwargs) -> None:
        return None


class FakeCursorClient:
    def __init__(self) -> None:
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
        assert agent_id == "agent-1"
        return self.agent

    async def get_conversation(self, agent_id: str) -> AgentConversation:
        assert agent_id == "agent-1"
        return AgentConversation.model_validate(
            {
                "id": agent_id,
                "messages": [
                    {"id": "m1", "type": "assistant_message", "text": "hello from agent"}
                ],
            }
        )

    async def list_agents(self) -> list[Agent]:
        return [self.agent]


class FakeCallbackQuery:
    def __init__(self, data: str, message_thread_id: int | None = None) -> None:
        self.data = data
        self.answers: list[tuple[str | None, bool]] = []
        self.edits: list[str] = []
        self.message = SimpleNamespace(message_thread_id=message_thread_id)

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text: str, **_: object) -> None:
        self.edits.append(text)

    async def edit_message_reply_markup(self, **_: object) -> None:
        return None


class FakePullRequestService:
    def __init__(self) -> None:
        self.enabled = True
        self.ready_calls: list[str] = []
        self.merge_calls: list[tuple[str, str]] = []
        self.pull_request = self._build_pull_request(state="open", draft=True, merged=False)

    async def get_pull_request(self, agent: Agent) -> GitHubPullRequest:
        return self.pull_request

    async def mark_ready_for_review(self, agent: Agent) -> GitHubPullRequest:
        self.ready_calls.append(agent.id)
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


def build_context(
    *,
    settings,
    state_repo,
    bot: FakeBot,
    agent_service: AgentService,
    pull_request_service: object | None = None,
):
    services = SimpleNamespace(
        settings=settings,
        database=state_repo.database,
        agent_service=agent_service,
        create_agent_service=SimpleNamespace(state_repo=state_repo),
    )
    if pull_request_service is not None:
        services.pull_request_service = pull_request_service
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=application, bot=bot)


@pytest.mark.asyncio
async def test_resetdb_confirm_callback_clears_state(settings, state_repo) -> None:
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.telegram_chat_id = 999
    session.active_agent_id = "agent-1"
    await state_repo.upsert_session(session)
    await state_repo.set_delivery_cursor("agent-1", 5)

    query = FakeCallbackQuery(RESET_DB_CONFIRM_PREFIX)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )

    await callback_router(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            bot=FakeBot(),
            agent_service=AgentService(FakeCursorClient(), state_repo),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.active_agent_id is None
    assert session.telegram_chat_id == 999
    assert await state_repo.get_delivery_cursor("agent-1") is None
    assert query.edits == ["Local DB state reset and reinitialized."]


@pytest.mark.asyncio
async def test_resetdb_cancel_callback_leaves_state_untouched(settings, state_repo) -> None:
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.telegram_chat_id = 999
    session.active_agent_id = "agent-1"
    await state_repo.upsert_session(session)

    query = FakeCallbackQuery(RESET_DB_CANCEL_PREFIX)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )

    await callback_router(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            bot=FakeBot(),
            agent_service=AgentService(FakeCursorClient(), state_repo),
        ),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.active_agent_id == "agent-1"
    assert query.edits == ["DB reset cancelled. No changes were made."]


@pytest.mark.asyncio
async def test_switch_agent_callback_creates_thread_in_thread_mode(settings, state_repo) -> None:
    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    session.telegram_chat_id = 999
    session.thread_mode_enabled = True
    await state_repo.upsert_session(session)
    await state_repo.set_delivery_cursor("agent-1", 0)

    query = FakeCallbackQuery(f"{SWITCH_AGENT_PREFIX}agent-1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )
    bot = FakeBot()

    await callback_router(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            bot=bot,
            agent_service=AgentService(FakeCursorClient(), state_repo),
        ),
    )

    binding = await state_repo.get_agent_thread_binding("agent-1")
    assert binding == AgentThreadBinding(
        agent_id="agent-1",
        telegram_chat_id=999,
        message_thread_id=88,
    )
    assert query.edits == ["Created thread for Agent One. Continue in that thread."]
    assert any(
        message == (999, 88, "Thread ready for <b>Agent One</b>. Send follow-ups here.")
        for message in bot.messages
    )
    assert any(message[1] == 88 and "hello from agent" in message[2] for message in bot.messages)


@pytest.mark.asyncio
async def test_ready_pr_callback_updates_message_with_new_pr_state(settings, state_repo) -> None:
    query = FakeCallbackQuery(f"{PR_READY_PREFIX}agent-1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )
    pull_request_service = FakePullRequestService()

    await callback_router(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            bot=FakeBot(),
            agent_service=AgentService(FakeCursorClient(), state_repo),
            pull_request_service=pull_request_service,
        ),
    )

    assert pull_request_service.ready_calls == ["agent-1"]
    assert query.answers[-1] == ("Marked ready for review", False)
    assert "PR status: ready for review" in query.edits[-1]


@pytest.mark.asyncio
async def test_merge_pr_callback_updates_message_after_merge(settings, state_repo) -> None:
    query = FakeCallbackQuery(f"{PR_MERGE_PREFIX}squash:agent-1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )
    pull_request_service = FakePullRequestService()
    await pull_request_service.mark_ready_for_review(Agent.model_validate(FakeCursorClient().agent))

    await callback_router(
        update,
        build_context(
            settings=settings,
            state_repo=state_repo,
            bot=FakeBot(),
            agent_service=AgentService(FakeCursorClient(), state_repo),
            pull_request_service=pull_request_service,
        ),
    )

    assert pull_request_service.merge_calls == [("agent-1", "squash")]
    assert query.answers[-1] == ("Pull request merged", False)
    assert "PR status: merged" in query.edits[-1]
