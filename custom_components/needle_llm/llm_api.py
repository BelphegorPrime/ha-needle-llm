"""Home Assistant LLM API backed by Needle."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .client import NeedleClient, NeedleClientError
from .compat import (
    make_tool_result,
    normalize_tool_result,
    route_parameters_schema,
    schema_to_json_schema,
)
from .const import DOMAIN
from .validation import RouteRejected, approve_route

_LOGGER = logging.getLogger(__name__)


class NeedleRouteTool(llm.Tool):
    """Route a Home Assistant request through Needle."""

    name = "NeedleRoute"
    title = "Route Home Assistant request through Needle"
    description = (
        "Use this for Home Assistant requests that need a Home Assistant tool, "
        "including device control, live state queries, scripts, timers, climate, "
        "lights, covers, media and other capabilities exposed by the installed "
        "Home Assistant Assist API. Pass the user's original request unchanged."
    )
    integration = DOMAIN
    parameters = route_parameters_schema()

    def __init__(
        self,
        client: NeedleClient,
        *,
        minimum_confidence: float,
    ) -> None:
        """Initialize the routing tool."""
        self._client = client
        self._minimum_confidence = minimum_confidence

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> Any:
        """Route one request to the native Home Assistant Assist API."""
        query = tool_input.tool_args["query"]

        try:
            assist_api = await llm.async_get_api(
                hass,
                llm.LLM_API_ASSIST,
                llm_context,
            )
        except HomeAssistantError as err:
            return _error(f"Home Assistant Assist API is unavailable: {err}")

        tools_by_name = {tool.name: tool for tool in assist_api.tools}
        needle_tools: list[dict[str, Any]] = []

        for tool in assist_api.tools:
            try:
                parameters = schema_to_json_schema(
                    tool.parameters,
                    getattr(assist_api, "custom_serializer", None),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Unable to serialize Home Assistant LLM tool %s",
                    tool.name,
                )
                continue

            needle_tools.append(
                {
                    "name": tool.name,
                    "description": (
                        tool.description
                        or f"Execute Home Assistant tool {tool.name}"
                    ),
                    "parameters": parameters,
                }
            )

        if not needle_tools:
            return _error("Home Assistant exposed no compatible Assist tools")

        allowed_tools = {tool["name"] for tool in needle_tools}

        try:
            result = await self._client.async_complete(
                tools=needle_tools,
                query=query,
            )
        except NeedleClientError as err:
            return _error(str(err))

        try:
            route = approve_route(
                result,
                self._minimum_confidence,
                allowed_tools=allowed_tools,
            )
        except RouteRejected as err:
            return make_tool_result(
                {
                    "executed": False,
                    "reason": str(err),
                    "confidence": result.get("confidence"),
                },
                error=True,
            )

        target_tool = tools_by_name.get(route.tool)
        if target_tool is None:
            return _error("Needle selected a tool that is no longer available")

        try:
            target_tool.parameters(route.arguments)
        except Exception as err:  # noqa: BLE001
            return _error(f"Home Assistant rejected Needle arguments: {err}")

        try:
            native_result = await assist_api.async_call_tool(
                llm.ToolInput(route.tool, route.arguments)
            )
        except HomeAssistantError as err:
            return _error(f"Home Assistant rejected the routed tool call: {err}")
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Home Assistant tool %s failed",
                route.tool,
            )
            return _error(f"Home Assistant tool failed: {err}")

        native_data, native_error = normalize_tool_result(native_result)

        return make_tool_result(
            {
                "executed": not native_error,
                "needle_tool": route.tool,
                "needle_confidence": route.confidence,
                "arguments": route.arguments,
                "home_assistant": native_data,
            },
            error=native_error,
        )


if hasattr(llm, "ToolAnnotations"):
    NeedleRouteTool.annotations = llm.ToolAnnotations(
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


class NeedleAPI(llm.API):
    """LLM API that exposes the Needle router."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_id: str,
        name: str,
        client: NeedleClient,
        minimum_confidence: float,
    ) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=api_id, name=name)
        self._client = client
        self._minimum_confidence = minimum_confidence

    async def async_get_api_instance(
        self,
        llm_context: llm.LLMContext,
    ) -> llm.APIInstance:
        """Return the API instance for one conversation request."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "For any request about Home Assistant that requires current "
                "home data or an action, call NeedleRoute. Pass the user's "
                "original request unchanged. NeedleRoute delegates only to "
                "tools provided by Home Assistant's native Assist API. Never "
                "claim success unless the result contains executed=true."
            ),
            llm_context=llm_context,
            tools=[
                NeedleRouteTool(
                    self._client,
                    minimum_confidence=self._minimum_confidence,
                )
            ],
        )


def api_id_for_url(base_url: str) -> str:
    """Return a deterministic API ID for a configured Needle server."""
    digest = hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]
    return f"{DOMAIN}_{digest}"


def api_name_for_url(base_url: str) -> str:
    """Return a stable human-readable API name."""
    parsed = urlparse(base_url)
    location = parsed.netloc or base_url
    return f"Needle LLM @ {location}"


def _error(reason: str) -> Any:
    """Return a standardized failed tool result."""
    return make_tool_result(
        {
            "executed": False,
            "reason": reason,
        },
        error=True,
    )
