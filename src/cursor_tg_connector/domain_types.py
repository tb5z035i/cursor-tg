from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WizardStep(StrEnum):
    IDLE = "idle"
    WAITING_MODEL = "waiting_model"
    WAITING_REPOSITORY = "waiting_repository"
    WAITING_BRANCH = "waiting_branch"
    WAITING_PROMPT = "waiting_prompt"


@dataclass(slots=True)
class SessionState:
    telegram_user_id: int
    telegram_chat_id: int | None = None
    active_agent_id: str | None = None
    wizard_state: WizardStep = WizardStep.IDLE
    wizard_payload: dict[str, Any] = field(default_factory=dict)
    last_create_agent_at: str | None = None


@dataclass(slots=True)
class AgentUnreadState:
    agent_id: str
    unread_count: int
    newest_unread_message_id: str | None


@dataclass(slots=True)
class AgentListItem:
    agent_id: str
    label: str
    unread_count: int
    is_active: bool
