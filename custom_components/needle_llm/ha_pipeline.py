"""Three-stage provider-backed routing with independent Needle approval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .client import NeedleClient, NeedleClientError
from .ha_provider import HomeAssistantModelProvider, ProviderError
from .routing import (
    build_discovery_tools,
    build_routing_query,
    candidate_tool_names,
    execution_tool,
)
from .target_guard import find_unique_mentioned_entity
from .validation import (
    ApprovedRoute,
    RouteRejected,
    approve_openai_route,
    approve_route,
)

STRATEGY_NEEDLE_FIRST = "needle_preselection"
STRATEGY_MODEL_FIRST = "model_preselection"
DEFAULT_STRATEGY = STRATEGY_NEEDLE_FIRST
STRATEGIES = (STRATEGY_NEEDLE_FIRST, STRATEGY_MODEL_FIRST)


@dataclass(slots=True)
class PipelineRejected(Exception):
    """A routing stage refused an action or failed safely."""

    stage: str
    reason: str


@dataclass(slots=True)
class PipelineResult:
    """Approved proposed tool call; no Home Assistant action performed yet."""

    route: ApprovedRoute
    candidate: str
    matched_target: dict[str, Any] | None
    removed_fields: list[dict[str, Any]]


def _exposed_target(
    hass: HomeAssistant, query: str, assistant: str
) -> dict[str, Any] | None:
    """Locate one exact exposed entity name without guessing a domain."""
    entities: list[dict[str, Any]] = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, assistant, state.entity_id):
            continue
        entities.append(
            {
                "name": state.name,
                "entity_id": state.entity_id,
                "device_class": state.attributes.get("device_class"),
            }
        )
    return find_unique_mentioned_entity(query, entities)


async def async_provider_route(
    *,
    hass: HomeAssistant,
    context: llm.LLMContext,
    query: str,
    tools: list[dict[str, Any]],
    needle: NeedleClient,
    provider: HomeAssistantModelProvider,
    strategy: str,
    minimum_confidence: float,
    diagnostics: dict[str, Any],
) -> PipelineResult:
    """Preselect a tool, independently approve with Needle, extract arguments.

    The model's preliminary tool call is *never* executed. Needle receives the
    entire native discovery surface and must independently agree with the
    preselected action at the configured confidence threshold.
    """
    if strategy not in STRATEGIES:
        raise PipelineRejected("configuration", "Unknown routing strategy")

    available = {tool["name"] for tool in tools}
    tools_by_name = {tool["name"]: tool for tool in tools}
    discovery_tools = build_discovery_tools(tools)
    diagnostics["routing_strategy"] = strategy
    diagnostics["minimum_confidence"] = minimum_confidence

    if strategy == STRATEGY_MODEL_FIRST:
        try:
            initial, transport = await provider.async_complete(
                tools=discovery_tools,
                query=query,
                language=context.language,
                stage="preselection",
            )
        except ProviderError as err:
            raise PipelineRejected("preselection", str(err)) from err
        try:
            tentative = approve_openai_route(
                initial, allowed_tools=available
            ).tool
        except RouteRejected as err:
            raise PipelineRejected("preselection", str(err)) from err
        diagnostics["preselection"] = {
            "source": "model",
            "candidate": tentative,
            "transport": transport,
            "model_proposal": initial.get("choices"),
        }
    else:
        try:
            initial, transport = await needle.async_complete(
                tools=discovery_tools,
                query=build_routing_query(query),
                stage="discovery",
            )
        except NeedleClientError as err:
            raise PipelineRejected("preselection", str(err)) from err
        candidates = candidate_tool_names(initial, available, limit=2)
        diagnostics["preselection"] = {
            "source": "needle",
            "candidates": candidates,
            "transport": transport,
            "confidence": initial.get("confidence"),
            "function_calls": initial.get("function_calls", []),
            "suppressed_calls": initial.get("suppressed_calls", []),
            "validation": initial.get("validation", {}),
            "reasoning": initial.get("reasoning"),
        }
        # Discovery is only a suggestion; suppressed calls are never approved.
        if len(candidates) != 1:
            raise PipelineRejected(
                "preselection",
                "Needle did not unambiguously preselect one Assist tool",
            )
        tentative = candidates[0]

    try:
        verification, verification_transport = await needle.async_complete(
            tools=discovery_tools,
            query=build_routing_query(query),
            stage="approval",
        )
    except NeedleClientError as err:
        raise PipelineRejected("needle_approval", str(err)) from err

    diagnostics["needle_approval"] = {
        "candidate": tentative,
        "transport": verification_transport,
        "confidence": verification.get("confidence"),
        "reasoning": verification.get("reasoning"),
        "function_calls": verification.get("function_calls", []),
        "suppressed_calls": verification.get("suppressed_calls", []),
        "validation": verification.get("validation", {}),
    }

    try:
        approved = approve_route(
            verification,
            minimum_confidence,
            allowed_tools=available,
        )
    except RouteRejected as err:
        raise PipelineRejected("needle_approval", str(err)) from err

    if approved.tool != tentative:
        raise PipelineRejected(
            "needle_approval",
            "Needle selected a different native Assist action than the "
            "preselection; no action was executed",
        )
    # The approval-stage tools are parameterless: Needle approves only the
    # action, not any argument from the untrusted preselection.
    if approved.arguments:
        raise PipelineRejected(
            "needle_approval",
            "Needle returned unexpected arguments while approving an action",
        )

    matched_target = _exposed_target(hass, query, context.assistant)
    native = tools_by_name[approved.tool]
    narrowed = execution_tool(
        native, unique_named_target=matched_target is not None
    )
    raw_fields = native["parameters"].get("properties", {})
    safe_fields = narrowed["parameters"].get("properties", {})
    removed_fields = (
        [{"tool": approved.tool, "fields": ["device_class"]}]
        if "device_class" in raw_fields and "device_class" not in safe_fields
        else []
    )
    diagnostics["target_schema"] = {
        "unique_exposed_name_match": matched_target is not None,
        "matched_entity_id": (
            matched_target["entity_id"] if matched_target else None
        ),
        "removed_optional_fields": removed_fields,
    }

    try:
        final, final_transport = await provider.async_complete(
            tools=[narrowed],
            query=query,
            language=context.language,
            stage="arguments",
        )
    except ProviderError as err:
        raise PipelineRejected("argument_generation", str(err)) from err

    choices = final.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    msg = choice.get("message") if isinstance(choice, dict) else {}
    diagnostics["route"] = {
        "approved_tool": approved.tool,
        "generated_by": "ha_model",
        "transport": final_transport,
        "finish_reason": (
            choice.get("finish_reason")
            if isinstance(choice, dict) else None
        ),
        "tool_calls": (
            msg.get("tool_calls", []) if isinstance(msg, dict) else []
        ),
    }
    try:
        route = approve_openai_route(
            final, allowed_tools={approved.tool}
        )
    except RouteRejected as err:
        raise PipelineRejected("argument_generation", str(err)) from err
    return PipelineResult(
        route=route,
        candidate=approved.tool,
        matched_target=matched_target,
        removed_fields=removed_fields,
    )
