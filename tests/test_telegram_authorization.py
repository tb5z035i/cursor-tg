from __future__ import annotations

from types import SimpleNamespace

import pytest

from cursor_tg_connector.domain_types import UnselectedAgentUnreadMode
from cursor_tg_connector.telegram_bot_callbacks import callback_router
from cursor_tg_connector.telegram_bot_commands import start_command, unread_command
from cursor_tg_connector.telegram_bot_messages import message_handler


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_: object) -> None:
        self.replies.append(text)


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
async def test_unread_command_reports_current_default_mode(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await unread_command(update, build_unread_context(settings, state_repo, []))

    assert message.replies == [
        "Current setting: unread count notices.\n\n"
        "Usage: /unread <full|count|none>\n"
        "• full — deliver unread messages from unselected agents in full.\n"
        "• count — send only unread-count notices (default).\n"
        "• none — send nothing until you switch to that agent."
    ]


@pytest.mark.asyncio
async def test_unread_command_updates_mode(settings, state_repo) -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=settings.telegram_allowed_user_id),
        effective_message=message,
        effective_chat=SimpleNamespace(id=999),
    )

    await unread_command(update, build_unread_context(settings, state_repo, ["full"]))

    session = await state_repo.get_session(settings.telegram_allowed_user_id)
    assert session.unselected_agent_unread_mode == UnselectedAgentUnreadMode.FULL
    assert message.replies == [
        "Unread handling for unselected agents is now set to full text delivery."
    ]
