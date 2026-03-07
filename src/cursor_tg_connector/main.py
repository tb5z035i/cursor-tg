from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

from cursor_tg_connector.config import Settings
from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.persistence_db import Database
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.services_agent_service import AgentService
from cursor_tg_connector.services_create_agent_service import CreateAgentService
from cursor_tg_connector.services_followup_service import FollowupService
from cursor_tg_connector.services_polling_service import PollingService
from cursor_tg_connector.telegram_bot_app import build_application, register_commands
from cursor_tg_connector.telegram_bot_common import AppServices
from cursor_tg_connector.utils_logging import configure_logging

logger = logging.getLogger(__name__)


def _resolve_env_file() -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=None)
    args, _ = parser.parse_known_args()
    return args.env_file or os.environ.get("ENV_FILE")


async def run() -> None:
    env_file = _resolve_env_file()
    settings = Settings(_env_file=env_file) if env_file else Settings()
    configure_logging(settings.log_level)

    database = Database(settings.sqlite_path)
    await database.initialize()
    state_repo = StateRepository(database)

    cursor_client = CursorApiClient(
        api_key=settings.cursor_api_key,
        base_url=settings.cursor_api_base_url,
        max_retries=settings.cursor_api_max_retries,
        retry_backoff_seconds=settings.cursor_api_retry_backoff_seconds,
    )
    api_key_info = await cursor_client.validate_api_key()
    logger.info("Validated Cursor API key: %s", api_key_info.api_key_name)

    agent_service = AgentService(cursor_client, state_repo)
    create_agent_service = CreateAgentService(cursor_client, state_repo)
    active_followups: set[str] = set()
    followup_service = FollowupService(
        settings=settings,
        cursor_client=cursor_client,
        state_repo=state_repo,
        agent_service=agent_service,
        active_followups=active_followups,
    )
    polling_service = PollingService(
        settings=settings,
        state_repo=state_repo,
        agent_service=agent_service,
        active_followups=active_followups,
    )
    app_services = AppServices(
        settings=settings,
        agent_service=agent_service,
        create_agent_service=create_agent_service,
        followup_service=followup_service,
        polling_service=polling_service,
    )

    application = build_application(app_services)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    installed_signals = _install_signal_handlers(loop, stop_event)
    updater_started = False
    application_started = False
    application_initialized = False
    try:
        await application.initialize()
        application_initialized = True
        await register_commands(application)
        await application.start()
        application_started = True
        if application.updater is None:
            raise RuntimeError("Telegram updater is not available")
        await application.updater.start_polling()
        updater_started = True
        logger.info("Telegram polling started")
        await stop_event.wait()
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        if application.updater is not None and updater_started:
            await application.updater.stop()
        if application_started:
            await application.stop()
        if application_initialized:
            await application.shutdown()
        await cursor_client.aclose()


def main() -> None:
    asyncio.run(run())


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    stop_event: asyncio.Event,
) -> list[signal.Signals]:
    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed.append(sig)
        except NotImplementedError:
            logger.warning("Signal handlers are not available on this platform")
            break
    return installed
