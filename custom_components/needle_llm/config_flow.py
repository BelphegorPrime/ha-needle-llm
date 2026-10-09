"""Guided configuration for Needle and reusable HA model providers."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import NeedleClient, NeedleClientError
from .const import (
    BACKEND_HA_PROVIDER,
    BACKEND_NEEDLE,
    CONF_BACKEND,
    CONF_MIN_CONFIDENCE,
    CONF_PROVIDER_MODEL,
    CONF_ROUTING_STRATEGY,
    CONF_TIMEOUT,
    DEFAULT_BACKEND,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SUPPORTED_BACKENDS,
    UNSUPPORTED_LEGACY_BACKENDS,
)
from .ha_pipeline import (
    DEFAULT_STRATEGY,
    STRATEGY_MODEL_FIRST,
    STRATEGY_NEEDLE_FIRST,
)
from .ha_provider import available_provider_models

_BACKEND_LABELS = {
    BACKEND_HA_PROVIDER: "Needle + existing Home Assistant model (recommended)",
    BACKEND_NEEDLE: "Needle only (standalone)",
}
_STRATEGY_LABELS = {
    STRATEGY_MODEL_FIRST: "HA model chooses tools, Needle verifies",
    STRATEGY_NEEDLE_FIRST: "Needle chooses tools, Needle verifies",
}
_MODE_HINTS = {
    BACKEND_HA_PROVIDER: (
        "Choose the existing Home Assistant model and who preselects tools. "
        "The URL on this page is exclusively for the Needle server."
    ),
    BACKEND_NEEDLE: (
        "Needle selects the tool and generates its arguments. "
        "Only the Needle server address is required."
    ),
}


def _selector(labels: dict[str, str]) -> SelectSelector:
    """Give choices human-friendly labels rather than internal identifiers."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=value, label=label)
                for value, label in labels.items()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _mode_schema(
    backend: str, *, provider_available: bool = True
) -> vol.Schema:
    """Offer provider routing only if a compatible HA model exists."""
    labels = dict(_BACKEND_LABELS)
    if not provider_available:
        labels.pop(BACKEND_HA_PROVIDER)
    return vol.Schema(
        {
            vol.Required(
                CONF_BACKEND,
                default=backend if backend in labels else BACKEND_NEEDLE,
            ): _selector(labels)
        }
    )


def _settings_schema(
    *,
    backend: str,
    provider_choices: dict[str, str],
    values: dict,
) -> vol.Schema:
    """Second page: show *only* settings relevant to this routing mode."""
    url = values.get(CONF_URL)
    url_marker = vol.Required(CONF_URL)
    if url is not None:
        url_marker = vol.Required(CONF_URL, default=url)

    fields: dict = {}
    if backend == BACKEND_HA_PROVIDER:
        # Put the model and understandable strategy before technical URL fields.
        models = dict(provider_choices)
        old_selection = values.get(CONF_PROVIDER_MODEL)
        if models:
            selected = (
                old_selection if old_selection in models
                else next(iter(models))
            )
            fields[vol.Required(
                CONF_PROVIDER_MODEL, default=selected
            )] = _selector(models)
        else:
            fields[vol.Required(CONF_PROVIDER_MODEL)] = _selector(
                {"": "No supported model configured"}
            )
        fields[vol.Required(
            CONF_ROUTING_STRATEGY,
            default=values.get(CONF_ROUTING_STRATEGY, DEFAULT_STRATEGY),
        )] = _selector(_STRATEGY_LABELS)
        fields[url_marker] = str
        fields[vol.Required(
            CONF_MIN_CONFIDENCE,
            default=values.get(
                CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE
            ),
        )] = vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0))
    elif backend == BACKEND_NEEDLE:
        fields[url_marker] = str
        fields[vol.Required(
            CONF_MIN_CONFIDENCE,
            default=values.get(
                CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE
            ),
        )] = vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0))
    else:
        raise vol.Invalid("Unsupported routing mode; Needle is required")

    fields[vol.Required(
        CONF_TIMEOUT, default=values.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    )] = vol.All(vol.Coerce(int), vol.Range(min=1, max=600))
    return vol.Schema(fields)


def _normalize_url(value: str) -> str:
    """Normalize the backend URL."""
    return value.strip().rstrip("/")


def _entry_title(base_url: str, backend: str) -> str:
    """Describe the active mode without a technical backend name."""
    location = urlparse(base_url).netloc or base_url
    if backend == BACKEND_HA_PROVIDER:
        name = "Needle + HA model"
    else:
        name = "Needle"
    return f"{name} @ {location}"


async def _async_validate_connection(
    hass, base_url: str, request_timeout: int
) -> str | None:
    """Require a genuine Needle server; never accept model-only endpoints."""
    if not base_url.startswith(("http://", "https://")):
        return "invalid_url"
    client = NeedleClient(
        async_get_clientsession(hass), base_url, request_timeout
    )
    try:
        details = await client.async_get_model()
    except NeedleClientError:
        return "cannot_connect"
    if not isinstance(details.get("name"), str) or not details["name"]:
        return "invalid_response"
    return None


def _provider_error(hass, backend: str, selection: str) -> str | None:
    """Only accept a loaded and currently selectable model reference."""
    if backend != BACKEND_HA_PROVIDER:
        return None
    choices = available_provider_models(hass)
    if not choices:
        return "no_provider_models"
    if selection not in choices:
        return "invalid_provider_model"
    return None


def _effective_values(backend: str, submitted: dict, previous: dict) -> dict:
    """Preserve hidden fields for switching modes without re-entering them."""
    result = dict(previous)
    result.update(submitted)
    result.pop("model", None)  # Discard obsolete standalone-model setting.
    result[CONF_BACKEND] = backend
    return result


class NeedleLLMConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Guided initial setup with a mode page followed by relevant settings."""

    VERSION = 1

    def __init__(self) -> None:
        """Keep the backend selection until the connection page is submitted."""
        self._selected_backend = DEFAULT_BACKEND

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeedleLLMOptionsFlow:
        """Configure an existing connection."""
        return NeedleLLMOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select the routing mode before showing the server settings."""
        if user_input is not None:
            self._selected_backend = user_input[CONF_BACKEND]
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="user",
            data_schema=_mode_schema(
                self._selected_backend,
                provider_available=bool(available_provider_models(self.hass)),
            ),
        )

    async def async_step_settings(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure only the active backend and validate before saving."""
        errors: dict[str, str] = {}
        backend = self._selected_backend
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            provider_model = user_input.get(CONF_PROVIDER_MODEL, "")
            error = _provider_error(self.hass, backend, provider_model)
            if error is None:
                error = await _async_validate_connection(
                    self.hass, url, user_input[CONF_TIMEOUT]
                )
            if error is not None:
                errors["base"] = error
            else:
                unique_id = (
                    url if backend == BACKEND_NEEDLE
                    else f"{backend}:{url}:{provider_model}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(url, backend),
                    data=_effective_values(
                        backend,
                        {**user_input, CONF_URL: url},
                        {
                            CONF_MIN_CONFIDENCE: DEFAULT_MIN_CONFIDENCE,
                            CONF_ROUTING_STRATEGY: DEFAULT_STRATEGY,
                            CONF_PROVIDER_MODEL: "",
                        },
                    ),
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(
                backend=backend,
                provider_choices=available_provider_models(self.hass),
                values=user_input or {},
            ),
            errors=errors,
            description_placeholders={
                "mode": _BACKEND_LABELS[backend],
                "hint": _MODE_HINTS[backend],
            },
        )


class NeedleLLMOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Guided options flow preserving old, unselected settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Keep the currently configured mode and server settings."""
        super().__init__(config_entry)
        self._current = {
            **config_entry.data,
            **config_entry.options,
        }
        saved_backend = self._current.get(CONF_BACKEND, DEFAULT_BACKEND)
        self._selected_backend = (
            saved_backend if saved_backend in SUPPORTED_BACKENDS
            else BACKEND_HA_PROVIDER
        )
        if saved_backend in UNSUPPORTED_LEGACY_BACKENDS:
            # The old direct-model URL is not a Needle URL. Never prefill it.
            self._current.pop(CONF_URL, None)

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """First choose which routing strategy family to configure."""
        if user_input is not None:
            self._selected_backend = user_input[CONF_BACKEND]
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="init",
            data_schema=_mode_schema(self._selected_backend),
        )

    async def async_step_settings(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Only show fields relevant to the selected mode."""
        errors: dict[str, str] = {}
        backend = self._selected_backend
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            provider_model = user_input.get(
                CONF_PROVIDER_MODEL,
                self._current.get(CONF_PROVIDER_MODEL, ""),
            )
            error = _provider_error(self.hass, backend, provider_model)
            if error is None:
                error = await _async_validate_connection(
                    self.hass, url, user_input[CONF_TIMEOUT]
                )
            if error is not None:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="",
                    data=_effective_values(
                        backend, {**user_input, CONF_URL: url}, self._current
                    ),
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(
                backend=backend,
                provider_choices=available_provider_models(self.hass),
                values=_effective_values(
                    backend, user_input or {}, self._current
                ),
            ),
            errors=errors,
            description_placeholders={
                "mode": _BACKEND_LABELS[backend],
                "hint": _MODE_HINTS[backend],
            },
        )
