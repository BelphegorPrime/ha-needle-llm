"""OpenAI-compatible llama.cpp HTTP client for Home Assistant tool routing."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class LlamaCppClientError(Exception):
    """Structured llama.cpp transport error."""

    stage: str
    path: str
    error_type: str
    message: str
    status: int | None = None
    response_body: str | None = None
    elapsed_ms: float | None = None

    def __str__(self) -> str:
        """Describe the error without leaking request headers."""
        return f"llama.cpp {self.stage} failed: {self.message or self.error_type}"

    def as_dict(self) -> dict[str, Any]:
        """Export transport diagnostics."""
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


def openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build native OpenAI function-calling definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tools
    ]


class LlamaCppClient:
    """One-shot tool routing; no persistent conversation or device actions."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        request_timeout: int,
        model: str = "",
    ) -> None:
        """Set the server URL and optional model alias."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._base_url = self._base_url[:-3]
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._model = model.strip() or None

    async def async_get_model(self) -> dict[str, Any]:
        """Validate /v1/models and return the selected or first model."""
        data = await self._request("GET", "/v1/models", stage="model")
        entries = data.get("data")
        if not isinstance(entries, list) or not entries:
            raise LlamaCppClientError(
                "model", "/v1/models", "InvalidResponse", "No models advertised"
            )
        first = entries[0]
        if not isinstance(first, dict) or not isinstance(first.get("id"), str):
            raise LlamaCppClientError(
                "model", "/v1/models", "InvalidResponse", "Missing model ID"
            )
        return {"name": self._model or first["id"]}

    async def async_complete(
        self,
        *,
        tools: list[dict[str, Any]],
        query: str,
        language: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Send a single stateless function-calling request."""
        if self._model is None:
            self._model = (await self.async_get_model())["name"]

        instruction = (
            "You are a Home Assistant tool router. Select only a tool whose "
            "action matches the user's request, using the tools supplied. "
            "Preserve literal entity and area names in all languages. "
            "Do not invent optional parameters, device classes, domains, "
            "colors, areas or numbers. Never guess an unsafe or ambiguous "
            "target. If you can act, return exactly one function tool call "
            "and no prose; otherwise return no tool call."
        )
        if language:
            instruction += f" Conversation locale: {language}."

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": query},
            ],
            "tools": openai_tools(tools),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": 0,
            "stream": False,
        }
        started = time.monotonic()
        result = await self._request(
            "POST", "/v1/chat/completions", stage="route", json=payload
        )
        usage = result.get("usage")
        return result, {
            "complete_ms": round((time.monotonic() - started) * 1000, 1),
            "model": self._model,
            "usage": usage if isinstance(usage, dict) else {},
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Read a JSON object with bounded HTTP timeout and helpful failures."""
        started = time.monotonic()
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}",
                timeout=self._timeout, **kwargs
            ) as response:
                raw = await response.text()
                elapsed_ms = (time.monotonic() - started) * 1000
                if response.status >= 400:
                    raise LlamaCppClientError(
                        stage, path, "HTTPError",
                        f"Server returned HTTP {response.status}",
                        status=response.status,
                        response_body=raw[:1000] or None,
                        elapsed_ms=elapsed_ms,
                    )
                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise LlamaCppClientError(
                        stage, path, type(err).__name__,
                        "Response was not valid JSON",
                        status=response.status,
                        response_body=raw[:1000] or None,
                        elapsed_ms=elapsed_ms,
                    ) from err
        except LlamaCppClientError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LlamaCppClientError(
                stage, path, type(err).__name__,
                str(err) or type(err).__name__,
                elapsed_ms=(time.monotonic() - started) * 1000,
            ) from err

        if not isinstance(data, dict):
            raise LlamaCppClientError(
                stage, path, "InvalidResponse", "Expected a JSON object"
            )
        return data
