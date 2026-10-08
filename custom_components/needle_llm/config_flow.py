"""Config and options flow for Needle and reusable model providers."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NeedleClient, NeedleClientError
from .const import (
    BACKEND_HA_PROVIDER,
    BACKEND_NEEDLE,
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
from .ha_pipeline import DEFAULT_STRATEGY, STRATEGIES
from .ha_provider import available_provider_models
from .openai_client import OpenAICompatibleClient, OpenAICompatibleClientError


def _normalize_url(value: str) -> str:
    """Normalize a server base URL."""
    return value.strip().rstrip("/")


def _entry_title(base_url: str, backend: str) -> str:
    """Create a readable entry name."""
    parsed = urlparse(base_url)
    location = parsed.netloc or base_url
    if backend == BACKEND_HA_PROVIDER:
        label = "Needle + HA model"
    elif backend == BACKEND_OPENAI_COMPATIBLE:
        label = "OpenAI-compatible"
    else:
        label = "Needle"
    return f"{label} @ {location}"


def _schema(
    *,
    url: str | None = None,
    backend: str = DEFAULT_BACKEND,
    model: str = "",
    provider_model: str = "",
    provider_choices: dict[str, str] | None = None,
    routing_strategy: str = DEFAULT_STRATEGY,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    timeout: int = DEFAULT_TIMEOUT,
) -> vol.Schema:
    """Build the form, selecting existing HA model subentries by their IDs."""
    url_marker = vol.Required(CONF_URL)
    if url is not None:
        url_marker = vol.Required(CONF_URL, default=url)
    choices = {"": "Select a configured model"}
    choices.update(provider_choices or {})
    if provider_model and provider_model not in choices:
        choices[provider_model] = "Previously selected (now unavailable)"

    return vol.Schema(
        {
            vol.Required(CONF_BACKEND, default=backend): vol.In(
                [BACKEND_NEEDLE, BACKEND_HA_PROVIDER, BACKEND_OPENAI_COMPATIBLE]
            ),
            url_marker: str,
            vol.Optional(CONF_PROVIDER_MODEL, default=provider_model): vol.In(
                choices
            ),
            vol.Optional(
                CONF_ROUTING_STRATEGY, default=routing_strategy
            ): vol.In(STRATEGIES),
            vol.Optional(CONF_MODEL, default=model): str,
            vol.Required(
                CONF_MIN_CONFIDENCE, default=min_confidence
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Required(CONF_TIMEOUT, default=timeout): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=600)
            ),
        }
    )


async def _async_validate_connection(
    hass,
    base_url: str,
    request_timeout: int,
    backend: str,
    model: str,
) -> str | None:
    """Validate Needle or direct OpenAI HTTP endpoint as appropriate."""
    if not base_url.startswith(("http://", "https://")):
        return "invalid_url"

    if backend == BACKEND_OPENAI_COMPATIBLE:
        client = OpenAICompatibleClient(
            async_get_clientsession(hass), base_url, request_timeout, model
        )
    else:
        # The provider-backed mode still requires Needle for approval.
        client = NeedleClient(
            async_get_clientsession(hass), base_url, request_timeout
        )

    try:
        model_info = await client.async_get_model()
    except (NeedleClientError, OpenAICompatibleClientError):
        return "cannot_connect"

    if not isinstance(model_info.get("name"), str) or not model_info["name"]:
        return "invalid_response"
    return None


def _provider_error(hass, backend: str, selected: str) -> str | None:
    """Require an explicitly selected, currently loaded model provider."""
    if backend != BACKEND_HA_PROVIDER:
        return None
    choices = available_provider_models(hass)
    if not choices:
        return "no_provider_models"
    if not selected or selected not in choices:
        return "invalid_provider_model"
    return None


class NeedleLLMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one Needle or provider-backed routing entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeedleLLMOptionsFlow:
        """Configure an existing routing entry."""
        return NeedleLLMOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_URL])
            backend = normalize_backend(
                user_input.get(CONF_BACKEND, DEFAULT_BACKEND)
            )
            model = user_input.get(CONF_MODEL, "")
            provider_model = user_input.get(CONF_PROVIDER_MODEL, "")
            strategy = user_input.get(
                CONF_ROUTING_STRATEGY, DEFAULT_STRATEGY
            )
            request_timeout = user_input[CONF_TIMEOUT]
            error = _provider_error(self.hass, backend, provider_model)
            if error is None:
                error = await _async_validate_connection(
                    self.hass, base_url, request_timeout, backend, model
                )

            if error is not None:
                errors["base"] = error
            else:
                identity = (
                    base_url if backend == BACKEND_NEEDLE
                    else f"{backend}:{base_url}:{provider_model}"
                )
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(base_url, backend),
                    data={
                        CONF_BACKEND: backend,
                        CONF_URL: base_url,
                        CONF_MODEL: model,
                        CONF_PROVIDER_MODEL: provider_model,
                        CONF_ROUTING_STRATEGY: strategy,
                        CONF_MIN_CONFIDENCE: user_input[CONF_MIN_CONFIDENCE],
                        CONF_TIMEOUT: request_timeout,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(
                provider_choices=available_provider_models(self.hass)
            ),
            errors=errors,
        )


class NeedleLLMOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Edit routing without duplicating provider API keys or connection URLs."""

    async def async_step_init(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Edit the active routing backend."""
        errors: dict[str, str] = {}
        saved = self.config_entry.data
        options = self.config_entry.options

        current_backend = normalize_backend(
            options.get(CONF_BACKEND, saved.get(CONF_BACKEND, DEFAULT_BACKEND))
        )
        current_url = options.get(CONF_URL, saved[CONF_URL])
        current_model = options.get(CONF_MODEL, saved.get(CONF_MODEL, ""))
        current_provider = options.get(
            CONF_PROVIDER_MODEL, saved.get(CONF_PROVIDER_MODEL, "")
        )
        current_strategy = options.get(
            CONF_ROUTING_STRATEGY,
            saved.get(CONF_ROUTING_STRATEGY, DEFAULT_STRATEGY),
        )
        current_confidence = options.get(
            CONF_MIN_CONFIDENCE,
            saved.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
        )
        current_timeout = options.get(
            CONF_TIMEOUT, saved.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        )

        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_URL])
            backend = normalize_backend(
                user_input.get(CONF_BACKEND, DEFAULT_BACKEND)
            )
            model = user_input.get(CONF_MODEL, "")
            provider_model = user_input.get(CONF_PROVIDER_MODEL, "")
            strategy = user_input.get(
                CONF_ROUTING_STRATEGY, DEFAULT_STRATEGY
            )
            request_timeout = user_input[CONF_TIMEOUT]
            error = _provider_error(self.hass, backend, provider_model)
            if error is None:
                error = await _async_validate_connection(
                    self.hass, base_url, request_timeout, backend, model
                )
            if error is not None:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_BACKEND: backend,
                        CONF_URL: base_url,
                        CONF_MODEL: model,
                        CONF_PROVIDER_MODEL: provider_model,
                        CONF_ROUTING_STRATEGY: strategy,
                        CONF_MIN_CONFIDENCE: user_input[CONF_MIN_CONFIDENCE],
                        CONF_TIMEOUT: request_timeout,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                backend=current_backend,
                url=current_url,
                model=current_model,
                provider_model=current_provider,
                provider_choices=available_provider_models(self.hass),
                routing_strategy=current_strategy,
                min_confidence=current_confidence,
                timeout=current_timeout,
            ),
            errors=errors,
        )
