from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from cursor_tg_connector.cursor_api_models import (
    Agent,
    AgentConversation,
    ApiKeyInfo,
    ErrorEnvelope,
    ListAgentsResponse,
    ListModelsResponse,
    ListRepositoriesResponse,
)

logger = logging.getLogger(__name__)


class CursorApiError(RuntimeError):
    pass


class CursorApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def validate_api_key(self) -> ApiKeyInfo:
        payload = await self._request("GET", "/v0/me")
        return ApiKeyInfo.model_validate(payload)

    async def list_agents(self) -> list[Agent]:
        agents: list[Agent] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor

            payload = await self._request("GET", "/v0/agents", params=params)
            response = ListAgentsResponse.model_validate(payload)
            agents.extend(response.agents)
            if not response.next_cursor:
                break
            cursor = response.next_cursor

        return agents

    async def get_agent(self, agent_id: str) -> Agent:
        payload = await self._request("GET", f"/v0/agents/{agent_id}")
        return Agent.model_validate(payload)

    async def get_conversation(self, agent_id: str) -> AgentConversation:
        payload = await self._request("GET", f"/v0/agents/{agent_id}/conversation")
        return AgentConversation.model_validate(payload)

    async def add_followup(self, agent_id: str, prompt_text: str) -> str:
        payload = await self._request(
            "POST",
            f"/v0/agents/{agent_id}/followup",
            json={"prompt": {"text": prompt_text}},
        )
        return str(payload["id"])

    async def create_agent(
        self,
        *,
        model: str,
        repository_url: str,
        base_branch: str,
        prompt_text: str,
    ) -> Agent:
        payload = await self._request(
            "POST",
            "/v0/agents",
            json={
                "model": model,
                "prompt": {"text": prompt_text},
                "source": {"repository": repository_url, "ref": base_branch},
            },
            expected_status=201,
        )
        return Agent.model_validate(payload)

    async def list_models(self) -> list[str]:
        payload = await self._request("GET", "/v0/models")
        response = ListModelsResponse.model_validate(payload)
        return response.models

    async def list_repositories(self) -> list[str]:
        payload = await self._request("GET", "/v0/repositories")
        response = ListRepositoriesResponse.model_validate(payload)
        return [repository.repository for repository in response.repositories]

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                )
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise CursorApiError(
                        f"Cursor API transport error after {attempt + 1} attempts: {exc}"
                    ) from exc

                await self._sleep_before_retry(attempt)
                continue

            if response.status_code == expected_status:
                return response.json()

            if attempt < self._max_retries and self._should_retry_status(response.status_code):
                logger.warning(
                    "Retrying Cursor API request method=%s url=%s status=%s attempt=%s",
                    method,
                    url,
                    response.status_code,
                    attempt + 1,
                )
                await self._sleep_before_retry(attempt, response)
                continue

            raise CursorApiError(self._build_error_message(response))

        raise CursorApiError("Cursor API request exhausted retries unexpectedly")

    async def _sleep_before_retry(
        self,
        attempt: int,
        response: httpx.Response | None = None,
    ) -> None:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    await asyncio.sleep(float(retry_after))
                    return
                except ValueError:
                    logger.warning("Invalid Retry-After header value: %s", retry_after)

        delay = self._retry_backoff_seconds * (2**attempt)
        await asyncio.sleep(delay)

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _build_error_message(self, response: httpx.Response) -> str:
        try:
            payload = ErrorEnvelope.model_validate(response.json())
            if payload.error and payload.error.message:
                message = payload.error.message
            else:
                message = response.text
        except Exception:  # pragma: no cover - defensive fallback
            message = response.text

        logger.warning(
            "Cursor API error status=%s url=%s message=%s",
            response.status_code,
            response.request.url,
            message,
        )
        return f"Cursor API request failed ({response.status_code}): {message}"
