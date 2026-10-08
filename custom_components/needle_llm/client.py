"""Async client for the Needle HTTP API."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class NeedleClientError(Exception):
    """Error raised by the Needle client with structured diagnostics."""

    stage: str
    path: str
    error_type: str
    message: str
    status: int | None = None
    response_body: str | None = None
    elapsed_ms: float | None = None

    def __str__(self) -> str:
        """Return a useful error even when the original exception was empty."""
        detail = self.message or self.error_type
        status = f" HTTP {self.status}" if self.status is not None else ""
        return f"Needle {self.stage} failed{status}: {detail}"

    def as_dict(self) -> dict[str, Any]:
        """Return diagnostics suitable for Home Assistant tool details."""
        data: dict[str, Any] = {
            "stage": self.stage,
            "path": self.path,
            "error_type": self.error_type,
            "message": self.message or self.error_type,
        }

        if self.status is not None:
            data["http_status"] = self.status

        if self.response_body:
            data["response_body"] = self.response_body

        if self.elapsed_ms is not None:
            data["elapsed_ms"] = round(self.elapsed_ms, 1)

        return data


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
        return await self._async_json_request(
            "GET",
            "/model",
            stage="model",
        )

    async def async_reset(self) -> dict[str, Any]:
        """Reset Needle conversation state."""
        return await self._async_json_request(
            "POST",
            "/reset",
            stage="reset",
        )

    async def async_complete(
        self,
        *,
        tools: list[dict[str, Any]],
        query: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Route one explicitly stateless request through Needle."""
        async with self._route_lock:
            reset_started = time.monotonic()
            reset_result = await self.async_reset()
            reset_ms = (time.monotonic() - reset_started) * 1000

            complete_started = time.monotonic()
            result = await self._async_json_request(
                "POST",
                "/complete",
                stage="complete",
                json={"tools": tools, "query": query},
            )
            complete_ms = (time.monotonic() - complete_started) * 1000

        return result, {
            "reset_ms": round(reset_ms, 1),
            "complete_ms": round(complete_ms, 1),
            "reset_response": reset_result,
        }

    async def _async_json_request(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform a JSON request with structured error reporting."""
        started = time.monotonic()

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                timeout=self._timeout,
                **kwargs,
            ) as response:
                body = await response.text()
                elapsed_ms = (time.monotonic() - started) * 1000

                if response.status >= 400:
                    raise NeedleClientError(
                        stage=stage,
                        path=path,
                        error_type="HTTPError",
                        message=f"Needle returned HTTP {response.status}",
                        status=response.status,
                        response_body=body[:1000] or None,
                        elapsed_ms=elapsed_ms,
                    )

                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise NeedleClientError(
                        stage=stage,
                        path=path,
                        error_type=type(err).__name__,
                        message=str(err) or "Response was not valid JSON",
                        status=response.status,
                        response_body=body[:1000] or None,
                        elapsed_ms=elapsed_ms,
                    ) from err

        except NeedleClientError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            elapsed_ms = (time.monotonic() - started) * 1000
            raise NeedleClientError(
                stage=stage,
                path=path,
                error_type=type(err).__name__,
                message=str(err) or type(err).__name__,
                elapsed_ms=elapsed_ms,
            ) from err

        if not isinstance(data, dict):
            raise NeedleClientError(
                stage=stage,
                path=path,
                error_type="InvalidResponse",
                message="Needle returned JSON that is not an object",
                status=response.status,
                response_body=body[:1000] or None,
                elapsed_ms=elapsed_ms,
            )

        return data
