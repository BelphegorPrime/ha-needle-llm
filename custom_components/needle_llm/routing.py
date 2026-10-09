"""Dynamic two-stage tool narrowing for Needle."""

from __future__ import annotations

from collections.abc import Collection
from difflib import SequenceMatcher
from typing import Any

EMPTY_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

TARGET_PARAMETER_NAMES = {
    "area",
    "device_class",
    "domain",
    "floor",
    "name",
}


def _parameter_names(tool: dict[str, Any]) -> list[str]:
    """Return top-level parameter names from a serialized tool schema."""
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return []

    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return []

    return [name for name in properties if isinstance(name, str)]


def _action_parameter_names(tool: dict[str, Any]) -> list[str]:
    """Return parameters that describe the action rather than its target."""
    return [
        name
        for name in _parameter_names(tool)
        if name not in TARGET_PARAMETER_NAMES
    ]


def _discovery_description(tool: dict[str, Any]) -> str:
    """Build a compact operation-first description for discovery."""
    name = tool["name"]
    title = tool.get("title")
    description = tool.get("description") or f"Home Assistant tool {name}"

    parts: list[str] = []
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())

    parts.append(description.strip())

    action_parameters = _action_parameter_names(tool)
    if action_parameters:
        fields = ", ".join(action_parameters)
        parts.append(
            "Action-specific inputs: "
            f"{fields}. Prefer this tool only when the user explicitly "
            "requests one of these action-specific settings."
        )
    else:
        parts.append(
            "This tool represents the requested action directly; prefer it "
            "over a device-specific settings tool when its action matches."
        )

    return ". ".join(part.rstrip(".") for part in parts if part) + "."


def build_discovery_tools(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build lightweight tool definitions for semantic discovery.

    Discovery intentionally omits the often-large Home Assistant parameter
    schemas. Needle only needs to decide which native Assist tool is relevant
    at this stage; no discovery result is ever executed.
    """
    return [
        {
            "name": tool["name"],
            "description": _discovery_description(tool),
            "parameters": EMPTY_PARAMETERS,
        }
        for tool in tools
    ]


def build_approval_tools(
    tools: list[dict[str, Any]], proposed_name: str
) -> list[dict[str, Any]]:
    """Present alternative actions for independent, argument-free approval.

    Use real alternatives in the proposed tool's native family. For tools
    without alternatives, retain the full catalog rather than allowing
    Needle to rubber-stamp the only available option.
    """
    proposed = next(
        (tool for tool in tools if tool["name"] == proposed_name), None
    )
    if proposed is None:
        return []

    family, separator, action = proposed_name.partition("__")
    same_family = [
        tool for tool in tools
        if tool["name"] != proposed_name
        and tool["name"].partition("__")[0] == family
    ]
    # Prefer genuinely similar operations (e.g. turn on vs turn off) over
    # unrelated tools sharing the generic "intent" family. Never hard-code
    # native tool names, entity domains or language-specific keywords.
    close_alternatives = [
        tool
        for tool in same_family
        if separator and SequenceMatcher(
            None,
            action.casefold(),
            tool["name"].partition("__")[2].casefold(),
        ).ratio() >= 0.72
    ]
    if close_alternatives:
        alternatives = [proposed, *close_alternatives]
    elif len(same_family) >= 1 and separator:
        alternatives = [proposed, *same_family]
    else:
        # No alternative means there is no independent action comparison.
        # Keep the full catalog rather than offering a single forced choice.
        alternatives = tools

    return [
        {
            "name": tool["name"],
            "description": (
                (tool.get("title") or tool.get("description") or tool["name"])
                .strip()[:240]
            ),
            "parameters": EMPTY_PARAMETERS,
        }
        for tool in alternatives
    ]


def build_routing_query(query: str) -> str:
    """Add generic routing guidance without hard-coding HA tool names."""
    return (
        "Choose the tool by the action the user explicitly requested, not just "
        "by the type of device mentioned. Prefer a tool whose action directly "
        "matches the request over a device-specific settings tool. Do not "
        "invent optional settings such as color, brightness, temperature, "
        "volume, position, duration, or similar values unless the user "
        "explicitly requested them. Never reinterpret an action word as the "
        "value of an unrelated setting.\n\n"
        f"User request: {query}"
    )


def execution_tool(
    tool: dict[str, Any],
    *,
    unique_named_target: bool = False,
) -> dict[str, Any]:
    """Build a minimal safe schema for argument extraction.

    A uniquely named exposed entity does not need an optional device_class
    classifier: the user has already specified the target by name. Omitting
    this optional field avoids feeding Needle huge repetitive class enums.
    The original schema is retained for native Home Assistant validation.
    """
    parameters = tool["parameters"]
    if unique_named_target:
        properties = parameters.get("properties")
        required = parameters.get("required", [])
        if (
            isinstance(properties, dict)
            and "device_class" in properties
            and "device_class" not in required
        ):
            parameters = {
                **parameters,
                "properties": {
                    key: value
                    for key, value in properties.items()
                    if key != "device_class"
                },
            }

    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": parameters,
    }


def candidate_tool_names(
    result: dict[str, Any],
    allowed_tools: Collection[str],
    *,
    limit: int = 3,
) -> list[str]:
    """Extract a small dynamic shortlist from a discovery response.

    Discovery calls are never executable, so suppressed calls may be used as
    shortlist hints. They remain non-executable and must pass the normal route
    validation in the second stage.
    """
    allowed = set(allowed_tools)
    candidates: list[str] = []

    for key in ("function_calls", "suppressed_calls"):
        calls = result.get(key)
        if not isinstance(calls, list):
            continue

        for call in calls:
            if not isinstance(call, dict):
                continue

            name = call.get("name")
            if (
                isinstance(name, str)
                and name in allowed
                and name not in candidates
            ):
                candidates.append(name)

            if len(candidates) >= limit:
                return candidates

    return candidates
