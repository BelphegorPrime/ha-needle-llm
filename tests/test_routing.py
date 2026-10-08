"""Tests for dynamic Needle tool narrowing."""

from custom_components.needle_llm.routing import (
    build_discovery_tools,
    candidate_tool_names,
    execution_tool,
)


TOOLS = [
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
    discovery = build_discovery_tools(TOOLS)

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
        {tool["name"] for tool in TOOLS},
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
        {tool["name"] for tool in TOOLS},
        limit=2,
    )

    assert candidates == [
        "intent__HassTurnOff",
        "media_player__HassSetVolume",
    ]


def test_execution_tool_restores_full_schema() -> None:
    """Stage two receives the exact full schema for shortlisted tools."""
    execution = execution_tool(TOOLS[0])

    assert execution == {
        "name": "intent__HassTurnOff",
        "description": "Turn off a Home Assistant device.",
        "parameters": TOOLS[0]["parameters"],
    }
