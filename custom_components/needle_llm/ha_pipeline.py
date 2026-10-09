"""Three-stage provider-backed routing with independent Needle approval."""

from __future__ import annotations

import re
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
    build_approval_tools,
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

    approval_tools = build_approval_tools(tools, tentative)
    if not approval_tools:
        raise PipelineRejected(
            "needle_approval", "Selected action is no longer available"
        )

    # Approve the original request against real alternatives. Do not send
    # optional argument examples or full parameter schemas in this pass.
    try:
        verification, verification_transport = await needle.async_complete(
            tools=approval_tools,
            query=query,
            stage="approval",
        )
    except NeedleClientError as err:
        raise PipelineRejected("needle_approval", str(err)) from err

    diagnostics["needle_approval"] = {
        "candidate": tentative,
        "candidate_tools": [tool["name"] for tool in approval_tools],
        "candidate_tool_count": len(approval_tools),
        "query_mode": "original_user_request",
        "transport": verification_transport,
        "confidence": verification.get("confidence"),
        "reasoning": verification.get("reasoning"),
        "function_calls": verification.get("function_calls", []),
        "suppressed_calls": verification.get("suppressed_calls", []),
        "validation": verification.get("validation", {}),
    }

    # An English fallback is allowed only after Needle independently agreed
    # with the original operation. Never replace an explicit refusal.
    initial_approval = diagnostics["needle_approval"]
    first_calls = verification.get("function_calls")
    original_agreement = (
        verification.get("success") is True
        and isinstance(first_calls, list)
        and len(first_calls) == 1
        and isinstance(first_calls[0], dict)
        and first_calls[0].get("name") == tentative
        and first_calls[0].get("arguments") == {}
        and not verification.get("suppressed_calls")
        and isinstance(verification.get("validation"), dict)
        and not verification["validation"].get("ungrounded")
        and verification["validation"].get("negation") is not True
    )
    try:
        original_confidence = float(verification.get("confidence"))
    except (TypeError, ValueError):
        original_confidence = -1.0

    if original_agreement and 0 <= original_confidence < minimum_confidence:
        # Translate only an action description. No translated arguments can
        # reach HA. The provider's tool call is validated before use.
        translation_tool = {
            "name": "NeedleTranslateToEnglish",
            "description": (
                "Translate the original request into English without changing "
                "actions, negation, scope, or literal Home Assistant names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "english_query": {"type": "string"},
                },
                "required": ["english_query"],
                "additionalProperties": False,
            },
        }
        fallback = {"attempted": True, "accepted": False}
        initial_approval["english_fallback"] = fallback
        try:
            translated, translation_transport = await provider.async_complete(
                tools=[translation_tool],
                query=query,
                language=context.language,
                stage="approval_translation",
            )
            fallback["translation_transport"] = translation_transport
            translation_call = approve_openai_route(
                translated, allowed_tools={"NeedleTranslateToEnglish"}
            )
            english = translation_call.arguments.get("english_query")
            # A bounded single-line request, with no unsafe translation
            # of literal HA target names to a new spelling.
            if not isinstance(english, str) or not (
                0 < len(english.strip()) <= 1000
            ) or "\n" in english or "\r" in english:
                raise RouteRejected("Invalid English normalization")
            english = english.strip()
            # The fallback is for language normalization, not rewriting
            # already-English inputs. Never use it to rescue a refusal.
            if english.casefold() == query.strip().casefold():
                raise RouteRejected(
                    "English normalization did not change the request"
                )
            # Names are literal HA identifiers, regardless of locale.
            for state in hass.states.async_all():
                if not async_should_expose(
                    hass, context.assistant, state.entity_id
                ):
                    continue
                name = state.name
                if not isinstance(name, str) or not name.strip():
                    continue
                pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
                if re.search(pattern, query, re.I) and not re.search(
                    pattern, english, re.I
                ):
                    raise RouteRejected(
                        "English normalization changed an exposed entity name"
                    )
            # Do not let the translation suppress a negation in the original.
            # In all cases Needle must still choose the same action with the
            # configured minimum confidence, and with no argument payload.
            second, second_transport = await needle.async_complete(
                tools=approval_tools,
                query=english,
                stage="approval_english",
            )
            fallback["english_query"] = english
            fallback["needle_transport"] = second_transport
            fallback["confidence"] = second.get("confidence")
            fallback["selected_calls"] = second.get("function_calls", [])
            fallback["validation"] = second.get("validation", {})
            second_approved = approve_route(
                second,
                minimum_confidence,
                allowed_tools={tool["name"] for tool in approval_tools},
            )
            if second_approved.tool != tentative or second_approved.arguments:
                raise RouteRejected("English approval disagreed with original action")
            if second.get("suppressed_calls"):
                raise RouteRejected(
                    "English approval contained suppressed calls"
                )
            # Retain both decisions in diagnostics and the original request
            # for final argument generation; never copy translated arguments.
            verification = second
            fallback["accepted"] = True
            initial_approval["original_confidence"] = original_confidence
            initial_approval["confidence"] = second_approved.confidence
            initial_approval["reasoning"] = second.get("reasoning")
            initial_approval["function_calls"] = second.get("function_calls", [])
            initial_approval["validation"] = second.get("validation", {})
        except (ProviderError, NeedleClientError, RouteRejected) as err:
            fallback["reason"] = str(err)

    try:
        approved = approve_route(
            verification,
            minimum_confidence,
            allowed_tools={tool["name"] for tool in approval_tools},
        )
    except RouteRejected as err:
        reason = str(err)
        # Agreement on an operation is not approval: the calibrated Needle
        # confidence gate still decides whether execution may proceed.
        calls = verification.get("function_calls")
        if (
            not diagnostics["needle_approval"]
            .get("english_fallback", {})
            .get("accepted")
            and reason.startswith("Needle confidence ")
            and isinstance(calls, list)
            and len(calls) == 1
            and isinstance(calls[0], dict)
            and calls[0].get("name") == tentative
        ):
            diagnostics["needle_approval"]["agreed_but_low_confidence"] = True
            reason = (
                f"Model and Needle selected the same operation ({tentative}), "
                f"but {reason}. No Home Assistant lookup or action ran; "
                "this does not establish that any devices are missing "
                "or that multiple devices were found."
            )
        raise PipelineRejected("needle_approval", reason) from err

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
