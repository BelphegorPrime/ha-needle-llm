"""Reuse an existing Home Assistant model integration without duplicating secrets.

The first adapter supports Home Assistant's built-in llama_cpp Conversation
integration, which exposes a loaded AsyncOpenAI client via ConfigEntry.runtime_data.
Other providers can be added as explicit adapters without changing routing.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


def openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert native Assist tools to OpenAI-format model proposals.

    This is used only by an existing HA model before Needle approval, never
    as a standalone model-only execution backend.
    """
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


PROVIDER_DOMAIN = "llama_cpp"
_MODEL_KEY = "chat_model"


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """Selected existing HA configuration and model subentry."""

    entry_id: str
    subentry_id: str


class ProviderError(Exception):
    """A configured Home Assistant model integration cannot be used."""


def encode_selection(entry_id: str, subentry_id: str) -> str:
    """Serialize a specific integration and conversation model."""
    return f"{entry_id}:{subentry_id}"


def decode_selection(value: str) -> ProviderSelection:
    """Reject malformed config references before they access HA entries."""
    parts = value.split(":")
    if len(parts) != 2 or not all(parts):
        raise ProviderError("Select an existing model integration and model")
    return ProviderSelection(*parts)


def available_provider_models(hass: HomeAssistant) -> dict[str, str]:
    """Return supported loaded HA integrations and their conversation models."""
    result: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(PROVIDER_DOMAIN):
        if (
            entry.disabled_by is not None
            or entry.state is not ConfigEntryState.LOADED
        ):
            continue
        for subentry in getattr(entry, "subentries", {}).values():
            if subentry.subentry_type != "conversation":
                continue
            model = subentry.data.get(_MODEL_KEY)
            if not isinstance(model, str) or not model:
                continue
            selection = encode_selection(entry.entry_id, subentry.subentry_id)
            result[selection] = (
                f"{entry.title} / {subentry.title} ({model})"
            )
    return result


def resolve_provider(
    hass: HomeAssistant,
    selection: str,
) -> tuple[Any, str]:
    """Read a loaded integration's existing client and selected model.

    No API keys or server URLs are copied into this router's configuration.
    A removed, disabled or unloaded integration fails closed.
    """
    ref = decode_selection(selection)
    entry = hass.config_entries.async_get_entry(ref.entry_id)
    if entry is None or entry.domain != PROVIDER_DOMAIN:
        raise ProviderError("The selected llama.cpp integration is unavailable")
    if (
        entry.disabled_by is not None
        or entry.state is not ConfigEntryState.LOADED
    ):
        raise ProviderError("The selected model integration is not loaded")

    subentry = getattr(entry, "subentries", {}).get(ref.subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise ProviderError("The selected conversation model is unavailable")

    model = subentry.data.get(_MODEL_KEY)
    if not isinstance(model, str) or not model:
        raise ProviderError("The selected model does not have a valid ID")

    client = getattr(entry, "runtime_data", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    create = getattr(completions, "create", None)
    if not callable(create):
        raise ProviderError(
            "The selected integration does not expose a compatible "
            "loaded model client"
        )
    return client, model


class HomeAssistantModelProvider:
    """Stateless model-call adapter for the selected HA conversation model."""

    def __init__(
        self, hass: HomeAssistant, selection: str, timeout: int
    ) -> None:
        """Keep only a reference; always resolve the current HA model client."""
        self._hass = hass
        self._selection = selection
        self._timeout = timeout

    async def async_complete(
        self,
        *,
        tools: list[dict[str, Any]],
        query: str,
        language: str | None,
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Generate a tool-call proposal, never execute one."""
        client, model = resolve_provider(self._hass, self._selection)
        system = (
            "You are a Home Assistant tool router. Select the action the "
            "user requested using only the supplied tools. Preserve the "
            "user's literal device and room names in any language. Never "
            "invent optional settings, targets, domains or device classes. "
            "When the request is actionable, propose exactly one function "
            "call. Do not execute any action and do not guess."
        )
        if language:
            system += f" Conversation locale: {language}."
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout):
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": query},
                    ],
                    tools=openai_tools(tools),
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    temperature=0,
                    stream=False,
                )
        except TimeoutError as err:
            raise ProviderError(
                "The selected Home Assistant model timed out"
            ) from err
        except Exception as err:  # noqa: BLE001
            # Do not leak provider credentials or user prompts through errors.
            raise ProviderError(
                "The selected model request failed: "
                f"{type(err).__name__}"
            ) from err

        data = response.model_dump(exclude_none=True)
        if not isinstance(data, dict):
            raise ProviderError("The selected model returned an invalid reply")
        usage = data.get("usage")
        return data, {
            "stage": stage,
            "model": model,
            "provider": PROVIDER_DOMAIN,
            "complete_ms": round((time.monotonic() - started) * 1000, 1),
            "usage": usage if isinstance(usage, dict) else {},
        }
