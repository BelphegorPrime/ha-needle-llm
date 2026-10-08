"""Tests for Needle route validation."""

import pytest

from custom_components.needle_llm.validation import (
    RouteRejected,
    approve_route,
)


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
    """A single confident grounded allowlisted call is approved."""
    route = approve_route(_result(), 0.80)

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
        approve_route(result, 0.80)


def test_low_confidence_is_rejected() -> None:
    """Low-confidence calls are rejected."""
    with pytest.raises(RouteRejected, match="below"):
        approve_route(_result(confidence=0.79), 0.80)


def test_ungrounded_is_rejected() -> None:
    """Needle grounding failures are rejected."""
    with pytest.raises(RouteRejected, match="ungrounded"):
        approve_route(_result(ungrounded=["HassTurnOn.name"]), 0.80)


def test_multiple_calls_are_rejected() -> None:
    """Version 0.1.0 executes no multi-call response."""
    calls = [
        {"name": "HassTurnOn", "arguments": {"name": "A"}},
        {"name": "HassTurnOn", "arguments": {"name": "B"}},
    ]

    with pytest.raises(RouteRejected, match="exactly one executable call"):
        approve_route(_result(function_calls=calls), 0.80)


def test_unknown_tool_is_rejected() -> None:
    """Tools outside the v0.1.0 allowlist are rejected."""
    calls = [
        {
            "name": "CallService",
            "arguments": {"service": "something"},
        }
    ]

    with pytest.raises(RouteRejected, match="Unsupported Needle tool"):
        approve_route(_result(function_calls=calls), 0.80)


def test_failed_result_is_rejected() -> None:
    """A failed Needle response is rejected before inspecting calls."""
    result = _result()
    result["success"] = False

    with pytest.raises(RouteRejected, match="success=true"):
        approve_route(result, 0.80)
