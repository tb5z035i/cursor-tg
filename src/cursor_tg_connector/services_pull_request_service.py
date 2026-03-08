from __future__ import annotations

from cursor_tg_connector.cursor_api_models import Agent
from cursor_tg_connector.github_api_client import GitHubApiClient
from cursor_tg_connector.github_api_models import (
    GitHubMergeMethod,
    GitHubMergeResult,
    GitHubPullRequest,
)


class PullRequestActionError(RuntimeError):
    pass


class PullRequestService:
    def __init__(self, github_client: GitHubApiClient | None) -> None:
        self.github_client = github_client

    @property
    def enabled(self) -> bool:
        return self.github_client is not None

    async def get_pull_request(self, agent: Agent) -> GitHubPullRequest:
        github_client = self._require_client()
        pr_url = self._require_pr_url(agent)
        return await github_client.get_pull_request(pr_url)

    async def get_pull_request_diff(self, agent: Agent) -> tuple[GitHubPullRequest, str]:
        github_client = self._require_client()
        pr_url = self._require_pr_url(agent)
        pull_request = await github_client.get_pull_request(pr_url)
        diff_text = await github_client.get_pull_request_diff(pr_url)
        return pull_request, diff_text

    async def mark_ready_for_review(self, agent: Agent) -> GitHubPullRequest:
        github_client = self._require_client()
        pr_url = self._require_pr_url(agent)
        pull_request = await github_client.get_pull_request(pr_url)
        if pull_request.merged:
            raise PullRequestActionError("This pull request has already been merged.")
        if pull_request.state.lower() != "open":
            raise PullRequestActionError("This pull request is not open anymore.")
        if not pull_request.draft:
            raise PullRequestActionError("This pull request is already ready for review.")
        return await github_client.mark_ready_for_review(
            pr_url,
            pull_request_id=pull_request.node_id,
        )

    async def merge_pull_request(
        self,
        agent: Agent,
        *,
        merge_method: GitHubMergeMethod,
    ) -> GitHubMergeResult:
        github_client = self._require_client()
        pr_url = self._require_pr_url(agent)
        pull_request = await github_client.get_pull_request(pr_url)
        if pull_request.merged:
            raise PullRequestActionError("This pull request has already been merged.")
        if pull_request.state.lower() != "open":
            raise PullRequestActionError("This pull request is not open anymore.")
        return await github_client.merge_pull_request(pr_url, merge_method=merge_method)

    def _require_client(self) -> GitHubApiClient:
        if self.github_client is None:
            raise PullRequestActionError(
                "GitHub PR integration is unavailable. Set GITHUB_TOKEN (or GITHUB_PAT) "
                "to enable PR inspection, diff, ready-for-review, and merge actions."
            )
        return self.github_client

    def _require_pr_url(self, agent: Agent) -> str:
        if not agent.target.pr_url:
            raise PullRequestActionError(
                f"{agent.name or agent.id} does not have a pull request yet."
            )
        return agent.target.pr_url
