"""Home Assistant LLM API backed by Needle."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import probatio

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import intent, llm

from .client import NeedleClient, NeedleClientError
from .const import DOMAIN, SUPPORTED_DOMAINS
from .validation import ApprovedRoute, RouteRejected, approve_route


class NeedleRouteTool(llm.Tool):
    """Route a Home Assistant device-control request through Needle."""

    name = "NeedleRoute"
    title = "Route Home Assistant control through Needle"
    description = (
        "Use this tool for Home Assistant turn on, turn off, open, close, "
        "activate and deactivate requests. Pass the user's original request "
        "unchanged in the query argument."
    )
    integration = DOMAIN
    annotations = llm.ToolAnnotations(
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    parameters = probatio.Schema(
        {
            probatio.Required(
                "query",
                description=(
                    "The user's original Home Assistant control request, unchanged"
                ),
            ): str,
        }
    )

    def __init__(
        self,
        client: NeedleClient,
        *,
        minimum_confidence: float,
        reset_before_call: bool,
    ) -> None:
        """Initialize the routing tool."""
        self._client = client
        self._minimum_confidence = minimum_confidence
        self._reset_before_call = reset_before_call

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Route and execute a Home Assistant intent."""
        query = tool_input.tool_args["query"]

        names_to_domains, area_names = _build_exposed_catalog(
            hass,
            llm_context.assistant,
        )

        if not names_to_domains:
            return _error("No supported entities are exposed to Assist")

        needle_tools = _build_needle_tools(
            names=sorted(names_to_domains),
            areas=area_names,
        )

        try:
            result = await self._client.async_complete(
                tools=needle_tools,
                query=query,
                reset_before_call=self._reset_before_call,
            )
        except NeedleClientError as err:
            return _error(str(err))

        try:
            route = approve_route(result, self._minimum_confidence)
            slots = _validated_slots(
                route,
                names_to_domains=names_to_domains,
                area_names=set(area_names),
            )
        except RouteRejected as err:
            return llm.ToolResult(
                data={
                    "executed": False,
                    "reason": str(err),
                    "confidence": result.get("confidence"),
                },
                error=True,
            )

        intent_type = (
            intent.INTENT_TURN_ON
            if route.tool == "HassTurnOn"
            else intent.INTENT_TURN_OFF
        )

        try:
            intent_response = await intent.async_handle(
                hass=hass,
                platform=llm_context.platform,
                intent_type=intent_type,
                slots={
                    key: {"value": value}
                    for key, value in slots.items()
                },
                text_input=query,
                context=llm_context.context,
                language=llm_context.language,
                assistant=llm_context.assistant,
                device_id=llm_context.device_id,
            )
        except intent.IntentError as err:
            return _error(f"Home Assistant rejected the routed intent: {err}")

        response_data = intent_response.as_dict()
        response_data.pop("language", None)
        response_data.pop("card", None)

        return llm.ToolResult(
            data={
                "executed": True,
                "needle_tool": route.tool,
                "needle_confidence": route.confidence,
                "arguments": route.arguments,
                "home_assistant": response_data,
            }
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
        reset_before_call: bool,
    ) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=api_id, name=name)
        self._client = client
        self._minimum_confidence = minimum_confidence
        self._reset_before_call = reset_before_call

    async def async_get_api_instance(
        self,
        llm_context: llm.LLMContext,
    ) -> llm.APIInstance:
        """Return the API instance for one conversation request."""
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "For Home Assistant turn on/off/open/close/activate/deactivate "
                "requests, call NeedleRoute. Pass the user's original request "
                "unchanged. Never claim that an action succeeded unless the "
                "tool result contains executed=true. If the tool rejects the "
                "request, explain briefly that the requested target could not "
                "be controlled."
            ),
            llm_context=llm_context,
            tools=[
                NeedleRouteTool(
                    self._client,
                    minimum_confidence=self._minimum_confidence,
                    reset_before_call=self._reset_before_call,
                )
            ],
        )


def api_name_for_url(base_url: str) -> str:
    """Return a stable human-readable API name."""
    parsed = urlparse(base_url)
    location = parsed.netloc or base_url
    return f"Needle LLM @ {location}"


def _build_exposed_catalog(
    hass: HomeAssistant,
    assistant: str,
) -> tuple[dict[str, set[str]], list[str]]:
    """Build a friendly-name catalog from entities exposed to Assist."""
    names_to_domains: dict[str, set[str]] = defaultdict(set)

    for state in hass.states.async_all():
        if state.domain not in SUPPORTED_DOMAINS:
            continue

        if not async_should_expose(hass, assistant, state.entity_id):
            continue

        names_to_domains[state.name].add(state.domain)

    area_registry = ar.async_get(hass)
    area_names = sorted(area.name for area in area_registry.async_list_areas())

    return dict(names_to_domains), area_names


def _build_needle_tools(
    *,
    names: list[str],
    areas: list[str],
) -> list[dict[str, Any]]:
    """Build restrictive Needle schemas from the current Assist catalog."""
    properties: dict[str, Any] = {
        "name": {
            "type": "string",
            "enum": names,
            "description": "Friendly name of an exposed Home Assistant entity",
        },
        "domain": {
            "type": "string",
            "enum": sorted(SUPPORTED_DOMAINS),
        },
    }

    if areas:
        properties["area"] = {
            "type": "string",
            "enum": areas,
        }

    parameters = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    return [
        {
            "name": "HassTurnOn",
            "description": (
                "Turn on, activate or open an exposed Home Assistant light "
                "or switch. For all matching targets in one area, use area "
                "together with domain."
            ),
            "parameters": parameters,
        },
        {
            "name": "HassTurnOff",
            "description": (
                "Turn off, deactivate or close an exposed Home Assistant light "
                "or switch. For all matching targets in one area, use area "
                "together with domain."
            ),
            "parameters": parameters,
        },
    ]


def _validated_slots(
    route: ApprovedRoute,
    *,
    names_to_domains: dict[str, set[str]],
    area_names: set[str],
) -> dict[str, Any]:
    """Apply a second validation boundary before invoking Home Assistant."""
    args = route.arguments
    unexpected = set(args) - {"name", "area", "domain"}
    if unexpected:
        raise RouteRejected(
            f"Needle returned unsupported arguments: {sorted(unexpected)}"
        )

    name = args.get("name")
    area = args.get("area")
    domain = args.get("domain")

    if name is not None and not isinstance(name, str):
        raise RouteRejected("Entity name must be a string")

    if area is not None and not isinstance(area, str):
        raise RouteRejected("Area must be a string")

    if domain is not None and not isinstance(domain, str):
        raise RouteRejected("Domain must be a string")

    if name is None and area is None:
        raise RouteRejected("Needle did not select an entity or area")

    if domain is not None and domain not in SUPPORTED_DOMAINS:
        raise RouteRejected(f"Unsupported domain: {domain}")

    if area is not None and area not in area_names:
        raise RouteRejected(f"Unknown area: {area}")

    if area is not None and name is None and domain is None:
        raise RouteRejected("Area-wide actions must include a domain")

    if name is not None:
        matching_domains = names_to_domains.get(name)
        if not matching_domains:
            raise RouteRejected("Target is not exposed to Assist")

        if domain is not None and domain not in matching_domains:
            raise RouteRejected("Target does not belong to the selected domain")

        if domain is None and len(matching_domains) != 1:
            raise RouteRejected("Entity friendly name is ambiguous")

    slots: dict[str, Any] = {}

    if name is not None:
        slots["name"] = name

    if area is not None:
        slots["area"] = area

    if domain is not None:
        # Home Assistant intent schemas expect domain to be a list.
        slots["domain"] = [domain]

    return slots


def _error(reason: str) -> llm.ToolResult:
    """Return a standardized failed tool result."""
    return llm.ToolResult(
        data={
            "executed": False,
            "reason": reason,
        },
        error=True,
    )
