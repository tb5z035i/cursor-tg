from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from cursor_tg_connector.domain_types import (
    AgentThreadBinding,
    SessionState,
    UnselectedAgentUnreadMode,
    WizardStep,
)
from cursor_tg_connector.persistence_db import Database


@dataclass(slots=True)
class NoticeState:
    agent_id: str
    last_notified_unread_count: int
    last_notified_message_id: str | None


class StateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_session(self, telegram_user_id: int) -> SessionState:
        db = await self.database.connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM telegram_session WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        if row is None:
            return SessionState(telegram_user_id=telegram_user_id)

        return SessionState(
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            active_agent_id=row["active_agent_id"],
            thread_mode_enabled=bool(row["thread_mode_enabled"]),
            unselected_agent_unread_mode=UnselectedAgentUnreadMode(
                row["unselected_agent_unread_mode"]
            ),
            wizard_state=WizardStep(row["wizard_state"]),
            wizard_payload=json.loads(row["wizard_payload_json"] or "{}"),
            last_create_agent_at=row["last_create_agent_at"],
        )

    async def upsert_session(
        self,
        session: SessionState,
    ) -> None:
        now = _utcnow()
        db = await self.database.connect()
        try:
            await db.execute(
                """
                INSERT INTO telegram_session (
                    telegram_user_id,
                    telegram_chat_id,
                    active_agent_id,
                    thread_mode_enabled,
                    unselected_agent_unread_mode,
                    wizard_state,
                    wizard_payload_json,
                    last_create_agent_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    telegram_chat_id = excluded.telegram_chat_id,
                    active_agent_id = excluded.active_agent_id,
                    thread_mode_enabled = excluded.thread_mode_enabled,
                    unselected_agent_unread_mode = excluded.unselected_agent_unread_mode,
                    wizard_state = excluded.wizard_state,
                    wizard_payload_json = excluded.wizard_payload_json,
                    last_create_agent_at = excluded.last_create_agent_at,
                    updated_at = excluded.updated_at
                """,
                (
                    session.telegram_user_id,
                    session.telegram_chat_id,
                    session.active_agent_id,
                    int(session.thread_mode_enabled),
                    session.unselected_agent_unread_mode.value,
                    session.wizard_state.value,
                    json.dumps(session.wizard_payload),
                    session.last_create_agent_at,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def update_chat_context(self, telegram_user_id: int, chat_id: int) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.telegram_chat_id = chat_id
        await self.upsert_session(session)
        return session

    async def set_active_agent(self, telegram_user_id: int, agent_id: str | None) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.active_agent_id = agent_id
        await self.upsert_session(session)
        return session

    async def set_thread_mode_enabled(
        self,
        telegram_user_id: int,
        enabled: bool,
    ) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.thread_mode_enabled = enabled
        if enabled:
            session.active_agent_id = None
        await self.upsert_session(session)
        return session

    async def set_unselected_agent_unread_mode(
        self,
        telegram_user_id: int,
        mode: UnselectedAgentUnreadMode,
    ) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.unselected_agent_unread_mode = mode
        await self.upsert_session(session)
        return session

    async def set_wizard(
        self,
        telegram_user_id: int,
        step: WizardStep,
        payload: dict,
    ) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.wizard_state = step
        session.wizard_payload = payload
        await self.upsert_session(session)
        return session

    async def clear_wizard(self, telegram_user_id: int) -> SessionState:
        return await self.set_wizard(telegram_user_id, WizardStep.IDLE, {})

    async def set_last_create_agent_at(self, telegram_user_id: int, when: datetime) -> SessionState:
        session = await self.get_session(telegram_user_id)
        session.last_create_agent_at = when.astimezone(UTC).isoformat()
        await self.upsert_session(session)
        return session

    async def get_notice_state(self, agent_id: str) -> NoticeState:
        db = await self.database.connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM agent_notice_state WHERE agent_id = ?",
                (agent_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        if row is None:
            return NoticeState(
                agent_id=agent_id,
                last_notified_unread_count=0,
                last_notified_message_id=None,
            )

        return NoticeState(
            agent_id=row["agent_id"],
            last_notified_unread_count=row["last_notified_unread_count"],
            last_notified_message_id=row["last_notified_message_id"],
        )

    async def update_notice_state(
        self,
        agent_id: str,
        unread_count: int,
        last_message_id: str | None,
    ) -> None:
        now = _utcnow()
        db = await self.database.connect()
        try:
            await db.execute(
                """
                INSERT INTO agent_notice_state (
                    agent_id,
                    last_notified_unread_count,
                    last_notified_message_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_notified_unread_count = excluded.last_notified_unread_count,
                    last_notified_message_id = excluded.last_notified_message_id,
                    updated_at = excluded.updated_at
                """,
                (agent_id, unread_count, last_message_id, now),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_delivery_cursor(self, agent_id: str) -> int | None:
        db = await self.database.connect()
        try:
            cursor = await db.execute(
                "SELECT delivered_count FROM agent_delivery_cursor WHERE agent_id = ?",
                (agent_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        return row["delivered_count"] if row else None

    async def set_delivery_cursor(self, agent_id: str, count: int) -> None:
        now = _utcnow()
        db = await self.database.connect()
        try:
            await db.execute(
                """
                INSERT INTO agent_delivery_cursor (agent_id, delivered_count, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    delivered_count = excluded.delivered_count,
                    updated_at = excluded.updated_at
                """,
                (agent_id, count, now),
            )
            await db.commit()
        finally:
            await db.close()

    async def clear_notice_state(self, agent_id: str) -> None:
        db = await self.database.connect()
        try:
            await db.execute(
                "DELETE FROM agent_notice_state WHERE agent_id = ?",
                (agent_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_agent_thread_binding(self, agent_id: str) -> AgentThreadBinding | None:
        db = await self.database.connect()
        try:
            cursor = await db.execute(
                "SELECT * FROM agent_thread_binding WHERE agent_id = ?",
                (agent_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        return AgentThreadBinding(
            agent_id=row["agent_id"],
            telegram_chat_id=row["telegram_chat_id"],
            message_thread_id=row["message_thread_id"],
        )

    async def get_thread_binding(
        self,
        chat_id: int,
        message_thread_id: int,
    ) -> AgentThreadBinding | None:
        db = await self.database.connect()
        try:
            cursor = await db.execute(
                """
                SELECT * FROM agent_thread_binding
                WHERE telegram_chat_id = ? AND message_thread_id = ?
                """,
                (chat_id, message_thread_id),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        if row is None:
            return None
        return AgentThreadBinding(
            agent_id=row["agent_id"],
            telegram_chat_id=row["telegram_chat_id"],
            message_thread_id=row["message_thread_id"],
        )

    async def upsert_agent_thread_binding(self, binding: AgentThreadBinding) -> None:
        now = _utcnow()
        db = await self.database.connect()
        try:
            await db.execute(
                """
                INSERT INTO agent_thread_binding (
                    agent_id,
                    telegram_chat_id,
                    message_thread_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    telegram_chat_id = excluded.telegram_chat_id,
                    message_thread_id = excluded.message_thread_id,
                    updated_at = excluded.updated_at
                """,
                (
                    binding.agent_id,
                    binding.telegram_chat_id,
                    binding.message_thread_id,
                    now,
                    now,
                ),
            )
            await db.commit()
        finally:
            await db.close()


def _utcnow() -> str:
    return datetime.now(tz=UTC).isoformat()
