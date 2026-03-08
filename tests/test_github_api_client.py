from __future__ import annotations

import httpx
import pytest
import respx

from cursor_tg_connector.github_api_client import (
    GitHubApiClient,
    GitHubApiError,
    parse_github_pr_url,
)


@pytest.mark.asyncio
async def test_get_pull_request_fetches_pr_details() -> None:
    async with httpx.AsyncClient(base_url="https://api.github.com") as http_client:
        client = GitHubApiClient(
            token="github-token",
            base_url="https://api.github.com",
            http_client=http_client,
        )

        with respx.mock(assert_all_called=True) as router:
            router.get("https://api.github.com/repos/acme/repo/pulls/123").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "number": 123,
                        "title": "Improve bot PR actions",
                        "state": "open",
                        "draft": True,
                        "merged": False,
                        "html_url": "https://github.com/acme/repo/pull/123",
                        "mergeable": True,
                        "mergeable_state": "clean",
                        "head": {"ref": "cursor/a"},
                        "base": {"ref": "main"},
                    },
                )
            )

            pull_request = await client.get_pull_request("https://github.com/acme/repo/pull/123")

    assert pull_request.number == 123
    assert pull_request.draft is True
    assert pull_request.mergeable_state == "clean"


@pytest.mark.asyncio
async def test_merge_pull_request_surfaces_github_error_message() -> None:
    async with httpx.AsyncClient(base_url="https://api.github.com") as http_client:
        client = GitHubApiClient(
            token="github-token",
            base_url="https://api.github.com",
            http_client=http_client,
        )

        with respx.mock(assert_all_called=True) as router:
            router.put("https://api.github.com/repos/acme/repo/pulls/123/merge").mock(
                return_value=httpx.Response(
                    405,
                    json={"message": "Pull Request is not mergeable"},
                )
            )

            with pytest.raises(GitHubApiError, match="Pull Request is not mergeable"):
                await client.merge_pull_request(
                    "https://github.com/acme/repo/pull/123",
                    merge_method="squash",
                )


def test_parse_github_pr_url_rejects_non_pull_request_urls() -> None:
    with pytest.raises(GitHubApiError, match="Unsupported pull request URL"):
        parse_github_pr_url("https://example.com/acme/repo/issues/123")
