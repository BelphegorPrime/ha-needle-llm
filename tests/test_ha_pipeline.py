"""Regression tests for existing-HA-provider Needle-approval routing."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    name: str, *, confidence: float = 0.97, approval: bool = True
) -> dict:
    if approval:
        # Only independent approval sees semantic aliases. Discovery still
        # uses the native Home Assistant tool names.
        name = {
            "intent__HassTurnOn": "turn_on",
            "intent__HassTurnOff": "turn_off",
        }.get(name, name)
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
            (_needle_call(selected, approval=False), {"complete_ms": 8}),
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
    # A low-confidence agreement may make one non-executable translation
    # request, but must never reach native argument generation.
    assert provider.async_complete.await_count == 2
    assert provider.async_complete.await_args.kwargs["stage"] == (
        "approval_translation"
    )
    needle.async_complete.assert_awaited_once()


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
    assert diag["needle_approval"]["original_confidence"] == 0.222
    assert diag["needle_approval"]["confidence"] == 0.94
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
async def test_explicit_negation_never_calls_provider_or_needle() -> None:
    """The deterministic preflight rejects negation before any network call."""
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
    provider.async_complete.assert_not_awaited()
    needle.async_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_english_retry_protects_real_entity_and_area_names() -> None:
    """Reproduce v0.5.4: 'Licht' is also an exposed entity name."""
    hass, context, needle, provider = _env()
    context.language = "en"  # Mirrors the actual trace despite German input.
    hass.states.async_all.return_value = [
        SimpleNamespace(
            name="Licht",
            entity_id="light.licht",
            attributes={},
        )
    ]
    needle.async_complete = AsyncMock(
        side_effect=[
            (_needle_call("intent__HassTurnOn", confidence=0.2201), {}),
            (_needle_call("intent__HassTurnOn", confidence=0.95), {}),
        ]
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "NeedleTranslateToEnglish",
                    {"english_query": "Turn on HA_LITERAL_0 in HA_LITERAL_1"},
                ),
                {},
            ),
            (
                _model_call(
                    "intent__HassTurnOn",
                    {"area": "wohnzimmer", "domain": ["light"]},
                ),
                {},
            ),
        ]
    )
    diagnostics: dict = {}
    with (
        patch(
            "custom_components.needle_llm.ha_pipeline.async_should_expose",
            return_value=True,
        ),
        patch(
            "custom_components.needle_llm.ha_pipeline.area_registry.async_get"
        ) as get_areas,
    ):
        get_areas.return_value.async_list_areas.return_value = [
            SimpleNamespace(name="Wohnzimmer")
        ]
        result = await async_provider_route(
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

    translation_request = provider.async_complete.await_args_list[1].kwargs
    assert translation_request["query"] == (
        "schalte HA_LITERAL_0 im HA_LITERAL_1 ein"
    )
    assert needle.async_complete.await_args_list[1].kwargs["query"] == (
        "Turn on licht in wohnzimmer"
    )
    assert result.route.tool == "intent__HassTurnOn"
    assert result.route.arguments == {
        "area": "wohnzimmer", "domain": ["light"]
    }
    fallback = diagnostics["needle_approval"]["english_fallback"]
    assert fallback["accepted"] is True
    assert fallback["protected_name_count"] == 2
    assert fallback["english_query"] == "Turn on licht in wohnzimmer"


@pytest.mark.asyncio
async def test_english_retry_rejects_duplicate_protected_name() -> None:
    """Cannot repeat a Home Assistant name during translation."""
    hass, context, needle, provider = _env()
    hass.states.async_all.return_value = [
        SimpleNamespace(name="Licht", entity_id="light.licht", attributes={})
    ]
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("intent__HassTurnOn", confidence=0.22), {})
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (
                _model_call(
                    "NeedleTranslateToEnglish",
                    {"english_query": "Turn on HA_LITERAL_0 and HA_LITERAL_0"},
                ),
                {},
            ),
        ]
    )
    diagnostics: dict = {}
    with patch(
        "custom_components.needle_llm.ha_pipeline.async_should_expose",
        return_value=True,
    ):
        with pytest.raises(PipelineRejected, match="below"):
            await async_provider_route(
                hass=hass,
                context=context,
                query="schalte licht ein",
                tools=TOOLS,
                needle=needle,
                provider=provider,
                strategy=STRATEGY_MODEL_FIRST,
                minimum_confidence=0.8,
                diagnostics=diagnostics,
            )
    assert needle.async_complete.await_count == 1
    assert diagnostics["needle_approval"]["english_fallback"]["accepted"] is False
    assert "duplicated" in diagnostics["needle_approval"]["english_fallback"]["reason"]


@pytest.mark.asyncio
async def test_semantic_approval_aliases_map_back_to_native_tool() -> None:
    """Title-less native Assist tools use safe action aliases too."""
    hass, context, needle, provider = _env()
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("turn_on", confidence=0.94), {})
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (_model_call("intent__HassTurnOn", {
                "area": "Wohnzimmer", "domain": ["light"]
            }), {}),
        ]
    )
    diagnostics: dict = {}
    result = await async_provider_route(
        hass=hass, context=context,
        query="schalte licht im wohnzimmer ein",
        tools=TOOLS,
        needle=needle, provider=provider,
        strategy=STRATEGY_MODEL_FIRST,
        minimum_confidence=0.8,
        diagnostics=diagnostics,
    )
    assert result.route.tool == "intent__HassTurnOn"
    assert result.route.arguments == {
        "area": "Wohnzimmer", "domain": ["light"]
    }
    approval = needle.async_complete.await_args.kwargs["tools"]
    assert [tool["name"] for tool in approval] == ["turn_on", "turn_off"]
    assert all(tool["parameters"] == {
        "type": "object", "properties": {}
    } for tool in approval)
    assert diagnostics["needle_approval"]["function_calls"][0]["name"] == (
        "intent__HassTurnOn"
    )
    assert diagnostics["needle_approval"]["approval_aliases"] == {
        "turn_on": "intent__HassTurnOn",
        "turn_off": "intent__HassTurnOff",
    }


@pytest.mark.asyncio
async def test_semantic_approval_alias_disagreement_stays_blocked() -> None:
    """A confident competing semantic alias is not an approval."""
    hass, context, needle, provider = _env()
    titled_tools = [
        {**tool, "title": "Turn off" if tool["name"].endswith("Off") else "Turn on"}
        for tool in TOOLS
    ]
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("turn_off", confidence=0.99), {})
    )
    provider.async_complete = AsyncMock(
        return_value=(_model_call("intent__HassTurnOn"), {})
    )
    with pytest.raises(PipelineRejected, match="different native Assist"):
        await async_provider_route(
            hass=hass, context=context,
            query="schalte licht im wohnzimmer ein",
            tools=titled_tools,
            needle=needle, provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics={},
        )
    assert provider.async_complete.await_count == 1


@pytest.mark.asyncio
async def test_semantic_approval_alias_low_confidence_stays_blocked() -> None:
    """The semantic tool spelling must not bypass the 0.8 gate."""
    hass, context, needle, provider = _env()
    titled_tools = [
        {**tool, "title": "Turn off" if tool["name"].endswith("Off") else "Turn on"}
        for tool in TOOLS
    ]
    needle.async_complete = AsyncMock(
        return_value=(_needle_call("turn_on", confidence=0.21), {})
    )
    provider.async_complete = AsyncMock(
        side_effect=[
            (_model_call("intent__HassTurnOn"), {}),
            (_model_call("NeedleTranslateToEnglish", {
                "english_query": "Turn on the lights in Wohnzimmer"
            }), {}),
        ]
    )
    diagnostics: dict = {}
    with pytest.raises(PipelineRejected, match="below"):
        await async_provider_route(
            hass=hass, context=context,
            query="schalte licht im wohnzimmer ein",
            tools=titled_tools,
            needle=needle, provider=provider,
            strategy=STRATEGY_MODEL_FIRST,
            minimum_confidence=0.8,
            diagnostics=diagnostics,
        )
    assert provider.async_complete.await_count == 2
    assert diagnostics["needle_approval"]["english_fallback"]["accepted"] is False
