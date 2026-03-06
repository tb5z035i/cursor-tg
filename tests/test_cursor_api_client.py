from __future__ import annotations

import httpx
import pytest
import respx

from cursor_tg_connector.cursor_api_client import CursorApiClient, CursorApiError


@pytest.mark.asyncio
async def test_list_agents_paginates() -> None:
    async with httpx.AsyncClient(base_url="https://api.cursor.com") as http_client:
        client = CursorApiClient(
            api_key="test-key",
            base_url="https://api.cursor.com",
            http_client=http_client,
        )

        with respx.mock(assert_all_called=True) as router:
            router.get("https://api.cursor.com/v0/agents").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "agents": [
                                {
                                    "id": "agent-1",
                                    "name": "A1",
                                    "status": "RUNNING",
                                    "source": {
                                        "repository": "https://github.com/acme/repo",
                                        "ref": "main",
                                    },
                                    "target": {"url": "https://cursor.com/a1"},
                                    "createdAt": "2024-01-01T00:00:00Z",
                                }
                            ],
                            "nextCursor": "cursor-2",
                        },
                    ),
                    httpx.Response(
                        200,
                        json={
                            "agents": [
                                {
                                    "id": "agent-2",
                                    "name": "A2",
                                    "status": "RUNNING",
                                    "source": {
                                        "repository": "https://github.com/acme/repo",
                                        "ref": "main",
                                    },
                                    "target": {"url": "https://cursor.com/a2"},
                                    "createdAt": "2024-01-01T00:00:00Z",
                                }
                            ]
                        },
                    ),
                ]
            )

            agents = await client.list_agents()

    assert [agent.id for agent in agents] == ["agent-1", "agent-2"]


@pytest.mark.asyncio
async def test_create_agent_surfaces_cursor_error_message() -> None:
    async with httpx.AsyncClient(base_url="https://api.cursor.com") as http_client:
        client = CursorApiClient(
            api_key="test-key",
            base_url="https://api.cursor.com",
            http_client=http_client,
        )

        with respx.mock(assert_all_called=True) as router:
            router.post("https://api.cursor.com/v0/agents").mock(
                return_value=httpx.Response(
                    429,
                    json={"error": {"message": "Too many requests"}},
                )
            )

            with pytest.raises(CursorApiError, match="Too many requests"):
                await client.create_agent(
                    model="gpt-5",
                    repository_url="https://github.com/acme/repo",
                    base_branch="main",
                    prompt_text="hello",
                )


@pytest.mark.asyncio
async def test_request_retries_on_server_error_then_succeeds() -> None:
    async with httpx.AsyncClient(base_url="https://api.cursor.com") as http_client:
        client = CursorApiClient(
            api_key="test-key",
            base_url="https://api.cursor.com",
            http_client=http_client,
            max_retries=1,
            retry_backoff_seconds=0.001,
        )

        with respx.mock(assert_all_called=True) as router:
            router.get("https://api.cursor.com/v0/me").mock(
                side_effect=[
                    httpx.Response(500, json={"error": {"message": "temporary failure"}}),
                    httpx.Response(
                        200,
                        json={
                            "apiKeyName": "test-key",
                            "createdAt": "2024-01-01T00:00:00Z",
                        },
                    ),
                ]
            )

            api_key_info = await client.validate_api_key()

    assert api_key_info.api_key_name == "test-key"


@pytest.mark.asyncio
async def test_validate_api_key_maps_generic_401_to_friendly_message() -> None:
    async with httpx.AsyncClient(base_url="https://api.cursor.com") as http_client:
        client = CursorApiClient(
            api_key="test-key",
            base_url="https://api.cursor.com",
            http_client=http_client,
        )

        with respx.mock(assert_all_called=True) as router:
            router.get("https://api.cursor.com/v0/me").mock(
                return_value=httpx.Response(
                    401,
                    json={"code": "internal", "message": "Error"},
                )
            )

            with pytest.raises(
                CursorApiError,
                match="Unauthorized - invalid or missing Cursor API key",
            ):
                await client.validate_api_key()
