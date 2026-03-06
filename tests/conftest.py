from __future__ import annotations

from pathlib import Path

import pytest

from cursor_tg_connector.config import Settings
from cursor_tg_connector.persistence_db import Database
from cursor_tg_connector.persistence_state_repo import StateRepository


@pytest.fixture
async def state_repo(tmp_path: Path) -> StateRepository:
    database = Database(tmp_path / "connector.db")
    await database.initialize()
    return StateRepository(database)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_ID": 1234,
            "CURSOR_API_KEY": "cursor-key",
            "SQLITE_PATH": str(tmp_path / "connector.db"),
            "POLL_INTERVAL_SECONDS": 1,
            "FOLLOWUP_POLL_INTERVAL_SECONDS": 0.01,
            "FOLLOWUP_POLL_TIMEOUT_SECONDS": 0.05,
        }
    )
