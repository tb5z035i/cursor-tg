from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from cursor_tg_connector.github_api_models import (
    GitHubErrorEnvelope,
    GitHubMergeMethod,
    GitHubMergeResult,
    GitHubPullRequest,
)

logger = logging.getLogger(__name__)


class GitHubApiError(RuntimeError):
    pass


class GitHubApiClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        timeout_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_pull_request(self, pr_url: str) -> GitHubPullRequest:
        owner, repo, pull_number = parse_github_pr_url(pr_url)
        payload = await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")
        return GitHubPullRequest.model_validate(payload)

    async def mark_ready_for_review(self, pr_url: str) -> GitHubPullRequest:
        owner, repo, pull_number = parse_github_pr_url(pr_url)
        payload = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/ready_for_review",
        )
        return GitHubPullRequest.model_validate(payload)

    async def merge_pull_request(
        self,
        pr_url: str,
        *,
        merge_method: GitHubMergeMethod,
    ) -> GitHubMergeResult:
        owner, repo, pull_number = parse_github_pr_url(pr_url)
        payload = await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/merge",
            json={"merge_method": merge_method},
        )
        return GitHubMergeResult.model_validate(payload)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object] | None = None,
        expected_status: int = 200,
    ) -> dict[str, object]:
        response = await self._client.request(method, url, json=json)
        if response.status_code == expected_status:
            return response.json()
        raise GitHubApiError(self._build_error_message(response))

    def _build_error_message(self, response: httpx.Response) -> str:
        try:
            payload = GitHubErrorEnvelope.model_validate(response.json())
            message = payload.message or response.text
        except Exception:  # pragma: no cover - defensive fallback
            message = response.text

        normalized = message.strip()
        if response.status_code == 401:
            normalized = "Unauthorized - invalid or missing GitHub token"
        elif response.status_code == 403 and "rate limit" in normalized.lower():
            normalized = "GitHub API rate limit exceeded"
        elif response.status_code == 404:
            normalized = (
                "Pull request not found, or the GitHub token does not have access to the repository"
            )

        logger.warning(
            "GitHub API error status=%s url=%s message=%s",
            response.status_code,
            response.request.url,
            normalized,
        )
        return f"GitHub API request failed ({response.status_code}): {normalized}"


def parse_github_pr_url(pr_url: str) -> tuple[str, str, int]:
    parsed = urlparse(pr_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GitHubApiError(f"Unsupported pull request URL: {pr_url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "pull":
        raise GitHubApiError(f"Unsupported pull request URL: {pr_url}")

    owner, repo, _, raw_number = parts[:4]
    try:
        return owner, repo, int(raw_number)
    except ValueError as exc:
        raise GitHubApiError(f"Unsupported pull request URL: {pr_url}") from exc
