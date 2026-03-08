from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from cursor_tg_connector.github_api_models import (
    GitHubErrorEnvelope,
    GitHubMergeMethod,
    GitHubMergeResult,
    GitHubPullRequest,
)

logger = logging.getLogger(__name__)

_MARK_READY_FOR_REVIEW_MUTATION = """
mutation MarkPullRequestReadyForReview($pullRequestId: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
    clientMutationId
  }
}
""".strip()


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
        self._graphql_url = build_github_graphql_url(base_url)
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

    async def mark_ready_for_review(
        self,
        pr_url: str,
        *,
        pull_request_id: str | None = None,
    ) -> GitHubPullRequest:
        if not pull_request_id:
            pull_request = await self.get_pull_request(pr_url)
            pull_request_id = pull_request.node_id
        if not pull_request_id:
            raise GitHubApiError(
                "GitHub pull request response did not include the node_id required to "
                "mark it ready for review."
            )

        await self._graphql_request(
            query=_MARK_READY_FOR_REVIEW_MUTATION,
            variables={"pullRequestId": pull_request_id},
        )
        return await self.get_pull_request(pr_url)

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

    async def _graphql_request(
        self,
        *,
        query: str,
        variables: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._graphql_url,
            json={"query": query, "variables": variables or {}},
        )
        if response.status_code != 200:
            raise GitHubApiError(self._build_error_message(response))

        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise GitHubApiError(
                self._format_error_message(
                    status_code=response.status_code,
                    url=str(response.request.url),
                    message=_extract_graphql_error_message(errors),
                )
            )
        return payload.get("data", {})

    def _build_error_message(self, response: httpx.Response) -> str:
        try:
            payload = GitHubErrorEnvelope.model_validate(response.json())
            message = payload.message or response.text
        except Exception:  # pragma: no cover - defensive fallback
            message = response.text

        return self._format_error_message(
            status_code=response.status_code,
            url=str(response.request.url),
            message=message,
        )

    def _format_error_message(self, *, status_code: int, url: str, message: str) -> str:
        normalized = message.strip()
        if status_code == 401:
            normalized = "Unauthorized - invalid or missing GitHub token"
        elif status_code == 403 and "rate limit" in normalized.lower():
            normalized = "GitHub API rate limit exceeded"
        elif status_code == 404:
            normalized = (
                "Pull request not found, or the GitHub token does not have access to the repository"
            )

        logger.warning(
            "GitHub API error status=%s url=%s message=%s",
            status_code,
            url,
            normalized,
        )
        return f"GitHub API request failed ({status_code}): {normalized}"


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


def build_github_graphql_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/api/v3"):
        graphql_path = f"{base_path[:-3]}/graphql"
    elif base_path:
        graphql_path = f"{base_path}/graphql"
    else:
        graphql_path = "/graphql"
    return urlunparse(parsed._replace(path=graphql_path, params="", query="", fragment=""))


def _extract_graphql_error_message(errors: object) -> str:
    if not isinstance(errors, list):
        return "GitHub GraphQL request failed"

    messages = [
        str(item.get("message", "")).strip()
        for item in errors
        if isinstance(item, dict) and item.get("message")
    ]
    if messages:
        return "; ".join(messages)
    return "GitHub GraphQL request failed"
