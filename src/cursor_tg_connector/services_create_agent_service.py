from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cursor_tg_connector.cursor_api_client import CursorApiClient
from cursor_tg_connector.cursor_api_models import Agent
from cursor_tg_connector.domain_types import SessionState, WizardStep
from cursor_tg_connector.persistence_state_repo import StateRepository
from cursor_tg_connector.utils_formatting import paginate


class CreateAgentError(RuntimeError):
    pass


@dataclass(slots=True)
class RepositoryPage:
    repositories: list[str]
    page: int
    total_pages: int


class CreateAgentService:
    def __init__(self, cursor_client: CursorApiClient, state_repo: StateRepository) -> None:
        self.cursor_client = cursor_client
        self.state_repo = state_repo

    async def start_wizard(self, telegram_user_id: int, chat_id: int) -> list[str]:
        session = await self.state_repo.update_chat_context(telegram_user_id, chat_id)
        if session.wizard_state != WizardStep.IDLE:
            raise CreateAgentError(
                "A create-agent wizard is already in progress. Use /cancel to exit it."
            )

        if self._is_rate_limited(session):
            raise CreateAgentError("You can only start /newagent once per minute.")

        models = await self.cursor_client.list_models()
        if not models:
            raise CreateAgentError("Cursor returned no available models.")

        await self.state_repo.set_last_create_agent_at(telegram_user_id, datetime.now(tz=UTC))
        await self.state_repo.set_wizard(
            telegram_user_id,
            WizardStep.WAITING_MODEL,
            {"models": models},
        )
        return models

    async def get_model_page(
        self,
        telegram_user_id: int,
        page: int,
        per_page: int = 8,
    ) -> RepositoryPage:
        session = await self.state_repo.get_session(telegram_user_id)
        models = self._wizard_list(session, "models")
        items, current_page, total_pages = paginate(models, page, per_page)
        return RepositoryPage(repositories=items, page=current_page, total_pages=total_pages)

    async def choose_model(self, telegram_user_id: int, model_id: str) -> RepositoryPage:
        session = await self.state_repo.get_session(telegram_user_id)
        models = self._wizard_list(session, "models")
        if session.wizard_state != WizardStep.WAITING_MODEL or model_id not in models:
            raise CreateAgentError(
                "That model selection is no longer valid. Run /newagent again."
            )

        repositories = await self.cursor_client.list_repositories()
        if not repositories:
            raise CreateAgentError("Cursor returned no available repositories.")

        payload = {"model": model_id, "repositories": repositories}
        await self.state_repo.set_wizard(telegram_user_id, WizardStep.WAITING_REPOSITORY, payload)
        return self.get_repository_page_from_payload(repositories, 0)

    async def get_repository_page(
        self,
        telegram_user_id: int,
        page: int,
        per_page: int = 8,
    ) -> RepositoryPage:
        session = await self.state_repo.get_session(telegram_user_id)
        repositories = self._wizard_list(session, "repositories")
        return self.get_repository_page_from_payload(repositories, page, per_page)

    def get_repository_page_from_payload(
        self,
        repositories: list[str],
        page: int,
        per_page: int = 8,
    ) -> RepositoryPage:
        items, current_page, total_pages = paginate(repositories, page, per_page)
        return RepositoryPage(repositories=items, page=current_page, total_pages=total_pages)

    async def choose_repository(
        self, telegram_user_id: int, repository_index: int
    ) -> tuple[str, list[str]]:
        session = await self.state_repo.get_session(telegram_user_id)
        repositories = self._wizard_list(session, "repositories")
        if (
            session.wizard_state != WizardStep.WAITING_REPOSITORY
            or repository_index >= len(repositories)
        ):
            raise CreateAgentError(
                "That repository selection is no longer valid. Run /newagent again."
            )

        repository = repositories[repository_index]
        branches = await self._fetch_branches_for_repository(repository)

        payload = {
            "model": session.wizard_payload["model"],
            "repository": repository,
            "branches": branches,
        }
        await self.state_repo.set_wizard(telegram_user_id, WizardStep.WAITING_BRANCH, payload)
        return repository, branches

    async def get_branch_page(
        self,
        telegram_user_id: int,
        page: int,
        per_page: int = 8,
    ) -> RepositoryPage:
        session = await self.state_repo.get_session(telegram_user_id)
        branches = self._wizard_list(session, "branches")
        return self.get_branch_page_from_payload(branches, page, per_page)

    def get_branch_page_from_payload(
        self,
        branches: list[str],
        page: int,
        per_page: int = 8,
    ) -> RepositoryPage:
        items, current_page, total_pages = paginate(branches, page, per_page)
        return RepositoryPage(repositories=items, page=current_page, total_pages=total_pages)

    async def choose_branch(self, telegram_user_id: int, branch_index: int) -> None:
        session = await self.state_repo.get_session(telegram_user_id)
        branches = self._wizard_list(session, "branches")
        if (
            session.wizard_state != WizardStep.WAITING_BRANCH
            or branch_index >= len(branches)
        ):
            raise CreateAgentError(
                "That branch selection is no longer valid. Run /newagent again."
            )

        payload = dict(session.wizard_payload)
        payload["branch"] = branches[branch_index]
        del payload["branches"]
        await self.state_repo.set_wizard(telegram_user_id, WizardStep.WAITING_PROMPT, payload)

    async def save_branch(self, telegram_user_id: int, branch_name: str) -> None:
        branch_name = branch_name.strip()
        if not branch_name:
            raise CreateAgentError("Base branch cannot be empty.")

        session = await self.state_repo.get_session(telegram_user_id)
        if session.wizard_state != WizardStep.WAITING_BRANCH:
            raise CreateAgentError("No branch input is expected right now.")

        payload = dict(session.wizard_payload)
        payload["branch"] = branch_name
        payload.pop("branches", None)
        await self.state_repo.set_wizard(telegram_user_id, WizardStep.WAITING_PROMPT, payload)

    async def finish_prompt(self, telegram_user_id: int, prompt_text: str) -> Agent:
        prompt_text = prompt_text.strip()
        if not prompt_text:
            raise CreateAgentError("Prompt text cannot be empty.")

        session = await self.state_repo.get_session(telegram_user_id)
        if session.wizard_state != WizardStep.WAITING_PROMPT:
            raise CreateAgentError("No prompt input is expected right now.")

        payload = session.wizard_payload
        agent = await self.cursor_client.create_agent(
            model=payload["model"],
            repository_url=payload["repository"],
            base_branch=payload["branch"],
            prompt_text=prompt_text,
        )
        await self.state_repo.clear_wizard(telegram_user_id)
        await self.state_repo.set_active_agent(telegram_user_id, agent.id)
        return agent

    async def cancel(self, telegram_user_id: int) -> bool:
        session = await self.state_repo.get_session(telegram_user_id)
        if session.wizard_state == WizardStep.IDLE:
            return False
        await self.state_repo.clear_wizard(telegram_user_id)
        return True

    async def get_session(self, telegram_user_id: int) -> SessionState:
        return await self.state_repo.get_session(telegram_user_id)

    def _is_rate_limited(self, session: SessionState) -> bool:
        if not session.last_create_agent_at:
            return False
        last_start = datetime.fromisoformat(session.last_create_agent_at)
        return datetime.now(tz=UTC) - last_start < timedelta(minutes=1)

    async def _fetch_branches_for_repository(self, repository_url: str) -> list[str]:
        try:
            agents = await self.cursor_client.list_agents()
        except Exception:
            agents = []

        seen: set[str] = set()
        branches: list[str] = []
        for agent in agents:
            ref = agent.source.ref
            if ref and agent.source.repository == repository_url and ref not in seen:
                seen.add(ref)
                branches.append(ref)

        if "main" not in seen:
            branches.insert(0, "main")

        return branches

    def _wizard_list(self, session: SessionState, key: str) -> list[str]:
        values = session.wizard_payload.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise CreateAgentError(
                "Wizard state is missing required options. Run /newagent again."
            )
        return values
