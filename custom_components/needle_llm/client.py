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

    async def async_reset(self) -> None:
        """Reset Needle conversation state."""
        try:
            async with self._session.post(
                f"{self._base_url}/reset",
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NeedleClientError("Unable to reset Needle") from err

    async def async_complete(
        self,
        *,
        tools: list[dict[str, Any]],
        query: str,
        reset_before_call: bool,
    ) -> dict[str, Any]:
        """Route one request through Needle.

        The reset and complete requests share a lock so concurrent Home Assistant
        requests cannot interleave their Needle conversation state.
        """
        async with self._route_lock:
            if reset_before_call:
                await self.async_reset()

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
            raise NeedleClientError(f"Needle request failed: {path}") from err

        if not isinstance(data, dict):
            raise NeedleClientError(f"Needle returned invalid JSON for {path}")

        return data
