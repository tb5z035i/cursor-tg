from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.domain_types import AgentListItem, UnselectedAgentUnreadMode
from cursor_tg_connector.telegram_bot_callbacks import callback_router
from cursor_tg_connector.telegram_bot_commands import (
    configure_unread_command,
    focus_command,
    start_command,
)
from cursor_tg_connector.telegram_bot_messages import message_handler


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []
        self.reply_markups: list[object | None] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeCallbackQuery:
    def __init__(self, data: str = "noop") -> None:
        self.data = data
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_text(self, *_: object, **__: object) -> None:
        raise AssertionError("Unauthorized callback should not edit messages")

    async def edit_message_reply_markup(self, *_: object, **__: object) -> None:
        raise AssertionError("Unauthorized callback should not edit markup")


def build_context(settings) -> SimpleNamespace:
    services = SimpleNamespace(settings=settings)
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(application=application, bot=SimpleNamespace())


def build_unread_context(settings, state_repo, args: list[str]) -> SimpleNamespace:
    services = SimpleNamespace(
        settings=settings,
        create_agent_service=SimpleNamespace(state_repo=state_repo),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(
        application=application,
        bot=SimpleNamespace(),
        args=args,
    )


def build_focus_context(settings, items: list[AgentListItem]) -> SimpleNamespace:
    class FakeAgentService:
        async def list_agents_with_unread_counts(
            self,
            telegram_user_id: int,
        ) -> list[AgentListItem]:
            assert telegram_user_id == settings.telegram_allowed_user_id
            return items

    services = SimpleNamespace(
        settings=settings,
        create_agent_service=SimpleNamespace(
            state_repo=SimpleNamespace(update_chat_context=_async_noop)
        ),
        agent_service=FakeAgentService(),
    )
    application = SimpleNamespace(bot_data={"services": services})
    return SimpleNamespace(
        application=application,
        bot=SimpleNamespace(),
        args=[],
    )


async def _async_noop(*_: object, **__: object) -> None:
    return None


@pytest.mark.asyncio
async def test_start_command_ignores_unauthorized_user(settings) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id + 1),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await start_command(update, build_context(settings))

    assert message.replies == []


@pytest.mark.asyncio
async def test_message_handler_ignores_unauthorized_user(settings) -> None:
    message = FakeMessage("hello")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id + 1),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await message_handler(update, build_context(settings))

    assert message.replies == []


@pytest.mark.asyncio
async def test_callback_router_rejects_unauthorized_user(settings) -> None:
    query = FakeCallbackQuery()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id + 1),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )

    await callback_router(update, build_context(settings))

    assert query.answers == [("Unauthorized", True)]


@pytest.mark.asyncio
async def test_configure_unread_command_reports_current_default_mode(
    settings, state_repo
) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await configure_unread_command(update, build_unread_context(settings, state_repo, []))

    assert message.replies == [
        "Current setting: unread count notices.\n\n"
        "Usage: /configure_unread <full|count|none>\n"
        "• full — deliver unread messages from unselected agents in full.\n"
        "• count — send only unread-count notices (default).\n"
        "• none — send nothing until you switch to that agent."
    ]


@pytest.mark.asyncio
async def test_configure_unread_command_updates_mode(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await configure_unread_command(
        update,
        build_unread_context(settings, state_repo, ["full"]),
    )

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.unselected_agent_unread_mode == UnselectedAgentUnreadMode.FULL
    assert message.replies == [
        "Unread handling for unselected agents is now set to full text delivery."
    ]


@pytest.mark.asyncio
async def test_focus_command_shows_clickable_agent_options_only(settings) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )
    items = [
        AgentListItem(
            agent_id="agent-1",
            label="Agent One · acme/repo · main · unread:0",
            unread_count=0,
            is_active=True,
        ),
        AgentListItem(
            agent_id="agent-2",
            label="Agent Two · acme/repo · main · unread:2",
            unread_count=2,
            is_active=False,
        ),
    ]

    await focus_command(update, build_focus_context(settings, items))

    assert message.replies == ["Select an agent:"]
    markup = message.reply_markups[0]
    assert markup is not None
    assert markup.inline_keyboard[0][0].text.startswith("✅ ")
    assert markup.inline_keyboard[0][0].callback_data == "agent:switch:agent-1"
    assert markup.inline_keyboard[1][0].callback_data == "agent:switch:agent-2"
