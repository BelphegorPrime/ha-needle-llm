"""Async client for the Needle HTTP API."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class NeedleClientError(Exception):
    """Base error raised by the Needle client."""


class NeedleClient:
    """Small async client for a Needle playground/server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        timeout: int,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._route_lock = asyncio.Lock()

    async def async_get_model(self) -> dict[str, Any]:
        """Return model information from Needle."""
        return await self._async_json_request("GET", "/model")

    async def async_complete(
        self,
        *,
        tools: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any]:
        """Route one stateless request through Needle.

        Needle's playground server already starts every /complete request from a
        fresh turn: it resets the current agent when the tool schema is unchanged
        and creates a fresh agent when it changes. Serializing calls prevents
        concurrent requests from sharing mutable server state.
        """
        async with self._route_lock:
            return await self._async_json_request(
                "POST",
                "/complete",
                json={"tools": tools, "query": query},
            )

    async def _async_json_request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform a JSON request."""
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                timeout=self._timeout,
                **kwargs,
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise NeedleClientError(
                f"Needle request failed: {path}: {err}"
            ) from err

        if not isinstance(data, dict):
            raise NeedleClientError(f"Needle returned invalid JSON for {path}")

        return data
