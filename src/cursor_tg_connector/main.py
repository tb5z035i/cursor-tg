from __future__ import annotations

import asyncio
import logging

from cursor_tg_connector.config import Settings
from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.persistence_db import Database
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.services_create_agent_service import CreateAgentService
from cursor_tg_connector.services_followup_service import FollowupService
from cursor_tg_connector.services_polling_service import PollingService
from cursor_tg_connector.telegram_bot_app import build_application
from cursor_tg_connector.telegram_bot_common import AppServices
from cursor_tg_connector.utils_logging import configure_logging

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings()
    configure_logging(settings.log_level)

    database = Database(settings.sqlite_path)
    await database.initialize()
    state_repo = StateRepository(database)

    cursor_client = CursorApiClient(
        api_key=settings.cursor_api_key,
        base_url=settings.cursor_api_base_url,
    )
    api_key_info = await cursor_client.validate_api_key()
    logger.info("Validated Cursor API key: %s", api_key_info.api_key_name)

    agent_service = AgentService(cursor_client, state_repo)
    create_agent_service = CreateAgentService(cursor_client, state_repo)
    followup_service = FollowupService(
        settings=settings,
        cursor_client=cursor_client,
        state_repo=state_repo,
        agent_service=agent_service,
    )
    polling_service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=agent_service,
    )
    app_services = AppServices(
        settings=settings,
        agent_service=agent_service,
        create_agent_service=create_agent_service,
        followup_service=followup_service,
        polling_service=polling_service,
    )

    application = build_application(app_services)
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Telegram polling started")
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await cursor_client.aclose()


def main() -> None:
    asyncio.run(run())
