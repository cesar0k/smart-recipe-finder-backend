"""Thin async HTTP client used by MCP tools.

Keeping the MCP layer as a REST client for v1 means we reuse the existing
validation, caching, rate limiting and background tasks instead of duplicating
them. The interface is small enough to swap for direct service calls later.
"""

from typing import Any

import httpx

from app.mcp.config import settings


class RecipeApiError(Exception):
    """Non-2xx response from the REST API."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail is not None:
            return str(detail)
    return str(payload)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Drop ``None`` and empty-string values so FastAPI does not see blanks."""
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None and value != ""}


class RecipeApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url or settings.SRF_API_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout or settings.SRF_API_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> "RecipeApiClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self._client.aclose()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._client.get(
            path,
            params=_clean_params(params),
        )
        if response.status_code >= 400:
            raise RecipeApiError(response.status_code, _extract_detail(response))
        return response.json()
