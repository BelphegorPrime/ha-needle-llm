"""Config flow for Needle LLM."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NeedleClient, NeedleClientError
from .const import (
    CONF_MIN_CONFIDENCE,
    CONF_TIMEOUT,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


def _normalize_url(value: str) -> str:
    """Normalize the configured Needle base URL."""
    return value.strip().rstrip("/")


def _entry_title(base_url: str) -> str:
    """Return a readable config-entry title."""
    parsed = urlparse(base_url)
    location = parsed.netloc or base_url
    return f"Needle @ {location}"


class NeedleLLMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Needle LLM."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_URL])
            timeout = user_input[CONF_TIMEOUT]

            if not (
                base_url.startswith("http://")
                or base_url.startswith("https://")
            ):
                errors["base"] = "invalid_url"
            else:
                client = NeedleClient(
                    async_get_clientsession(self.hass),
                    base_url,
                    timeout,
                )

                try:
                    model_info = await client.async_get_model()
                except NeedleClientError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(base_url)
                    self._abort_if_unique_id_configured()

                    model_name = model_info.get("name")
                    if not isinstance(model_name, str) or not model_name:
                        errors["base"] = "invalid_response"
                    else:
                        return self.async_create_entry(
                            title=_entry_title(base_url),
                            data={
                                CONF_URL: base_url,
                                CONF_MIN_CONFIDENCE: user_input[
                                    CONF_MIN_CONFIDENCE
                                ],
                                CONF_TIMEOUT: timeout,
                            },
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL): str,
                    vol.Required(
                        CONF_MIN_CONFIDENCE,
                        default=DEFAULT_MIN_CONFIDENCE,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=0.0, max=1.0),
                    ),
                    vol.Required(
                        CONF_TIMEOUT,
                        default=DEFAULT_TIMEOUT,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1, max=300),
                    ),
                }
            ),
            errors=errors,
        )
