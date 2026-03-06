from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cursor_tg_connector.cursor_api_models import Agent
from cursor_tg_connector.domain_types import WizardStep
from cursor_tg_connector.services_create_agent_service import CreateAgentError, CreateAgentService


class FakeCursorClient:
    def __init__(self) -> None:
        self.models = ["gpt-5.4", "opus-4.6-fast"]
        self.repositories = [
            "https://github.com/acme/repo-a",
            "https://github.com/acme/repo-b",
        ]
        self.created_agent_calls: list[tuple[str, str, str, str]] = []

    async def list_models(self) -> list[str]:
        return self.models

    async def list_repositories(self) -> list[str]:
        return self.repositories

    async def create_agent(
        self,
        *,
        model: str,
        repository_url: str,
        base_branch: str,
        prompt_text: str,
    ) -> Agent:
        self.created_agent_calls.append((model, repository_url, base_branch, prompt_text))
        return Agent.model_validate(
            {
                "id": "agent-123",
                "name": "Build feature",
                "status": "CREATING",
                "source": {"repository": repository_url, "ref": base_branch},
                "target": {"url": "https://cursor.com/agent-123", "branchName": "cursor/branch"},
                "createdAt": "2024-01-01T00:00:00Z",
            }
        )


@pytest.mark.asyncio
async def test_create_agent_wizard_happy_path(state_repo) -> None:
    service = CreateAgentService(FakeCursorClient(), state_repo)

    models = await service.start_wizard(1234, 5678)
    assert models == ["gpt-5.4", "opus-4.6-fast"]

    session = await service.get_session(1234)
    assert session.wizard_state == WizardStep.WAITING_MODEL

    repo_page = await service.choose_model(1234, "gpt-5.4")
    assert repo_page.repositories == ["https://github.com/acme/repo-a", "https://github.com/acme/repo-b"]

    repository = await service.choose_repository(1234, 1)
    assert repository == "https://github.com/acme/repo-b"

    await service.save_branch(1234, "main")
    agent = await service.finish_prompt(1234, "Implement it")

    session = await service.get_session(1234)
    assert session.wizard_state == WizardStep.IDLE
    assert session.active_agent_id == "agent-123"
    assert agent.id == "agent-123"


@pytest.mark.asyncio
async def test_create_agent_start_is_rate_limited(state_repo) -> None:
    service = CreateAgentService(FakeCursorClient(), state_repo)
    await state_repo.set_last_create_agent_at(1234, datetime.now(tz=UTC) - timedelta(seconds=30))

    with pytest.raises(CreateAgentError, match="once per minute"):
        await service.start_wizard(1234, 5678)
