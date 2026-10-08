"""Needle LLM integration for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NeedleClient
from .compat import register_api_compat
from .const import (
    CONF_MIN_CONFIDENCE,
    CONF_TIMEOUT,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .llm_api import NeedleAPI, api_id_for_url, api_name_for_url


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Needle LLM from a config entry."""
    identity_url = entry.data[CONF_URL]
    base_url = entry.options.get(CONF_URL, identity_url)
    timeout = entry.options.get(
        CONF_TIMEOUT,
        entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )
    minimum_confidence = entry.options.get(
        CONF_MIN_CONFIDENCE,
        entry.data.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
    )

    client = NeedleClient(
        async_get_clientsession(hass),
        base_url,
        timeout,
    )

    api = NeedleAPI(
        hass,
        api_id=api_id_for_url(identity_url),
        name=api_name_for_url(base_url),
        client=client,
        minimum_confidence=minimum_confidence,
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
    """Unload a Needle LLM config entry."""
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
    """Reload Needle LLM after configuration options change."""
    await hass.config_entries.async_reload(entry.entry_id)
