"""Config flow for Needle LLM."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
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


def _schema(
    *,
    url: str | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    timeout: int = DEFAULT_TIMEOUT,
) -> vol.Schema:
    """Return the shared configuration schema."""
    url_marker = vol.Required(CONF_URL)
    if url is not None:
        url_marker = vol.Required(CONF_URL, default=url)

    return vol.Schema(
        {
            url_marker: str,
            vol.Required(
                CONF_MIN_CONFIDENCE,
                default=min_confidence,
            ): vol.All(
                vol.Coerce(float),
                vol.Range(min=0.0, max=1.0),
            ),
            vol.Required(
                CONF_TIMEOUT,
                default=timeout,
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=600),
            ),
        }
    )


async def _async_validate_connection(
    hass,
    base_url: str,
    request_timeout: int,
) -> str | None:
    """Validate a Needle server and return an error key on failure."""
    if not (
        base_url.startswith("http://")
        or base_url.startswith("https://")
    ):
        return "invalid_url"

    client = NeedleClient(
        async_get_clientsession(hass),
        base_url,
        request_timeout,
    )

    try:
        model_info = await client.async_get_model()
    except NeedleClientError:
        return "cannot_connect"

    model_name = model_info.get("name")
    if not isinstance(model_name, str) or not model_name:
        return "invalid_response"

    return None


class NeedleLLMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Needle LLM."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeedleLLMOptionsFlow:
        """Return the options flow."""
        return NeedleLLMOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_URL])
            timeout = user_input[CONF_TIMEOUT]
            error = await _async_validate_connection(
                self.hass,
                base_url,
                timeout,
            )

            if error is not None:
                errors["base"] = error
            else:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()

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
            data_schema=_schema(),
            errors=errors,
        )


class NeedleLLMOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Allow an existing Needle LLM entry to be changed."""

    async def async_step_init(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Edit Needle connection and routing options."""
        errors: dict[str, str] = {}

        current_url = self.config_entry.options.get(
            CONF_URL,
            self.config_entry.data[CONF_URL],
        )
        current_confidence = self.config_entry.options.get(
            CONF_MIN_CONFIDENCE,
            self.config_entry.data.get(
                CONF_MIN_CONFIDENCE,
                DEFAULT_MIN_CONFIDENCE,
            ),
        )
        current_timeout = self.config_entry.options.get(
            CONF_TIMEOUT,
            self.config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )

        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_URL])
            timeout = user_input[CONF_TIMEOUT]
            error = await _async_validate_connection(
                self.hass,
                base_url,
                timeout,
            )

            if error is not None:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_URL: base_url,
                        CONF_MIN_CONFIDENCE: user_input[
                            CONF_MIN_CONFIDENCE
                        ],
                        CONF_TIMEOUT: timeout,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                url=current_url,
                min_confidence=current_confidence,
                timeout=current_timeout,
            ),
            errors=errors,
        )
