"""Tests for Needle route validation."""

import pytest

from custom_components.needle_llm.routing import (
    build_approval_tools,
    build_discovery_tools,
    build_routing_query,
    build_semantic_approval_tools,
    candidate_tool_names,
    execution_tool,
)
from custom_components.needle_llm.target_guard import (
    TargetGuardRejected,
    find_unique_mentioned_entity,
    reconcile_named_target,
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



def test_repair_wrong_class_only_for_unique_exposed_name() -> None:
    """A hallucinated class can be removed for one explicit exposed entity."""
    schema = {
        "properties": {
            "name": {"type": "string"},
            "area": {"type": "string"},
            "domain": {"type": "array"},
            "device_class": {"type": "array"},
        }
    }
    arguments = {
        "name": "Wohnzimmerlampe",
        "area": "Wohnzimmer",
        "device_class": ["blind"],
    }
    entities = [
        {
            "name": "Wohnzimmerlampe",
            "entity_id": "light.wohnzimmerlampe",
            "device_class": None,
        }
    ]

    repaired, details = reconcile_named_target(
        "Schalte die Wohnzimmerlampe aus", arguments, schema, entities
    )

    assert repaired == {
        "name": "Wohnzimmerlampe",
        "area": "Wohnzimmer",
        "domain": ["light"],
    }
    assert details["status"] == "repaired_unrequested_device_class"
    assert arguments["device_class"] == ["blind"]


def test_never_repair_ambiguous_named_entities() -> None:
    """Identical exposed names across domains must remain constrained."""
    arguments = {"name": "Wohnzimmerlampe", "device_class": ["blind"]}
    entities = [
        {"name": "Wohnzimmerlampe", "entity_id": "light.one"},
        {"name": "Wohnzimmerlampe", "entity_id": "switch.two"},
    ]
    repaired, details = reconcile_named_target(
        "Wohnzimmerlampe aus", arguments, {}, entities
    )
    assert repaired == arguments
    assert details["status"] == "not_unique_or_not_exposed"


def test_never_repair_substring_or_unexposed_target() -> None:
    """The full named target must be present in the original request."""
    arguments = {"name": "Tor", "device_class": ["blind"]}
    entities = [{"name": "Tor", "entity_id": "switch.gate"}]
    repaired, details = reconcile_named_target(
        "Schalte das Einfahrtstor aus", arguments, {}, entities
    )
    assert repaired == arguments
    assert details["status"] == "unchanged"


def test_never_repair_explicit_conflicting_class() -> None:
    """An explicitly requested contradictory class must fail closed."""
    with pytest.raises(TargetGuardRejected, match="explicitly requested"):
        reconcile_named_target(
            "Schalte die blind Wohnzimmerlampe aus",
            {"name": "Wohnzimmerlampe", "device_class": ["blind"]},
            {},
            [{"name": "Wohnzimmerlampe", "entity_id": "light.lamp"}],
        )


def test_never_repair_conflicting_domain() -> None:
    """A conflicting domain cannot be changed silently."""
    with pytest.raises(TargetGuardRejected, match="conflicting domain"):
        reconcile_named_target(
            "Schalte die Wohnzimmerlampe aus",
            {
                "name": "Wohnzimmerlampe",
                "domain": ["switch"],
                "device_class": ["blind"],
            },
            {"properties": {"domain": {}}},
            [{"name": "Wohnzimmerlampe", "entity_id": "light.lamp"}],
        )



def test_unique_literal_exposed_target_can_omit_optional_device_class() -> None:
    """Optional class enums are unnecessary for uniquely named targets."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "device_class": {"type": "array", "items": {"enum": ["blind"]}},
            "domain": {"type": "array"},
        },
        "required": ["name"],
    }
    tool = {
        "name": "intent__HassTurnOff",
        "description": "Turn off an entity",
        "parameters": schema,
    }
    result = execution_tool(tool, unique_named_target=True)

    assert "device_class" not in result["parameters"]["properties"]
    assert "device_class" in schema["properties"]
    assert result["parameters"]["required"] == ["name"]


def test_dont_strip_required_class_or_ambiguous_target() -> None:
    """No target guessing or removal of required native schema fields."""
    params = {
        "type": "object",
        "properties": {"name": {}, "device_class": {}},
        "required": ["device_class"],
    }
    tool = {"name": "test", "description": "test", "parameters": params}
    result = execution_tool(tool, unique_named_target=True)
    assert "device_class" in result["parameters"]["properties"]
    unchanged = execution_tool(tool, unique_named_target=False)
    assert unchanged["parameters"] is params


def test_unique_exposed_literal_name_across_languages() -> None:
    """Device labels remain literal even when user language differs."""
    entities = [
        {"name": "Wohnzimmerlampe", "entity_id": "light.wohnzimmerlampe"},
        {"name": "Hallway light", "entity_id": "light.hallway"},
    ]
    found = find_unique_mentioned_entity(
        "Turn off Wohnzimmerlampe", entities
    )
    assert found == entities[0]
    assert find_unique_mentioned_entity(
        "Turn off the hallway", entities
    ) is None


def test_similar_or_ambiguous_exposed_names_are_not_unique() -> None:
    """A short name inside another entity name is not sufficient."""
    entities = [
        {"name": "Tor", "entity_id": "switch.gate"},
        {"name": "Einfahrtstor", "entity_id": "switch.driveway"},
    ]
    assert find_unique_mentioned_entity(
        "Öffne das Einfahrtstor", entities
    ) == entities[1]
    assert find_unique_mentioned_entity(
        "Öffne Tor und Einfahrtstor", entities
    ) is None


def test_approval_tools_include_alternatives_without_schema_fields() -> None:
    """Approval should compare actions, not hallucinate optional settings."""
    tools = [
        {"name": "intent__HassTurnOn", "title": "Turn on",
         "description": "Turn on devices",
         "parameters": {"properties": {"area": {}, "domain": {}}}},
        {"name": "intent__HassTurnOff", "title": "Turn off",
         "description": "Turn off devices",
         "parameters": {"properties": {"area": {}, "domain": {}}}},
        {"name": "media_player__HassMediaNext", "title": "Next",
         "description": "Play next item", "parameters": {}},
    ]
    candidates = build_approval_tools(tools, "intent__HassTurnOn")
    assert [item["name"] for item in candidates] == [
        "intent__HassTurnOn", "intent__HassTurnOff",
    ]
    assert all(item["parameters"] == {"type": "object", "properties": {}}
               for item in candidates)


def test_approval_does_not_rubber_stamp_isolated_tool() -> None:
    """Keep genuine alternatives even for an isolated proposed family."""
    tools = [
        {"name": "light__HassLightSet", "description": "Adjust light"},
        {"name": "intent__HassTurnOn", "description": "Turn on"},
        {"name": "intent__HassTurnOff", "description": "Turn off"},
    ]
    assert len(build_approval_tools(tools, "light__HassLightSet")) == 3


def test_needle_negation_rejected_even_with_high_confidence() -> None:
    """An explicit negation cannot approve an execution."""
    result = _result(confidence=0.99)
    result["validation"]["negation"] = True
    with pytest.raises(RouteRejected, match="rejected the requested action"):
        approve_route(result, 0.80, allowed_tools=ALLOWED)



def test_approval_filters_unrelated_same_family_actions() -> None:
    """A timer action must not distract from the turn-on/off comparison."""
    tools = [
        {"name": "intent__HassTurnOn", "title": "Turn on"},
        {"name": "intent__HassTurnOff", "title": "Turn off"},
        {"name": "intent__HassCancelAllTimers", "title": "Cancel all timers"},
        {"name": "light__HassLightSet", "title": "Set light"},
    ]
    candidates = build_approval_tools(tools, "intent__HassTurnOn")
    assert [tool["name"] for tool in candidates] == [
        "intent__HassTurnOn",
        "intent__HassTurnOff",
    ]
    assert all(tool["parameters"] == {
        "type": "object", "properties": {}
    } for tool in candidates)


def test_approval_fallback_still_has_independent_choices() -> None:
    """Keep unrelated alternatives when no close action exists."""
    tools = [
        {"name": "intent__HassCancelAllTimers", "title": "Cancel timers"},
        {"name": "intent__HassTurnOn", "title": "Turn on"},
        {"name": "intent__HassTurnOff", "title": "Turn off"},
    ]
    candidates = build_approval_tools(
        tools, "intent__HassCancelAllTimers"
    )
    assert len(candidates) >= 2



def test_approval_preserves_native_description_with_short_title() -> None:
    """Needle should see action semantics, not just a two-word title."""
    tools = [
        {
            "name": "intent__HassTurnOn",
            "title": "Turn on",
            "description": "Activate a named device or all devices in an area.",
        },
        {
            "name": "intent__HassTurnOff",
            "title": "Turn off",
            "description": "Deactivate a named device or all devices in an area.",
        },
    ]
    proposed = build_approval_tools(tools, "intent__HassTurnOn")
    assert [item["name"] for item in proposed] == [
        "intent__HassTurnOn",
        "intent__HassTurnOff",
    ]
    assert "all devices in an area" in proposed[0]["description"]
    assert "all devices in an area" in proposed[1]["description"]
    assert proposed[0]["description"].startswith("Turn on.")
    assert proposed[1]["description"].startswith("Turn off.")
    assert all(
        tool["parameters"] == {"type": "object", "properties": {}}
        for tool in proposed
    )


def test_approval_description_avoids_duplicate_title() -> None:
    """Do not repeat identical titles and descriptions."""
    tools = [
        {
            "name": "intent__HassTurnOn",
            "title": "Turn on",
            "description": "Turn on",
        },
        {
            "name": "intent__HassTurnOff",
            "title": "Turn off",
            "description": "Turn off.",
        },
    ]
    candidates = build_approval_tools(tools, "intent__HassTurnOn")
    assert candidates[0]["description"] == "Turn on"
    assert candidates[1]["description"] == "Turn off"


def test_semantic_approval_aliases_use_real_native_titles() -> None:
    """Keep independent alternatives and map semantic names to native tools."""
    tools = [
        {"name": "intent__HassTurnOn", "title": "Turn on",
         "description": "Turn on devices"},
        {"name": "intent__HassTurnOff", "title": "Turn off",
         "description": "Turn off devices"},
    ]
    aliased, mapping = build_semantic_approval_tools(
        tools, "intent__HassTurnOn"
    )
    assert [item["name"] for item in aliased] == ["turn_on", "turn_off"]
    assert mapping == {
        "turn_on": "intent__HassTurnOn",
        "turn_off": "intent__HassTurnOff",
    }
    assert all(item["parameters"] == {
        "type": "object", "properties": {}
    } for item in aliased)


def test_semantic_aliases_fail_to_native_names_on_title_collision() -> None:
    """Duplicate titles must never cause a name to resolve to the wrong tool."""
    tools = [
        {"name": "intent__HassTurnOn", "title": "Operate"},
        {"name": "intent__HassTurnOff", "title": "Operate"},
    ]
    aliased, mapping = build_semantic_approval_tools(
        tools, "intent__HassTurnOn"
    )
    assert [item["name"] for item in aliased] == [
        "intent__HassTurnOn", "intent__HassTurnOff"
    ]
    assert mapping == {
        "intent__HassTurnOn": "intent__HassTurnOn",
        "intent__HassTurnOff": "intent__HassTurnOff",
    }


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.1, 1.1])
def test_nonfinite_or_out_of_range_confidence_rejected(invalid: float) -> None:
    """Nonfinite numbers must never bypass Needle's confidence gate."""
    with pytest.raises(RouteRejected, match="invalid confidence"):
        approve_route(_result(confidence=invalid), 0.8, allowed_tools=ALLOWED)



def test_semantic_aliases_derive_from_native_names_without_titles() -> None:
    """Actual HA native Assist tools can omit the title field entirely."""
    tools = [
        {"name": "intent__HassTurnOn",
         "description": "Turn on a device or area"},
        {"name": "intent__HassTurnOff",
         "description": "Turn off a device or area"},
    ]
    aliased, mapping = build_semantic_approval_tools(
        tools, "intent__HassTurnOn"
    )
    assert [item["name"] for item in aliased] == ["turn_on", "turn_off"]
    assert mapping == {
        "turn_on": "intent__HassTurnOn",
        "turn_off": "intent__HassTurnOff",
    }
    assert "Turn on a device" in aliased[0]["description"]
    assert "Turn off a device" in aliased[1]["description"]
    assert all(item["parameters"] == {
        "type": "object", "properties": {}
    } for item in aliased)


def test_native_aliases_keep_unique_action_when_namespaces_differ() -> None:
    """Never strip away unrelated action words or change the meaning."""
    tools = [
        {"name": "intent__HassTurnOn", "description": "Turn on"},
        {"name": "media__PlayMedia", "description": "Play media"},
    ]
    result, mapping = build_semantic_approval_tools(
        tools, "media__PlayMedia"
    )
    assert set(mapping.values()) == set(tool["name"] for tool in tools)
    assert [tool["name"] for tool in result] == [
        "hass_turn_on", "play_media"
    ]


def test_native_aliases_safely_fall_back_on_collisions() -> None:
    """Two native tool names deriving the same action must not be conflated."""
    tools = [
        {"name": "intent__HassTurnOn", "description": "Turn on"},
        {"name": "other__HassTurnOn", "description": "Turn on other"},
    ]
    result, mapping = build_semantic_approval_tools(
        tools, "intent__HassTurnOn"
    )
    assert [tool["name"] for tool in result] == [
        "intent__HassTurnOn", "other__HassTurnOn"
    ]
    assert mapping == {
        "intent__HassTurnOn": "intent__HassTurnOn",
        "other__HassTurnOn": "other__HassTurnOn",
    }
