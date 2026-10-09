"""Regression tests for existing-HA-provider Needle-approval routing."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.needle_llm.ha_pipeline import (
    STRATEGY_MODEL_FIRST,
    STRATEGY_NEEDLE_FIRST,
    PipelineRejected,
    async_provider_route,
)
from custom_components.needle_llm.ha_provider import (
    ProviderError,
    decode_selection,
    encode_selection,
    resolve_provider,
)


def _model_call(name: str, args: dict | None = None) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args or {}),
                            },
                        }
                    ]
                },
            }
        ]
    }


def _needle_call(
    name: str, *, confidence: float = 0.97
) -> dict:
    return {
        "success": True,
        "confidence": confidence,
        "function_calls": [{"name": name, "arguments": {}}],
        "validation": {"ungrounded": []},
    }


TOOLS = [
    {
        "name": "intent__HassTurnOff",
        "description": "Turn off the named device",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
    {
        "name": "intent__HassTurnOn",
        "description": "Turn on the named device",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
]


def _env() -> tuple:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    context = SimpleNamespace(language="de-DE", assistant="conversation")
    needle = MagicMock()
    provider = MagicMock()
    return hass, context, needle, provider


@pytest.mark.parametrize(
    "strategy", [STRATEGY_NEEDLE_FIRST, STRATEGY_MODEL_FIRST]
)
@pytest.mark.asyncio
async def test_provider_requires_independent_needle_approval(strategy: str) -> None:
    """Both strategies end with a separately approved, single tool."""
    hass, context, needle, provider = _env()
    selected = "intent__HassTurnOff"
    needle.async_complete = AsyncMock(
        side_effect=[
            (_needle_call(selected), {"complete_ms": 8}),
            (_needle_call(selected), {"complete_ms": 9}),
        ]
        if strategy == STRATEGY_NEEDLE_FIRST
        else [(_needle_call(selected), {"complete_ms": 9})]
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call(selected), {"complete_ms": 11}),
            (_model_call(selected, {"name": "Wohnzimmerlampe"}), {"complete_ms": 12}),
        ]
        if strategy == STRATEGY_MODEL_FIRST
        else [
            (
                _model_call(selected, {"name": "Wohnzimmerlampe"}),
                {"complete_ms": 12},
            )
        ]
    )
    diag: dict = {}
    result = await async_provider_route(
        hass=hass,
        context=context,
        query="Schalte die Wohnzimmerlampe aus",
        tools=TOOLS,
        needle=needle,
        provider=provider,
        strategy=strategy,
        minimum_confidence=0.8,
        diagnostics=diag,
    )
    assert result.route.tool == selected
    assert result.route.arguments == {"name": "Wohnzimmerlampe"}
    assert result.route.confidence is None
    assert diag["needle_approval"]["confidence"] == 0.97
    assert diag["needle_approval"]["query_mode"] == "original_user_request"
    assert diag["needle_approval"]["candidate_tool_count"] == 2
    assert needle.async_complete.await_args.kwargs["query"] == (
        "Schalte die Wohnzimmerlampe aus"
    )
    assert diag["preselection"]["source"] in ("model", "needle")
    assert provider.async_complete.await_args.kwargs["tools"][0]["name"] == selected
    assert len(provider.async_complete.await_args.kwargs["tools"]) == 1


@pytest.mark.asyncio
async def test_model_and_needle_disagreement_never_reaches_final_call() -> None:
    """Needle does not rubber-stamp a model's different tool choice."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("intent__HassTurnOff"), {})
    )
    provider.async_complete = AsyncMock(
        return_value=(_model_call("intent__HassTurnOn"), {})
    )
    with pytest.raises(PipelineRejected, match="different native Assist"):
        await async_provider_route(
            hass=hass,
            context=context,
            query="Schalte die Wohnzimmerlampe aus",
            tools=TOOLS,
            needle=needle,
            provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics={},
        )
    assert provider.async_complete.await_count == 1


@pytest.mark.asyncio
async def test_low_needle_confidence_prevents_final_model_call() -> None:
    """A fast model cannot bypass the Needle confidence gate."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("intent__HassTurnOff", confidence=0.3), {})
    )
    provider.async_complete = AsyncMock(
        return_value=(_model_call("intent__HassTurnOff"), {})
    )
    with pytest.raises(PipelineRejected, match="below"):
        await async_provider_route(
            hass=hass,
            context=context,
            query="Schalte die Wohnzimmerlampe aus",
            tools=TOOLS,
            needle=needle,
            provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics={},
        )
    assert provider.async_complete.await_count == 1


def test_provider_selection_requires_entry_and_model() -> None:
    """Provider selections reference both parent entry and conversation model."""
    assert decode_selection(encode_selection("abc", "def")).entry_id == "abc"
    with pytest.raises(ProviderError):
        decode_selection("abc")


def test_provider_rejects_missing_entry_before_accessing_runtime() -> None:
    """Missing or removed provider fails closed."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = None
    with pytest.raises(ProviderError, match="unavailable"):
        resolve_provider(hass, "abc:def")



def test_provider_api_id_does_not_conflict_with_needle_api_id() -> None:
    """Two router entries may refer to the same local Needle server."""
    from custom_components.needle_llm.llm_api import (
        api_id_for_router,
        api_id_for_url,
    )

    url = "http://needle:7860"
    assert api_id_for_router(url, "needle", "needle-entry") == api_id_for_url(
        url
    )
    first = api_id_for_router(url, "ha_provider", "provider-entry-1")
    second = api_id_for_router(url, "ha_provider", "provider-entry-2")
    assert first != second
    assert first != api_id_for_url(url)


@pytest.mark.asyncio
async def test_room_wide_request_uses_area_and_domain_not_invented_name() -> None:
    """The model's area-based arguments survive a Needle tool approval."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("intent__HassTurnOn"), {})
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "intent__HassTurnOn",
                    {"area": "Wohnzimmer", "domain": ["light"]},
                ),
                {},
            ),
        ]
    )
    result = await async_provider_route(
        hass=hass,
        context=context,
        query="Schalte Licht im Wohnzimmer ein",
        tools=TOOLS,
        needle=needle,
        provider=provider,
        strategy=STRATEGY_MODEL_FIRST,
        minimum_confidence=0.8,
        diagnostics={},
    )
    assert result.route.arguments == {
        "area": "Wohnzimmer",
        "domain": ["light"],
    }


@pytest.mark.asyncio
async def test_low_confidence_agreement_recovers_after_english_approval() -> None:
    """Original-language disagreement cannot be bypassed; agreement can retry."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        side_effect=[
            (_needle_call("intent__HassTurnOn", confidence=0.222), {}),
            (_needle_call("intent__HassTurnOn", confidence=0.94), {}),
        ]
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "NeedleTranslateToEnglish",
                    {"english_query": "Turn on the lights in Wohnzimmer"},
                ),
                {},
            ),
            (
                _model_call(
                    "intent__HassTurnOn",
                    {"area": "Wohnzimmer", "domain": ["light"]},
                ),
                {},
            ),
        ]
    )
    diag: dict = {}
    result = await async_provider_route(
        hass=hass,
        context=context,
        query="schalte licht im wohnzimmer ein",
        tools=TOOLS,
        needle=needle,
        provider=provider,
        strategy=STRATEGY_MODEL_FIRST,
        minimum_confidence=0.8,
        diagnostics=diag,
    )
    assert result.route.tool == "intent__HassTurnOn"
    assert result.route.arguments == {"area": "Wohnzimmer", "domain": ["light"]}
    assert diag["needle_approval"]["confidence"] == 0.222
    assert diag["needle_approval"]["english_fallback"]["accepted"] is True
    assert diag["needle_approval"]["english_fallback"]["confidence"] == 0.94
    assert needle.async_complete.await_args.kwargs["stage"] == "approval_english"
    assert provider.async_complete.await_args.kwargs["query"] == (
        "schalte licht im wohnzimmer ein"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secondary_tool,confidence",
    [
        ("intent__HassTurnOff", 0.99),
        ("intent__HassTurnOn", 0.55),
    ],
)
async def test_english_fallback_rejects_conflicts_and_low_confidence(
    secondary_tool: str, confidence: float
) -> None:
    """The translation is never a permission to bypass original safety."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        side_effect=[
            (_needle_call("intent__HassTurnOn", confidence=0.222), {}),
            (_needle_call(secondary_tool, confidence=confidence), {}),
        ]
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "NeedleTranslateToEnglish",
                    {"english_query": "Turn on the lights in Wohnzimmer"},
                ),
                {},
            ),
        ]
    )
    diagnostics: dict = {}
    with pytest.raises(PipelineRejected, match="below"):
        await async_provider_route(
            hass=hass,
            context=context,
            query="schalte licht im wohnzimmer ein",
            tools=TOOLS,
            needle=needle,
            provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics=diagnostics,
        )
    assert provider.async_complete.await_count == 2
    assert diagnostics["needle_approval"]["english_fallback"]["accepted"] is False


@pytest.mark.asyncio
async def test_english_fallback_cannot_translate_exposed_names() -> None:
    """Reject translation that renames an actual Assist-exposed entity."""
    hass, context, needle, provider = _env()
    hass.states.async_all.return_value = [
        SimpleNamespace(
            name="Wohnzimmerlampe",
            entity_id="light.wohnzimmer",
        ),
    ]
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("intent__HassTurnOn", confidence=0.222), {})
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "NeedleTranslateToEnglish",
                    {"english_query": "Turn on the living room lamp"},
                ),
                {},
            ),
        ]
    )
    with pytest.raises(PipelineRejected, match="below"):
        await async_provider_route(
            hass=hass,
            context=context,
            query="schalte die Wohnzimmerlampe ein",
            tools=TOOLS,
            needle=needle,
            provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics={},
        )
    needle.async_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_needle_negation_never_triggers_translation() -> None:
    """Never use translated text to override an explicit native refusal."""
    hass, context, needle, provider = _env()
    negative = _needle_call("intent__HassTurnOn", confidence=0.222)
    negative["validation"]["negation"] = True
    needle.async_complete = AsyncMock(return_value=(negative, {}))
    provider.async_complete = AsyncMock(
        return_value=(_model_call("intent__HassTurnOn"), {})
    )
    with pytest.raises(PipelineRejected):
        await async_provider_route(
            hass=hass,
            context=context,
            query="schalte licht nicht ein",
            tools=TOOLS,
            needle=needle,
            provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics={},
        )
    assert provider.async_complete.await_count == 1
