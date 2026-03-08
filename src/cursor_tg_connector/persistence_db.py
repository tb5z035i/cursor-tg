from __future__ import annotations

from pathlib import Path

import aiosqlite

CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_session (
    telegram_user_id INTEGER PRIMARY KEY,
    telegram_chat_id INTEGER,
    active_agent_id TEXT,
    thread_mode_enabled INTEGER NOT NULL DEFAULT 0,
    thread_mode_configured INTEGER NOT NULL DEFAULT 0,
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

CREATE_AGENT_THREAD_BINDING_TABLE = """
CREATE TABLE IF NOT EXISTS agent_thread_binding (
    agent_id TEXT PRIMARY KEY,
    telegram_chat_id INTEGER NOT NULL,
    message_thread_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (telegram_chat_id, message_thread_id)
)
"""

MANAGED_TABLES = (
    "agent_thread_binding",
    "agent_delivery_cursor",
    "agent_notice_state",
    "agent_message_delivery",
    "telegram_session",
)


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
                column_name="thread_mode_enabled",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="thread_mode_configured",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="unselected_agent_unread_mode",
                definition="TEXT NOT NULL DEFAULT 'count'",
            )
            await db.execute(CREATE_MESSAGE_DELIVERY_TABLE)
            await db.execute(CREATE_NOTICE_STATE_TABLE)
            await db.execute(CREATE_DELIVERY_CURSOR_TABLE)
            await _ensure_column(
                db,
                table_name="agent_delivery_cursor",
                column_name="last_message_id",
                definition="TEXT",
            )
            await _ensure_column(
                db,
                table_name="agent_delivery_cursor",
                column_name="last_message_text_length",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await db.execute(CREATE_AGENT_THREAD_BINDING_TABLE)
            await db.commit()

    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        return db

    async def reset(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_SESSION_TABLE)
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="thread_mode_enabled",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="thread_mode_configured",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await _ensure_column(
                db,
                table_name="telegram_session",
                column_name="unselected_agent_unread_mode",
                definition="TEXT NOT NULL DEFAULT 'count'",
            )
            await db.execute(CREATE_MESSAGE_DELIVERY_TABLE)
            await db.execute(CREATE_NOTICE_STATE_TABLE)
            await db.execute(CREATE_DELIVERY_CURSOR_TABLE)
            await _ensure_column(
                db,
                table_name="agent_delivery_cursor",
                column_name="last_message_id",
                definition="TEXT",
            )
            await _ensure_column(
                db,
                table_name="agent_delivery_cursor",
                column_name="last_message_text_length",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            await db.execute(CREATE_AGENT_THREAD_BINDING_TABLE)
            for table_name in MANAGED_TABLES:
                await db.execute(f"DELETE FROM {table_name}")
            await db.commit()
        await self.initialize()


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
