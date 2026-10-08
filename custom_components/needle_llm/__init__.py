"""Needle / OpenAI-compatible tool router integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NeedleClient
from .compat import register_api_compat
from .const import (
    BACKEND_OPENAI_COMPATIBLE,
    CONF_BACKEND,
    CONF_MIN_CONFIDENCE,
    CONF_MODEL,
    CONF_PROVIDER_MODEL,
    CONF_ROUTING_STRATEGY,
    CONF_TIMEOUT,
    DEFAULT_BACKEND,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    normalize_backend,
)
from .ha_pipeline import DEFAULT_STRATEGY
from .llm_api import NeedleAPI, api_id_for_url, api_name_for_url
from .openai_client import OpenAICompatibleClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Register the routing API for the configured backend."""
    identity_url = entry.data[CONF_URL]
    base_url = entry.options.get(CONF_URL, identity_url)
    backend = normalize_backend(
        entry.options.get(
            CONF_BACKEND, entry.data.get(CONF_BACKEND, DEFAULT_BACKEND)
        )
    )
    model = entry.options.get(CONF_MODEL, entry.data.get(CONF_MODEL, ""))
    provider_model = entry.options.get(
        CONF_PROVIDER_MODEL, entry.data.get(CONF_PROVIDER_MODEL, "")
    )
    strategy = entry.options.get(
        CONF_ROUTING_STRATEGY,
        entry.data.get(CONF_ROUTING_STRATEGY, DEFAULT_STRATEGY),
    )
    request_timeout = entry.options.get(
        CONF_TIMEOUT,
        entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )
    minimum_confidence = entry.options.get(
        CONF_MIN_CONFIDENCE,
        entry.data.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
    )

    if backend == BACKEND_OPENAI_COMPATIBLE:
        client = OpenAICompatibleClient(
            async_get_clientsession(hass), base_url, request_timeout, model
        )
    else:
        client = NeedleClient(
            async_get_clientsession(hass), base_url, request_timeout
        )

    api = NeedleAPI(
        hass,
        api_id=api_id_for_url(identity_url),
        name=api_name_for_url(base_url, backend=backend),
        client=client,
        backend=backend,
        minimum_confidence=minimum_confidence,
        provider_model=provider_model,
        routing_strategy=strategy,
        timeout=request_timeout,
    )

    unregister = register_api_compat(hass, api)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "api": api,
        "unregister": unregister,
    }
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unregister the selected tool router."""
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if entry_data is not None:
        entry_data["unregister"]()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload the backend when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
