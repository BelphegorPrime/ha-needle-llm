"""Compatibility helpers across Home Assistant LLM API generations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

_ROUTE_QUERY_DESCRIPTION = (
    "The user's original Home Assistant request. Preserve device, entity, area, "
    "floor and action wording as closely as possible."
)


def route_parameters_schema() -> Any:
    """Return a Tool parameter schema compatible with this HA release."""
    if type(llm.Tool.parameters).__module__.startswith("probatio"):
        import probatio

        return probatio.Schema(
            {
                probatio.Required(
                    "query",
                    description=_ROUTE_QUERY_DESCRIPTION,
                ): str,
            }
        )

    return vol.Schema(
        {
            vol.Required(
                "query",
                description=_ROUTE_QUERY_DESCRIPTION,
            ): str,
        }
    )


def schema_to_json_schema(
    schema: Any,
    custom_serializer: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Convert a Home Assistant tool schema to JSON Schema."""
    if type(schema).__module__.startswith("probatio"):
        import probatio

        result = probatio.to_openapi(
            schema,
            custom_serializer=custom_serializer,
        )
    else:
        from voluptuous_openapi import convert

        if custom_serializer is None:
            result = convert(schema)
        else:
            try:
                result = convert(schema, custom_serializer=custom_serializer)
            except TypeError:
                result = convert(schema)

    if not isinstance(result, dict):
        raise ValueError("Home Assistant returned a non-object tool schema")

    return result


def make_tool_result(data: dict[str, Any], *, error: bool = False) -> Any:
    """Return the native ToolResult when available, else a legacy JSON object."""
    tool_result = getattr(llm, "ToolResult", None)
    if tool_result is not None:
        return tool_result(data=data, error=error)

    if error:
        data = {**data, "error": True}
    return data


def register_api_compat(
    hass: HomeAssistant,
    api: llm.API,
) -> Callable[[], None]:
    """Register an API and return an unregister callback on old and new HA."""
    unregister = llm.async_register_api(hass, api)
    if callable(unregister):
        return unregister

    def _unregister_legacy() -> None:
        registry_getter = getattr(llm, "_async_get_apis", None)
        if registry_getter is None:
            return
        registry = registry_getter(hass)
        if isinstance(registry, dict):
            registry.pop(api.id, None)

    return _unregister_legacy


def normalize_tool_result(result: Any) -> tuple[dict[str, Any], bool]:
    """Normalize legacy dict and modern ToolResult return values."""
    if hasattr(result, "data"):
        data = result.data
        error = bool(getattr(result, "error", False))
    else:
        data = result
        error = bool(data.get("error", False)) if isinstance(data, dict) else False

    if not isinstance(data, dict):
        return {"result": data}, error

    return dict(data), error
