from __future__ import annotations

from pathlib import Path

import aiosqlite

CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_session (
    telegram_user_id INTEGER PRIMARY KEY,
    telegram_chat_id INTEGER,
    active_agent_id TEXT,
    unselected_agent_unread_mode TEXT NOT NULL DEFAULT 'count',
    wizard_state TEXT NOT NULL DEFAULT 'idle',
    wizard_payload_json TEXT NOT NULL DEFAULT '{}',
    last_create_agent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

CREATE_MESSAGE_DELIVERY_TABLE = """
CREATE TABLE IF NOT EXISTS agent_message_delivery (
    agent_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, message_id)
)
"""

CREATE_NOTICE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_notice_state (
    agent_id TEXT PRIMARY KEY,
    last_notified_unread_count INTEGER NOT NULL DEFAULT 0,
    last_notified_message_id TEXT,
    updated_at TEXT NOT NULL
)
"""

CREATE_DELIVERY_CURSOR_TABLE = """
CREATE TABLE IF NOT EXISTS agent_delivery_cursor (
    agent_id TEXT PRIMARY KEY,
    delivered_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
)
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_SESSION_TABLE)
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="unselected_agent_unread_mode",
                definition="TEXT NOT NULL DEFAULT 'count'",
            )
            await db.execute(CREATE_MESSAGE_DELIVERY_TABLE)
            await db.execute(CREATE_NOTICE_STATE_TABLE)
            await db.execute(CREATE_DELIVERY_CURSOR_TABLE)
            await db.commit()

    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        return db


async def _ensure_column(
    db: aiosqlite.Connection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    existing_columns = {row[1] for row in rows}
    if column_name in existing_columns:
        return
    await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
