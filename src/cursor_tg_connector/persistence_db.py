from __future__ import annotations

from pathlib import Path

import aiosqlite

CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_session (
    telegram_user_id INTEGER PRIMARY KEY,
    telegram_chat_id INTEGER,
    active_agent_id TEXT,
    thread_mode_enabled INTEGER NOT NULL DEFAULT 0,
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
            await db.execute(CREATE_MESSAGE_DELIVERY_TABLE)
            await db.execute(CREATE_NOTICE_STATE_TABLE)
            await db.execute(CREATE_DELIVERY_CURSOR_TABLE)
            await db.execute(CREATE_AGENT_THREAD_BINDING_TABLE)
            await self._ensure_session_columns(db)
            await db.commit()

    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        return db

    async def reset(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_SESSION_TABLE)
            await db.execute(CREATE_MESSAGE_DELIVERY_TABLE)
            await db.execute(CREATE_NOTICE_STATE_TABLE)
            await db.execute(CREATE_DELIVERY_CURSOR_TABLE)
            await db.execute(CREATE_AGENT_THREAD_BINDING_TABLE)
            await self._ensure_session_columns(db)
            for table_name in MANAGED_TABLES:
                await db.execute(f"DELETE FROM {table_name}")
            await db.commit()
        await self.initialize()

    async def _ensure_session_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(telegram_session)")
        columns = {
            row[1]
            for row in await cursor.fetchall()
        }
        if "thread_mode_enabled" not in columns:
            await db.execute(
                """
                ALTER TABLE telegram_session
                ADD COLUMN thread_mode_enabled INTEGER NOT NULL DEFAULT 0
                """
            )
