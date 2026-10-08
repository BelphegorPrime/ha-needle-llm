"""Tool-call serialization tests for the HA model proposal adapter."""

import json

import pytest

from custom_components.needle_llm.ha_provider import openai_tools
from custom_components.needle_llm.validation import (
    RouteRejected,
    approve_openai_route,
)


def _response(
    name: str = "intent__HassTurnOff",
    arguments: str = '{"name":"Wohnzimmerlampe"}',
) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
            }
        ]
    }


def test_tool_schema_mapping() -> None:
    """Preserve the native HA schema when calling OpenAI tools."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    native = [
        {
            "name": "intent__HassTurnOff",
            "description": "Turn off",
            "parameters": schema,
        }
    ]
    tools = openai_tools(native)
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "intent__HassTurnOff"
    assert tools[0]["function"]["parameters"] is schema


def test_approve_single_native_tool_call() -> None:
    """An allowed tool is parsed without fabricating model confidence."""
    result = approve_openai_route(
        _response(), allowed_tools={"intent__HassTurnOff"}
    )
    assert result.tool == "intent__HassTurnOff"
    assert result.arguments == {"name": "Wohnzimmerlampe"}
    assert result.confidence is None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"choices": []}, "exactly one choice"),
        ({"choices": [{"message": {"tool_calls": []}}]}, "exactly one tool call"),
        (_response("not_available"), "unavailable tool"),
        (_response(arguments="{bad json"), "malformed argument JSON"),
        (_response(arguments=json.dumps([1, 2])), "JSON object"),
        (
            {
                "choices": [
                    {**_response()["choices"][0], "finish_reason": "length"}
                ]
            },
            "incomplete",
        ),
    ],
)
def test_fail_closed_on_invalid_model_output(body: dict, reason: str) -> None:
    """Do not execute hallucinated, truncated, or malformed calls."""
    with pytest.raises(RouteRejected, match=reason):
        approve_openai_route(body, allowed_tools={"intent__HassTurnOff"})


def test_fail_closed_on_multiple_calls() -> None:
    """Even if parallel tools are emitted, no call may execute."""
    response = _response()
    calls = response["choices"][0]["message"]["tool_calls"]
    calls.append(calls[0].copy())
    with pytest.raises(RouteRejected, match="exactly one tool call"):
        approve_openai_route(
            response, allowed_tools={"intent__HassTurnOff"}
        )
