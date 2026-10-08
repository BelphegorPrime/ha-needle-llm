"""Guided configuration schema and execution trace regression tests."""

from __future__ import annotations

import voluptuous as vol

from custom_components.needle_llm.config_flow import (
    _mode_schema,
    _settings_schema,
)
from custom_components.needle_llm.const import (
    BACKEND_HA_PROVIDER,
    BACKEND_NEEDLE,
    BACKEND_OPENAI_COMPATIBLE,
    CONF_BACKEND,
    CONF_MIN_CONFIDENCE,
    CONF_MODEL,
    CONF_PROVIDER_MODEL,
    CONF_ROUTING_STRATEGY,
    CONF_TIMEOUT,
)
from custom_components.needle_llm.trace import build_routing_trace


def _fields(schema: vol.Schema) -> set[str]:
    """Get declared form fields independently of voluptuous markers."""
    return {
        key.schema if isinstance(key, vol.Marker) else key
        for key in schema.schema
    }


def test_choose_mode_on_separate_first_page() -> None:
    """Users should not need to understand server options before choosing."""
    assert _fields(_mode_schema(BACKEND_HA_PROVIDER)) == {CONF_BACKEND}


def test_ha_provider_only_shows_relevant_fields() -> None:
    """Provider mode must not ask for a duplicate model ID or API key."""
    form = _settings_schema(
        backend=BACKEND_HA_PROVIDER,
        provider_choices={"entry:subentry": "Living-room Qwen"},
        values={},
    )
    fields = _fields(form)
    assert CONF_PROVIDER_MODEL in fields
    assert CONF_ROUTING_STRATEGY in fields
    assert CONF_MIN_CONFIDENCE in fields
    assert CONF_TIMEOUT in fields
    assert CONF_MODEL not in fields
    assert "url" in fields
    assert form(
        {
            "provider_model": "entry:subentry",
            "routing_strategy": "model_preselection",
            "url": "http://needle:7860",
            "min_confidence": 0.8,
            "timeout": 30,
        }
    )["provider_model"] == "entry:subentry"


def test_needle_only_has_no_provider_model() -> None:
    """Standalone Needle does not show unrelated settings."""
    fields = _fields(
        _settings_schema(
            backend=BACKEND_NEEDLE, provider_choices={}, values={}
        )
    )
    assert fields == {"url", CONF_MIN_CONFIDENCE, CONF_TIMEOUT}


def test_direct_openai_never_shows_needle_confidence() -> None:
    """Do not imply direct model tool calls provide Needle confidence."""
    fields = _fields(
        _settings_schema(
            backend=BACKEND_OPENAI_COMPATIBLE,
            provider_choices={},
            values={},
        )
    )
    assert fields == {"url", CONF_MODEL, CONF_TIMEOUT}


def test_trace_exposes_actual_routing_stages_and_timings() -> None:
    """A completed trace displays selection, approval and HA execution."""
    diag = {
        "backend": BACKEND_HA_PROVIDER,
        "routing_strategy": "model_preselection",
        "available_tool_count": 21,
        "minimum_confidence": 0.8,
        "preselection": {
            "source": "model",
            "candidate": "intent__HassTurnOff",
            "transport": {"complete_ms": 123.4},
        },
        "needle_approval": {
            "candidate": "intent__HassTurnOff",
            "confidence": 0.92,
            "function_calls": [
                {"name": "intent__HassTurnOff", "arguments": {}}
            ],
            "transport": {"complete_ms": 1100},
            "validation": {"ungrounded": []},
        },
        "route": {
            "transport": {"complete_ms": 188},
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "intent__HassTurnOff",
                        "arguments": '{"name":"Wohnzimmerlampe"}',
                    },
                }
            ],
        },
        "ha_execution_ms": 12,
    }
    result = build_routing_trace(
        diag,
        status="success",
        stage="completed",
        selected_tool="intent__HassTurnOff",
        arguments={"name": "Wohnzimmerlampe"},
        home_assistant={"response_type": "action_done"},
        total_ms=1550,
    )
    assert result["status"] == "success"
    assert result["total_ms"] == 1550
    assert [x["step"] for x in result["steps"]] == [
        "tool_preselection",
        "needle_approval",
        "argument_generation",
        "home_assistant",
    ]
    assert result["steps"][0]["performed_by"] == "model"
    assert result["steps"][1]["status"] == "approved"
    assert result["steps"][1]["confidence"] == 0.92
    assert result["steps"][3]["status"] == "executed"
    assert result["steps"][3]["duration_ms"] == 12


def test_rejected_trace_must_not_claim_executed_action() -> None:
    """No missing-device story before Home Assistant execution occurs."""
    result = build_routing_trace(
        {
            "backend": BACKEND_HA_PROVIDER,
            "needle_approval": {
                "candidate": "intent__HassTurnOff",
                "confidence": 0.1,
                "function_calls": [],
                "transport": {"complete_ms": 1400},
            },
        },
        status="rejected",
        stage="needle_approval",
    )
    assert result["failed_at"] == "needle_approval"
    assert result["steps"][0]["status"] == "rejected"
    assert "home_assistant" not in {
        step["step"] for step in result["steps"]
    }
