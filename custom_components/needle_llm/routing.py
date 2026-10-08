"""Dynamic two-stage tool narrowing for Needle."""

from __future__ import annotations

from collections.abc import Collection
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
) -> dict[str, Any]:
    """Strip integration-only metadata before sending a real tool to Needle."""
    description = tool["description"]
    action_parameters = _action_parameter_names(tool)

    if action_parameters:
        fields = ", ".join(action_parameters)
        description = (
            f"{description} Only set action-specific inputs ({fields}) when "
            "they are explicitly grounded in the user's request."
        )

    return {
        "name": tool["name"],
        "description": description,
        "parameters": tool["parameters"],
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
