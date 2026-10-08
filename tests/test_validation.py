"""Tests for Needle route validation."""

import pytest

from custom_components.needle_llm.routing import (
    build_discovery_tools,
    build_routing_query,
    candidate_tool_names,
    execution_tool,
)
from custom_components.needle_llm.validation import (
    RouteRejected,
    approve_route,
)

ALLOWED = {"HassTurnOn", "HassTurnOff", "GetLiveContext"}


def _result(
    *,
    confidence: float = 0.95,
    function_calls: list | None = None,
    ungrounded: list | None = None,
) -> dict:
    return {
        "success": True,
        "function_calls": function_calls
        if function_calls is not None
        else [
            {
                "name": "HassTurnOff",
                "arguments": {"name": "Wohnzimmerlampe"},
            }
        ],
        "suppressed_calls": [],
        "confidence": confidence,
        "validation": {"ungrounded": ungrounded or []},
    }


def test_approve_route() -> None:
    """A single confident grounded available call is approved."""
    route = approve_route(_result(), 0.80, allowed_tools=ALLOWED)

    assert route.tool == "HassTurnOff"
    assert route.arguments == {"name": "Wohnzimmerlampe"}
    assert route.confidence == 0.95


def test_suppressed_only_is_rejected() -> None:
    """A suppressed call must never become executable."""
    result = _result(function_calls=[])
    result["suppressed_calls"] = [
        {
            "name": "HassTurnOn",
            "arguments": {"name": "Einfahrtstor"},
        }
    ]

    with pytest.raises(RouteRejected, match="exactly one executable call"):
        approve_route(result, 0.80, allowed_tools=ALLOWED)


def test_low_confidence_is_rejected() -> None:
    """Low-confidence calls are rejected."""
    with pytest.raises(RouteRejected, match="below"):
        approve_route(_result(confidence=0.79), 0.80, allowed_tools=ALLOWED)


def test_ungrounded_is_rejected() -> None:
    """Needle grounding failures are rejected."""
    with pytest.raises(RouteRejected, match="ungrounded"):
        approve_route(
            _result(ungrounded=["HassTurnOn.name"]),
            0.80,
            allowed_tools=ALLOWED,
        )


def test_multiple_calls_are_rejected() -> None:
    """One NeedleRoute invocation executes at most one native HA tool."""
    calls = [
        {"name": "HassTurnOn", "arguments": {"name": "A"}},
        {"name": "HassTurnOn", "arguments": {"name": "B"}},
    ]

    with pytest.raises(RouteRejected, match="exactly one executable call"):
        approve_route(_result(function_calls=calls), 0.80, allowed_tools=ALLOWED)


def test_tool_not_in_current_assist_api_is_rejected() -> None:
    """Needle cannot execute tools absent from the current Assist API instance."""
    calls = [
        {
            "name": "CallService",
            "arguments": {"service": "something"},
        }
    ]

    with pytest.raises(RouteRejected, match="unavailable tool"):
        approve_route(_result(function_calls=calls), 0.80, allowed_tools=ALLOWED)


def test_failed_result_is_rejected() -> None:
    """A failed Needle response is rejected before inspecting calls."""
    result = _result()
    result["success"] = False

    with pytest.raises(RouteRejected, match="success=true"):
        approve_route(result, 0.80, allowed_tools=ALLOWED)


NARROWING_TOOLS = [
    {
        "name": "intent__HassTurnOff",
        "title": "Turn off",
        "description": "Turn off a Home Assistant device.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
    {
        "name": "media_player__HassSetVolume",
        "title": "Set volume",
        "description": "Set media player volume.",
        "parameters": {
            "type": "object",
            "properties": {"volume_level": {"type": "number"}},
        },
    },
]


def test_discovery_tools_remove_large_parameter_schemas() -> None:
    """Discovery keeps semantics but strips argument schemas."""
    discovery = build_discovery_tools(NARROWING_TOOLS)

    assert discovery[0]["name"] == "intent__HassTurnOff"
    assert discovery[0]["description"].startswith("Turn off.")
    assert discovery[0]["parameters"] == {
        "type": "object",
        "properties": {},
    }


def test_suppressed_discovery_call_can_be_shortlist_hint() -> None:
    """A suppressed discovery candidate may narrow stage two without execution."""
    result = {
        "function_calls": [],
        "suppressed_calls": [
            {
                "name": "intent__HassTurnOff",
                "arguments": {},
            }
        ],
    }

    candidates = candidate_tool_names(
        result,
        {tool["name"] for tool in NARROWING_TOOLS},
    )

    assert candidates == ["intent__HassTurnOff"]


def test_discovery_candidates_are_limited_and_deduplicated() -> None:
    """Discovery cannot expand back into the full Assist tool set."""
    result = {
        "function_calls": [
            {"name": "intent__HassTurnOff", "arguments": {}},
        ],
        "suppressed_calls": [
            {"name": "intent__HassTurnOff", "arguments": {}},
            {"name": "media_player__HassSetVolume", "arguments": {}},
            {"name": "unknown", "arguments": {}},
        ],
    }

    candidates = candidate_tool_names(
        result,
        {tool["name"] for tool in NARROWING_TOOLS},
        limit=2,
    )

    assert candidates == [
        "intent__HassTurnOff",
        "media_player__HassSetVolume",
    ]


def test_execution_tool_restores_full_schema() -> None:
    """Stage two receives the exact full schema for shortlisted tools."""
    execution = execution_tool(NARROWING_TOOLS[0])

    assert execution == {
        "name": "intent__HassTurnOff",
        "description": "Turn off a Home Assistant device.",
        "parameters": NARROWING_TOOLS[0]["parameters"],
    }



def test_discovery_description_prefers_explicit_action_over_settings() -> None:
    """Settings tools explain that their fields must be explicitly requested."""
    discovery = build_discovery_tools(NARROWING_TOOLS)

    assert "Action-specific inputs: volume_level" in discovery[1]["description"]
    assert "explicitly requests" in discovery[1]["description"]


def test_routing_query_is_operation_first() -> None:
    """Needle receives generic operation-first routing guidance."""
    query = build_routing_query("Schalte die Wohnzimmerlampe aus")

    assert "action the user explicitly requested" in query
    assert "Do not invent optional settings" in query
    assert query.endswith("User request: Schalte die Wohnzimmerlampe aus")


def test_execution_tool_does_not_append_discovery_instructions() -> None:
    """Stage two must not inject instructions that look like arguments."""
    tool = {
        "name": "light__HassLightSet",
        "title": "Set light",
        "description": "Sets color or brightness.",
        "parameters": {
            "type": "object",
            "properties": {"color": {"type": "string"}},
        },
    }
    result = execution_tool(tool)
    assert result["description"] == "Sets color or brightness."
    assert result["parameters"] is tool["parameters"]
