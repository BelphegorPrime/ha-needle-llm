"""Validation helpers for Needle and OpenAI-compatible routing results."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any


class RouteRejected(Exception):
    """Raised when a route must not be executed."""


@dataclass(frozen=True, slots=True)
class ApprovedRoute:
    """A model route that passed preliminary validation."""

    tool: str
    arguments: dict[str, Any]
    confidence: float | None


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
    if isinstance(validation, dict):
        if validation.get("ungrounded"):
            raise RouteRejected("Needle marked one or more arguments as ungrounded")
        if validation.get("negation") is True:
            raise RouteRejected("Needle rejected the requested action")

    call = calls[0]
    if not isinstance(call, dict):
        raise RouteRejected("Needle returned an invalid function call")

    tool = call.get("name")
    if not isinstance(tool, str) or tool not in allowed_tools:
        raise RouteRejected(f"Needle selected an unavailable tool: {tool}")

    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        raise RouteRejected("Needle returned invalid tool arguments")

    return ApprovedRoute(tool=tool, arguments=arguments, confidence=confidence)


def approve_openai_route(
    result: dict[str, Any],
    *,
    allowed_tools: Collection[str],
) -> ApprovedRoute:
    """Reject anything except exactly one valid OpenAI-compatible function call.

    The OpenAI interface does not return a Needle-style calibrated confidence.
    """
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RouteRejected("OpenAI-compatible did not return exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") == "length":
        raise RouteRejected("OpenAI-compatible returned an incomplete choice")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise RouteRejected("OpenAI-compatible returned no assistant message")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise RouteRejected("OpenAI-compatible did not return exactly one tool call")
    call = calls[0]
    if not isinstance(call, dict) or call.get("type") != "function":
        raise RouteRejected("OpenAI-compatible returned a non-function tool call")
    function = call.get("function")
    if not isinstance(function, dict):
        raise RouteRejected("OpenAI-compatible returned an invalid function")
    name = function.get("name")
    if not isinstance(name, str) or name not in allowed_tools:
        raise RouteRejected(f"OpenAI-compatible selected an unavailable tool: {name}")
    args = function.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError) as err:
            raise RouteRejected(
                "OpenAI-compatible returned malformed argument JSON"
            ) from err
    if not isinstance(args, dict):
        raise RouteRejected("OpenAI-compatible arguments must be a JSON object")
    return ApprovedRoute(tool=name, arguments=args, confidence=None)
