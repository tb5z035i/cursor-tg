from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest
from telegram import InlineKeyboardMarkup

from cursor_tg_connector.config import Settings
from cursor_tg_connector.cursor_api_models import Agent, ConversationMessage
from cursor_tg_connector.domain_types import (
    AgentThreadBinding,
    UnselectedAgentUnreadMode,
    WizardStep,
)
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.services_agent_service import AgentConversationSnapshot, AgentService
from cursor_tg_connector.services_polling_service import PollingService


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[int, int | None, str, InlineKeyboardMarkup | None]] = []

    async def send_text(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self.messages.append((chat_id, message_thread_id, text, reply_markup))

    async def send_typing(self, chat_id: int, message_thread_id: int | None = None) -> None:
        pass


class FakeAgentService:
    def __init__(self, poll_batches: list[list[AgentConversationSnapshot]]) -> None:
        self.poll_batches = deque(poll_batches)

    async def list_running_snapshots(self) -> list[AgentConversationSnapshot]:
        if len(self.poll_batches) > 1:
            return self.poll_batches.popleft()
        return self.poll_batches[0]


class MutableCursorClient:
    def __init__(self, agent: Agent, messages: list[dict[str, str]]) -> None:
        self.agent = agent
        self.messages = messages

    async def list_agents(self) -> list[Agent]:
        return [self.agent]

    async def get_agent(self, agent_id: str) -> Agent:
        assert agent_id == self.agent.id
        return self.agent

    async def get_conversation(self, agent_id: str):
        assert agent_id == self.agent.id
        return SimpleNamespace(
            messages=[make_message(item["id"], item["text"]) for item in self.messages]
        )


def make_agent(agent_id: str, name: str) -> Agent:
    return Agent.model_validate(
        {
            "id": agent_id,
            "name": name,
            "status": "RUNNING",
            "source": {"repository": "https://github.com/acme/repo", "ref": "main"},
            "target": {"url": f"https://cursor.com/{agent_id}", "branchName": "cursor/test"},
            "createdAt": "2024-01-01T00:00:00Z",
        }
    )


def make_message(message_id: str, text: str) -> ConversationMessage:
    return ConversationMessage.model_validate(
        {
            "id": message_id,
            "type": "assistant_message",
            "text": text,
        }
    )


@pytest.mark.asyncio
async def test_polling_service_sends_active_contents_and_inactive_notice(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    active_snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[
            make_message("msg-1", "first response"),
            make_message("msg-2", "second response"),
        ],
    )
    inactive_snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("msg-3", "hidden response")],
    )
    no_unread_active_snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService(
            [
                [active_snapshot, inactive_snapshot],
                [no_unread_active_snapshot, inactive_snapshot],
            ]
        ),
    )

    await service.poll_once(notifier)
    await service.poll_once(notifier)

    texts = [text for _, _, text, _ in notifier.messages]
    assert texts.count("> **Active Agent**\nfirst response") == 1
    assert texts.count("> **Active Agent**\nsecond response") == 1
    assert (
        texts.count(
            "> **Other Agent**\n1 unread message(s). Tap below or use /focus to switch."
        )
        == 1
    )
    notice_markup = next(
        markup for _, _, text, markup in notifier.messages if "1 unread message(s)." in text
    )
    assert notice_markup is not None
    assert notice_markup.inline_keyboard[0][0].callback_data == "agent:switch:agent-other"


@pytest.mark.asyncio
async def test_polling_service_caps_active_agent_delivery_to_ten(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[make_message(f"msg-{index}", f"text {index}") for index in range(12)],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    assert len(notifier.messages) == 10


@pytest.mark.asyncio
async def test_polling_service_skips_agent_with_active_followup(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[make_message("msg-1", "response")],
    )
    notifier = FakeNotifier()
    active_followups: set[str] = {"agent-active"}
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
        active_followups=active_followups,
    )

    await service.poll_once(notifier)

    assert len(notifier.messages) == 0


@pytest.mark.asyncio
async def test_polling_service_skips_notifications_during_create_wizard(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.WAITING_PROMPT
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[make_message("msg-1", "response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    assert notifier.messages == []


@pytest.mark.asyncio
async def test_inactive_notice_dedup_ignores_unstable_message_ids(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    snapshot_poll1 = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("uuid-aaa", "response")],
    )
    snapshot_poll2 = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("uuid-zzz", "response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot_poll1], [snapshot_poll2]]),
    )

    await service.poll_once(notifier)
    await service.poll_once(notifier)

    notice_texts = [text for _, _, text, _ in notifier.messages if "unread" in text]
    assert len(notice_texts) == 1


@pytest.mark.asyncio
async def test_polling_service_advances_delivery_cursor(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[
            make_message("msg-1", "first"),
            make_message("msg-2", "second"),
        ],
        delivered_count=3,
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    cursor = await state_repo.get_delivery_cursor("agent-active")
    assert cursor == 5


@pytest.mark.asyncio
async def test_polling_service_can_deliver_full_text_for_inactive_agents(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.unselected_agent_unread_mode = UnselectedAgentUnreadMode.FULL
    await state_repo.upsert_session(session)

    inactive_snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[
            make_message("msg-1", "first hidden response"),
            make_message("msg-2", "second hidden response"),
        ],
        delivered_count=4,
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[inactive_snapshot]]),
    )

    await service.poll_once(notifier)

    assert [text for _, _, text, _ in notifier.messages] == [
        "> **Other Agent**\nfirst hidden response",
        "> **Other Agent**\nsecond hidden response",
    ]
    first_markup = notifier.messages[0][3]
    second_markup = notifier.messages[1][3]
    assert first_markup is not None
    assert first_markup.inline_keyboard[0][0].callback_data == "agent:switch:agent-other"
    assert second_markup is None
    cursor = await state_repo.get_delivery_cursor("agent-other")
    assert cursor == 6


@pytest.mark.asyncio
async def test_polling_service_can_hide_inactive_agent_notifications(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.unselected_agent_unread_mode = UnselectedAgentUnreadMode.NONE
    await state_repo.upsert_session(session)

    inactive_snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("msg-1", "hidden response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[inactive_snapshot]]),
    )

    await service.poll_once(notifier)

    assert notifier.messages == []
    assert await state_repo.get_delivery_cursor("agent-other") is None


@pytest.mark.asyncio
async def test_polling_service_routes_bound_agent_messages_to_thread(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.thread_mode_enabled = True
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)
    await state_repo.upsert_agent_thread_binding(
        AgentThreadBinding(
            agent_id="agent-active",
            telegram_chat_id=5678,
            message_thread_id=77,
        )
    )

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-active", "Active Agent"),
        unread_messages=[make_message("msg-1", "threaded response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    assert notifier.messages == [(5678, 77, "> **Active Agent**\nthreaded response", None)]


@pytest.mark.asyncio
async def test_polling_service_thread_mode_honors_count_policy_for_unbound_agents(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.thread_mode_enabled = True
    session.unselected_agent_unread_mode = UnselectedAgentUnreadMode.COUNT
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("msg-3", "hidden response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    assert notifier.messages[0][0:3] == (
        5678,
        None,
        "> **Other Agent**\n1 unread message(s). Tap below or use /agents to create or "
        "open its thread.",
    )
    assert notifier.messages[0][3] is not None


@pytest.mark.asyncio
async def test_polling_service_thread_mode_honors_full_policy_for_unbound_agents(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.thread_mode_enabled = True
    session.unselected_agent_unread_mode = UnselectedAgentUnreadMode.FULL
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)

    snapshot = AgentConversationSnapshot(
        agent=make_agent("agent-other", "Other Agent"),
        unread_messages=[make_message("msg-3", "hidden response")],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=FakeAgentService([[snapshot]]),
    )

    await service.poll_once(notifier)

    assert notifier.messages[0][0:3] == (
        5678,
        None,
        "> **Other Agent**\nhidden response",
    )
    assert notifier.messages[0][3] is not None


@pytest.mark.asyncio
async def test_polling_service_relays_appended_text_when_last_message_grows(
    settings: Settings,
    state_repo: StateRepository,
) -> None:
    session = await state_repo.get_session(1234)
    session.telegram_chat_id = 5678
    session.active_agent_id = "agent-active"
    session.wizard_state = WizardStep.IDLE
    await state_repo.upsert_session(session)
    await state_repo.set_delivery_cursor("agent-active", 0)

    agent = make_agent("agent-active", "Active Agent")
    client = MutableCursorClient(
        agent,
        [{"id": "msg-1", "text": "hello"}],
    )
    notifier = FakeNotifier()
    service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=AgentService(client, state_repo),
    )

    await service.poll_once(notifier)
    client.messages = [{"id": "msg-1", "text": "hello world"}]
    await service.poll_once(notifier)

    assert [text for _, _, text, _ in notifier.messages] == [
        "> **Active Agent**\nhello",
        "> **Active Agent**\n world",
    ]
