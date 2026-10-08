"""Validation helpers for Needle and llama.cpp routing results."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import json
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

    return ApprovedRoute(tool=tool, arguments=arguments, confidence=confidence)


def approve_llama_route(
    result: dict[str, Any],
    *,
    allowed_tools: Collection[str],
) -> ApprovedRoute:
    """Reject anything except exactly one valid llama.cpp function call.

    The OpenAI interface does not return a Needle-style calibrated confidence.
    """
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RouteRejected("llama.cpp did not return exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") == "length":
        raise RouteRejected("llama.cpp returned an incomplete choice")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise RouteRejected("llama.cpp returned no assistant message")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise RouteRejected("llama.cpp did not return exactly one tool call")
    call = calls[0]
    if not isinstance(call, dict) or call.get("type") != "function":
        raise RouteRejected("llama.cpp returned a non-function tool call")
    function = call.get("function")
    if not isinstance(function, dict):
        raise RouteRejected("llama.cpp returned an invalid function")
    name = function.get("name")
    if not isinstance(name, str) or name not in allowed_tools:
        raise RouteRejected(f"llama.cpp selected an unavailable tool: {name}")
    args = function.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError) as err:
            raise RouteRejected("llama.cpp returned malformed argument JSON") from err
    if not isinstance(args, dict):
        raise RouteRejected("llama.cpp arguments must be a JSON object")
    return ApprovedRoute(tool=name, arguments=args, confidence=None)
