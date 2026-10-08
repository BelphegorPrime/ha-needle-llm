"""Validation helpers for Needle routing results."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any


class RouteRejected(Exception):
    """Raised when a Needle result must not be executed."""


@dataclass(frozen=True, slots=True)
class ApprovedRoute:
    """A Needle route that passed validation."""

    tool: str
    arguments: dict[str, Any]
    confidence: float


def approve_route(
    result: dict[str, Any],
    minimum_confidence: float,
    *,
    allowed_tools: Collection[str],
) -> ApprovedRoute:
    """Validate Needle output before forwarding it to Home Assistant."""
    if result.get("success") is not True:
        raise RouteRejected("Needle did not return success=true")

    calls = result.get("function_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise RouteRejected("Needle did not return exactly one executable call")

    try:
        confidence = float(result.get("confidence"))
    except (TypeError, ValueError) as err:
        raise RouteRejected("Needle did not return a confidence value") from err

    if confidence < minimum_confidence:
        raise RouteRejected(
            f"Needle confidence {confidence:.4f} is below "
            f"{minimum_confidence:.4f}"
        )

    validation = result.get("validation")
    if isinstance(validation, dict) and validation.get("ungrounded"):
        raise RouteRejected("Needle marked one or more arguments as ungrounded")

    call = calls[0]
    if not isinstance(call, dict):
        raise RouteRejected("Needle returned an invalid function call")

    tool = call.get("name")
    if not isinstance(tool, str) or tool not in allowed_tools:
        raise RouteRejected(f"Needle selected an unavailable tool: {tool}")

    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise RouteRejected("Needle returned invalid tool arguments")

    return ApprovedRoute(
        tool=tool,
        arguments=arguments,
        confidence=confidence,
    )
