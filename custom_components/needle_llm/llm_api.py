"""Home Assistant Assist tool routing with optional HA model provider."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any
from urllib.parse import urlparse

from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
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
from .const import (
    BACKEND_HA_PROVIDER,
    BACKEND_NEEDLE,
    DEFAULT_BACKEND,
    DOMAIN,
)
from .ha_pipeline import (
    DEFAULT_STRATEGY,
    PipelineRejected,
    async_provider_route,
)
from .ha_provider import HomeAssistantModelProvider
from .routing import (
    build_discovery_tools,
    build_routing_query,
    candidate_tool_names,
    execution_tool,
)
from .target_guard import (
    TargetGuardRejected,
    find_unique_mentioned_entity,
    reconcile_named_target,
)
from .trace import build_routing_trace
from .validation import (
    RouteRejected,
    approve_route,
)

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
        backend: str,
        minimum_confidence: float,
        provider: HomeAssistantModelProvider | None = None,
        routing_strategy: str = DEFAULT_STRATEGY,
    ) -> None:
        """Initialize the routing tool."""
        self._client = client
        self._backend = backend
        self._minimum_confidence = minimum_confidence
        self._provider = provider
        self._routing_strategy = routing_strategy

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> Any:
        """Route one request to the native Home Assistant Assist API."""
        request_started = time.monotonic()
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
            "backend": self._backend,
            "request_language": llm_context.language,
            "minimum_confidence": self._minimum_confidence,
            "available_tool_count": len(needle_tools),
            "skipped_tool_count": len(skipped_tools),
            "skipped_tools": skipped_tools,
        }

        if self._backend == BACKEND_HA_PROVIDER:
            if self._provider is None or not isinstance(
                self._client, NeedleClient
            ):
                return _error(
                    "The Needle model provider is not configured",
                    stage="configuration",
                    diagnostics=diagnostics,
                )
            try:
                proposal = await async_provider_route(
                    hass=hass,
                    context=llm_context,
                    query=query,
                    tools=needle_tools,
                    needle=self._client,
                    provider=self._provider,
                    strategy=self._routing_strategy,
                    minimum_confidence=self._minimum_confidence,
                    diagnostics=diagnostics,
                )
            except PipelineRejected as err:
                return _error(
                    err.reason,
                    stage=err.stage,
                    diagnostics=diagnostics,
                )
            route = proposal.route
            candidates = [proposal.candidate]
            matched_target = proposal.matched_target
            removed_fields = proposal.removed_fields
        else:
            discovery_tools = build_discovery_tools(needle_tools)
            routed_query = build_routing_query(query)

            diagnostics["routing_strategy"] = "two_stage_unique_target_schema_v5"

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

            # Only simplify optional targeting fields when the original utterance
            # literally names exactly one entity exposed to this Assist assistant.
            # No device type, translated noun, or fuzzy entity guess is assumed.
            mentioned_entities: list[dict[str, Any]] = []
            for state in hass.states.async_all():
                if not async_should_expose(
                    hass, llm_context.assistant, state.entity_id
                ):
                    continue
                mentioned_entities.append(
                    {
                        "name": state.name,
                        "entity_id": state.entity_id,
                        "device_class": state.attributes.get("device_class"),
                    }
                )

            matched_target = find_unique_mentioned_entity(
                query, mentioned_entities
            )
            narrowed_tools = [
                execution_tool(
                    tools_by_serialized_name[name],
                    unique_named_target=matched_target is not None,
                )
                for name in candidates
            ]
            removed_fields = [
                {
                    "tool": name,
                    "fields": ["device_class"],
                }
                for name, narrowed in zip(
                    candidates, narrowed_tools, strict=True
                )
                if "device_class"
                in tools_by_serialized_name[name]["parameters"].get(
                    "properties", {}
                )
                and "device_class" not in narrowed["parameters"].get(
                    "properties", {}
                )
            ]
            diagnostics["target_schema"] = {
                "unique_exposed_name_match": matched_target is not None,
                "matched_entity_id": (
                    matched_target["entity_id"] if matched_target else None
                ),
                "removed_optional_fields": removed_fields,
            }

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
                        "routing_trace": build_routing_trace(
                            diagnostics,
                            status="rejected",
                            stage="validation",
                        ),
                        "diagnostics": diagnostics,
                    },
                    error=True,
                )

        target_tool = tools_by_name.get(route.tool)
        if target_tool is None:
            return _error(
                "The model selected a tool that is no longer available",
                stage="tool_lookup",
                diagnostics=diagnostics,
            )

        arguments = route.arguments
        if matched_target is not None and removed_fields:
            # The simplified schema may only be used for this exact target.
            # Never allow an unrelated name/area to gain execution authority.
            intended_name = matched_target["name"]
            supplied_name = arguments.get("name")
            if (
                not isinstance(supplied_name, str)
                or " ".join(supplied_name.casefold().split())
                != " ".join(intended_name.casefold().split())
            ):
                return _error(
                    "The model's target name differs from the uniquely matched "
                    "exposed entity; no action was executed",
                    stage="target_validation",
                    diagnostics={
                        **diagnostics,
                        "selected_tool": route.tool,
                        "selected_arguments": arguments,
                    },
                )
        if matched_target is not None and removed_fields:
            actual_domain = matched_target["entity_id"].split(".", 1)[0]
            if "domain" in arguments:
                value = arguments["domain"]
                domains = value if isinstance(value, list) else [value]
                if domains != [actual_domain]:
                    return _error(
                        "The model's domain conflicts with the named exposed entity",
                        stage="target_validation",
                        diagnostics={
                            **diagnostics,
                            "selected_tool": route.tool,
                            "selected_arguments": arguments,
                        },
                    )
            elif "domain" in tools_by_serialized_name[route.tool][
                "parameters"
            ].get("properties", {}):
                arguments = {**arguments, "domain": [actual_domain]}

        if "device_class" in arguments:
            name = arguments.get("name")
            exposed_entities: list[dict[str, Any]] = []
            if isinstance(name, str) and name.strip():
                for state in hass.states.async_all():
                    if state.name.casefold() != name.casefold():
                        continue
                    if not async_should_expose(
                        hass, llm_context.assistant, state.entity_id
                    ):
                        continue
                    exposed_entities.append(
                        {
                            "name": state.name,
                            "entity_id": state.entity_id,
                            "device_class": state.attributes.get("device_class"),
                        }
                    )

            try:
                arguments, target_guard = reconcile_named_target(
                    query,
                    arguments,
                    tools_by_serialized_name[route.tool]["parameters"],
                    exposed_entities,
                )
            except TargetGuardRejected as err:
                return _error(
                    f"The model's entity filters are contradictory: {err}",
                    stage="target_validation",
                    diagnostics={
                        **diagnostics,
                        "selected_tool": route.tool,
                        "selected_arguments": route.arguments,
                    },
                )
            diagnostics["target_guard"] = target_guard

        try:
            target_tool.parameters(arguments)
        except Exception as err:  # noqa: BLE001
            return _error(
                f"Home Assistant rejected routed arguments: {err}",
                stage="argument_validation",
                diagnostics={
                    **diagnostics,
                    "selected_tool": route.tool,
                    "selected_arguments": arguments,
                },
            )

        execution_started = time.monotonic()
        try:
            native_result = await assist_api.async_call_tool(
                llm.ToolInput(route.tool, arguments)
            )
        except HomeAssistantError as err:
            diagnostics["ha_execution_ms"] = round(
                (time.monotonic() - execution_started) * 1000, 1
            )
            return _error(
                f"Home Assistant rejected the routed tool call: {err}",
                stage="home_assistant_execution",
                diagnostics={
                    **diagnostics,
                    "selected_tool": route.tool,
                    "selected_arguments": arguments,
                },
            )
        except Exception as err:  # noqa: BLE001
            diagnostics["ha_execution_ms"] = round(
                (time.monotonic() - execution_started) * 1000, 1
            )
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
                    "selected_arguments": arguments,
                    "exception_type": type(err).__name__,
                },
            )

        diagnostics["ha_execution_ms"] = round(
            (time.monotonic() - execution_started) * 1000, 1
        )
        native_data, native_error = normalize_tool_result(native_result)
        elapsed_ms = round(
            (time.monotonic() - request_started) * 1000, 1
        )
        routing_trace = build_routing_trace(
            diagnostics,
            status="failed" if native_error else "success",
            stage="home_assistant_execution" if native_error else "completed",
            selected_tool=route.tool,
            arguments=arguments,
            home_assistant=native_data,
            total_ms=elapsed_ms,
        )

        return make_tool_result(
            {
                "executed": not native_error,
                "router_backend": self._backend,
                "selected_tool": route.tool,
                "confidence": route.confidence,
                **(
                    {"needle_confidence": route.confidence}
                    if self._backend == BACKEND_NEEDLE
                    else {
                        "needle_confidence": diagnostics.get(
                            "needle_approval", {}
                        ).get("confidence")
                    }
                ),
                "arguments": arguments,
                "home_assistant": native_data,
                "routing_trace": routing_trace,
                "diagnostics": {
                    **diagnostics,
                    "stage": "completed",
                    "total_ms": elapsed_ms,
                    "narrowed_from": len(needle_tools),
                    "narrowed_to": (
                        len(candidates)
                        if self._backend == BACKEND_NEEDLE
                        else len(needle_tools)
                    ),
                    "selected_tool": route.tool,
                    "selected_arguments": arguments,
                    "backend": self._backend,
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


class NeedleVerifiedRouteTool(NeedleRouteTool):
    """Route through an existing HA model with compulsory Needle approval."""

    name = "NeedleVerifiedRoute"
    title = "Home Assistant model routing verified by Needle"
    description = (
        "Use this tool for Home Assistant control or state requests when "
        "Needle Verified Routing is selected. It provides full per-stage "
        "routing details and performs native Assist actions only after "
        "Needle approval. Pass the original user request unchanged."
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
        backend: str,
        minimum_confidence: float,
        provider_model: str = "",
        routing_strategy: str = DEFAULT_STRATEGY,
        timeout: int = 30,
    ) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=api_id, name=name)
        self._client = client
        self._backend = backend
        self._minimum_confidence = minimum_confidence
        self._routing_strategy = routing_strategy
        self._provider = (
            HomeAssistantModelProvider(hass, provider_model, timeout)
            if backend == BACKEND_HA_PROVIDER
            else None
        )

    async def async_get_api_instance(
        self,
        llm_context: llm.LLMContext,
    ) -> llm.APIInstance:
        """Return the API instance for one conversation request."""
        if self._backend == BACKEND_HA_PROVIDER:
            tool_cls = NeedleVerifiedRouteTool
        else:
            tool_cls = NeedleRouteTool
        tool_name = tool_cls.name
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "For any request about Home Assistant that requires current "
                f"home data or an action, call {tool_name}. Pass the user's "
                "original request unchanged. The route tool delegates only to "
                "tools provided by Home Assistant's native Assist API. Never "
                "claim success unless the result contains executed=true. "
                "If executed=false and no native Home Assistant tool ran, "
                "say that routing failed; do not invent missing devices, "
                "unavailable entities or failed entity lookups. If Needle "
                "rejected a correct tool proposal solely because its confidence "
                "was below threshold, explain the confidence failure instead "
                "of claiming the request was ambiguous or multiple devices "
                "were detected."
            ),
            llm_context=llm_context,
            tools=[
                tool_cls(
                    self._client,
                    backend=self._backend,
                    minimum_confidence=self._minimum_confidence,
                    provider=self._provider,
                    routing_strategy=self._routing_strategy,
                )
            ],
        )


def api_id_for_url(base_url: str) -> str:
    """Return a deterministic API ID for a configured Needle server."""
    digest = hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]
    return f"{DOMAIN}_{digest}"


def api_id_for_router(
    base_url: str, backend: str, entry_id: str
) -> str:
    """Keep existing API IDs, separate provider entries sharing Needle URL."""
    if backend == BACKEND_HA_PROVIDER:
        return api_id_for_url(f"ha_provider:{entry_id}")
    return api_id_for_url(base_url)


def api_name_for_url(
    base_url: str, *, backend: str = DEFAULT_BACKEND
) -> str:
    """Return a stable human-readable API name."""
    parsed = urlparse(base_url)
    location = parsed.netloc or base_url
    if backend == BACKEND_HA_PROVIDER:
        label = "Needle + HA model"
    else:
        label = "Needle LLM"
    return f"{label} @ {location}"


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
            "routing_trace": build_routing_trace(
                diagnostics or {},
                status=(
                    "failed"
                    if stage == "home_assistant_execution"
                    else "rejected"
                ),
                stage=stage,
                selected_tool=(
                    (diagnostics or {}).get("selected_tool")
                ),
                arguments=(diagnostics or {}).get("selected_arguments"),
            ),
            "guidance": (
                "Needle did not reach the configured confidence threshold, "
                "so no Home Assistant action or target lookup ran. State "
                "this low-confidence routing failure plainly; do not claim "
                "that devices were missing or that multiple lights were "
                "found."
                if stage == "needle_approval"
                and reason.startswith("Needle confidence ")
                else (
                    "No Home Assistant device was found or acted on unless "
                    "home_assistant_execution occurred. Do not invent a target "
                    "lookup result."
                )
            ),
            "diagnostics": diagnostics or {},
        },
        error=True,
    )
