"""Dynamic two-stage tool narrowing for Needle."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

EMPTY_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
}


def build_discovery_tools(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build lightweight tool definitions for semantic discovery.

    Discovery intentionally omits the often-large Home Assistant parameter
    schemas. Needle only needs to decide which native Assist tool is relevant
    at this stage; no discovery result is ever executed.
    """
    discovered: list[dict[str, Any]] = []

    for tool in tools:
        name = tool["name"]
        title = tool.get("title")
        description = tool.get("description") or f"Home Assistant tool {name}"

        if isinstance(title, str) and title.strip():
            description = f"{title.strip()}. {description}"

        discovered.append(
            {
                "name": name,
                "description": description,
                "parameters": EMPTY_PARAMETERS,
            }
        )

    return discovered


def execution_tool(
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Strip integration-only metadata before sending a real tool to Needle."""
    return {
        "name": tool["name"],
        "description": tool["description"],
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
