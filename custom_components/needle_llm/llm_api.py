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
from .routing import (
    build_discovery_tools,
    build_routing_query,
    candidate_tool_names,
    execution_tool,
)
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
        skipped_tools: list[str] = []

        for tool in assist_api.tools:
            try:
                parameters = schema_to_json_schema(
                    tool.parameters,
                    getattr(assist_api, "custom_serializer", None),
                )
            except Exception:  # noqa: BLE001
                skipped_tools.append(tool.name)
                _LOGGER.exception(
                    "Unable to serialize Home Assistant LLM tool %s",
                    tool.name,
                )
                continue

            needle_tools.append(
                {
                    "name": tool.name,
                    "title": getattr(tool, "title", None),
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
        tools_by_serialized_name = {
            tool["name"]: tool for tool in needle_tools
        }

        diagnostics = {
            "available_tool_count": len(needle_tools),
            "skipped_tool_count": len(skipped_tools),
            "skipped_tools": skipped_tools,
        }

        discovery_tools = build_discovery_tools(needle_tools)
        routed_query = build_routing_query(query)

        diagnostics["routing_strategy"] = "two_stage_raw_arguments_v3"

        try:
            discovery_result, discovery_transport = (
                await self._client.async_complete(
                    tools=discovery_tools,
                    query=routed_query,
                    stage="discovery",
                )
            )
        except NeedleClientError as err:
            return _error(
                str(err),
                stage=err.stage,
                diagnostics={
                    **diagnostics,
                    "discovery_transport": err.as_dict(),
                },
            )

        candidates = candidate_tool_names(
            discovery_result,
            allowed_tools,
            limit=3,
        )
        diagnostics["discovery"] = {
            "candidate_tools": candidates,
            "candidate_tool_count": len(candidates),
            "transport": discovery_transport,
            "needle": _needle_diagnostics(discovery_result),
        }

        if not candidates:
            return _error(
                "Needle discovery did not identify a candidate Home Assistant tool",
                stage="discovery_validation",
                diagnostics=diagnostics,
            )

        narrowed_tools = [
            execution_tool(tools_by_serialized_name[name])
            for name in candidates
        ]

        try:
            # Discovery needs action-selection guidance. Once the native
            # Assist tools have been narrowed, give Needle only the user's
            # original request to extract target and explicitly stated values.
            # Extra instructions can be mistaken for tool arguments.
            result, transport = await self._client.async_complete(
                tools=narrowed_tools,
                query=query,
                stage="route",
            )
        except NeedleClientError as err:
            return _error(
                str(err),
                stage=err.stage,
                diagnostics={
                    **diagnostics,
                    "route_transport": err.as_dict(),
                },
            )

        diagnostics["route"] = {
            "query_mode": "original_user_request",
            "candidate_tools": candidates,
            "candidate_tool_count": len(candidates),
            "transport": transport,
            "needle": _needle_diagnostics(result),
        }

        try:
            route = approve_route(
                result,
                self._minimum_confidence,
                allowed_tools=set(candidates),
            )
        except RouteRejected as err:
            return make_tool_result(
                {
                    "executed": False,
                    "reason": str(err),
                    "confidence": result.get("confidence"),
                    "stage": "validation",
                    "device_lookup_attempted": False,
                    "guidance": (
                        "Needle did not produce an approved tool call. "
                        "No Home Assistant action or device lookup ran. "
                        "Do not conclude that the device is missing."
                    ),
                    "diagnostics": diagnostics,
                },
                error=True,
            )

        target_tool = tools_by_name.get(route.tool)
        if target_tool is None:
            return _error(
                "Needle selected a tool that is no longer available",
                stage="tool_lookup",
                diagnostics=diagnostics,
            )

        try:
            target_tool.parameters(route.arguments)
        except Exception as err:  # noqa: BLE001
            return _error(
                f"Home Assistant rejected Needle arguments: {err}",
                stage="argument_validation",
                diagnostics={
                    **diagnostics,
                    "selected_tool": route.tool,
                    "selected_arguments": route.arguments,
                },
            )

        try:
            native_result = await assist_api.async_call_tool(
                llm.ToolInput(route.tool, route.arguments)
            )
        except HomeAssistantError as err:
            return _error(
                f"Home Assistant rejected the routed tool call: {err}",
                stage="home_assistant_execution",
                diagnostics={
                    **diagnostics,
                    "selected_tool": route.tool,
                    "selected_arguments": route.arguments,
                },
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Home Assistant tool %s failed",
                route.tool,
            )
            return _error(
                f"Home Assistant tool failed: {err}",
                stage="home_assistant_execution",
                diagnostics={
                    **diagnostics,
                    "selected_tool": route.tool,
                    "selected_arguments": route.arguments,
                    "exception_type": type(err).__name__,
                },
            )

        native_data, native_error = normalize_tool_result(native_result)

        return make_tool_result(
            {
                "executed": not native_error,
                "needle_tool": route.tool,
                "needle_confidence": route.confidence,
                "arguments": route.arguments,
                "home_assistant": native_data,
                "diagnostics": {
                    **diagnostics,
                    "stage": "completed",
                    "narrowed_from": len(needle_tools),
                    "narrowed_to": len(candidates),
                    "selected_tool": route.tool,
                    "selected_arguments": route.arguments,
                },
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
                "claim success unless the result contains executed=true. "
                "If executed=false and no native Home Assistant tool ran, "
                "say that routing failed; do not invent missing devices, "
                "unavailable entities or failed entity lookups."
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


def _needle_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Extract useful Needle diagnostics without returning the whole payload."""
    return {
        "type": result.get("type"),
        "success": result.get("success"),
        "confidence": result.get("confidence"),
        "reason": result.get("reason"),
        "reasoning": result.get("reasoning"),
        "function_calls": result.get("function_calls") or [],
        "suppressed_calls": result.get("suppressed_calls") or [],
        "validation": result.get("validation") or {},
        "prefill_tps": result.get("prefill_tps"),
        "decode_tps": result.get("decode_tps"),
        "peak_ram_mb": result.get("peak_ram_mb"),
    }


def _error(
    reason: str,
    *,
    stage: str = "integration",
    diagnostics: dict[str, Any] | None = None,
) -> Any:
    """Return a standardized failed tool result with diagnostics."""
    return make_tool_result(
        {
            "executed": False,
            "stage": stage,
            "reason": reason,
            "diagnostics": diagnostics or {},
        },
        error=True,
    )
