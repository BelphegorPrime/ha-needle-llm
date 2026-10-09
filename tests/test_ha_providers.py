"""Cross-version capability detection and native HA model adapters."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.config_entries import ConfigEntryState

from custom_components.needle_llm.ha_provider import (
    HomeAssistantModelProvider,
    ProviderError,
    _normalize_ollama,
    _normalize_openai_response,
    available_provider_models,
    encode_selection,
    resolve_provider,
)
from custom_components.needle_llm.validation import approve_openai_route


def _entry(
    domain: str,
    key: str,
    client: object,
    *,
    with_subentries: bool = True,
) -> SimpleNamespace:
    """Build a loaded provider resembling HA 2025.7+ configurations."""
    subentry = SimpleNamespace(
        subentry_id="model-1",
        subentry_type="conversation",
        data={key: "test-model"},
        title="Test model",
    )
    kwargs = {
        "domain": domain,
        "entry_id": f"{domain}-1",
        "title": "Configured provider",
        "disabled_by": None,
        "state": ConfigEntryState.LOADED,
        "runtime_data": client,
    }
    if with_subentries:
        kwargs["subentries"] = {"model-1": subentry}
    return SimpleNamespace(**kwargs)


def _ha(*entries: SimpleNamespace) -> MagicMock:
    """Return entries for each provider domain."""
    hass = MagicMock()
    by_id = {entry.entry_id: entry for entry in entries}
    hass.config_entries.async_entries.side_effect = (
        lambda domain: [e for e in entries if e.domain == domain]
    )
    hass.config_entries.async_get_entry.side_effect = by_id.get
    return hass


def _client(kind: str) -> SimpleNamespace:
    """Expose only the provider-specific model proposal interface."""
    if kind == "llama_cpp":
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock())
            )
        )
    if kind == "openai_conversation":
        return SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock())
        )
    return SimpleNamespace(chat=AsyncMock())


def _model_response(data: dict) -> SimpleNamespace:
    return SimpleNamespace(model_dump=lambda **kwargs: data)


@pytest.mark.parametrize(
    ("domain", "key"),
    [
        ("llama_cpp", "chat_model"),
        ("ollama", "model"),
        ("openai_conversation", "chat_model"),
    ],
)
def test_detect_existing_provider_without_fixed_ha_version(
    domain: str, key: str
) -> None:
    """2025.7+ style conversation models are picked up by capability."""
    entry = _entry(domain, key, _client(domain))
    hass = _ha(entry)
    choices = available_provider_models(hass)
    selection = encode_selection(entry.entry_id, "model-1")
    assert selection in choices
    assert "test-model" in choices[selection]
    assert resolve_provider(hass, selection)[1] == "test-model"


@pytest.mark.parametrize(
    ("domain", "key"),
    [
        ("ollama", "model"),
        ("openai_conversation", "chat_model"),
    ],
)
def test_pre_subentry_versions_are_not_offered(
    domain: str, key: str
) -> None:
    """HA 2025.6 and older may expose different settings; hide provider."""
    entry = _entry(
        domain, key, _client(domain), with_subentries=False
    )
    hass = _ha(entry)
    assert available_provider_models(hass) == {}
    with pytest.raises(ProviderError, match="does not expose"):
        resolve_provider(hass, encode_selection(entry.entry_id, "model-1"))


def test_missing_model_client_or_subentry_is_not_listed() -> None:
    """An unavailable interface must not appear in the UI."""
    entry = _entry(
        "openai_conversation", "chat_model", SimpleNamespace()
    )
    assert available_provider_models(_ha(entry)) == {}


def test_disabled_model_is_not_listed() -> None:
    """Do not allow an inactive HA config entry as a routing provider."""
    entry = _entry("ollama", "model", _client("ollama"))
    entry.disabled_by = "user"
    assert available_provider_models(_ha(entry)) == {}


def test_ollama_normalization_retains_function_and_json_arguments() -> None:
    """Ollama's native dictionary tool args become a guarded JSON call."""
    response = _model_response(
        {
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "intent__HassTurnOff",
                            "arguments": {"name": "Wohnzimmerlampe"},
                        }
                    }
                ]
            },
            "done_reason": "stop",
        }
    )
    result = _normalize_ollama(response)
    call = approve_openai_route(
        result, allowed_tools={"intent__HassTurnOff"}
    )
    assert call.arguments == {"name": "Wohnzimmerlampe"}


def test_openai_responses_normalizes_function_calls() -> None:
    """OpenAI's Responses API is not the Chat Completions format."""
    response = _model_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "intent__HassTurnOff",
                    "arguments": json.dumps({"name": "Wohnzimmerlampe"}),
                }
            ],
        }
    )
    result = _normalize_openai_response(response)
    call = approve_openai_route(
        result, allowed_tools={"intent__HassTurnOff"}
    )
    assert call.tool == "intent__HassTurnOff"
    assert call.arguments == {"name": "Wohnzimmerlampe"}


def test_incomplete_openai_responses_fail_closed() -> None:
    """Do not execute tool proposals from truncated/unfinished output."""
    response = _model_response(
        {
            "status": "incomplete",
            "output": [
                {
                    "type": "function_call",
                    "name": "intent__HassTurnOff",
                    "arguments": "{}",
                }
            ],
        }
    )
    result = _normalize_openai_response(response)
    with pytest.raises(Exception, match="incomplete"):
        approve_openai_route(result, allowed_tools={"intent__HassTurnOff"})


@pytest.mark.parametrize(
    ("domain", "key"),
    [
        ("llama_cpp", "chat_model"),
        ("ollama", "model"),
        ("openai_conversation", "chat_model"),
    ],
)
@pytest.mark.asyncio
async def test_provider_runs_model_only_and_does_not_execute_ha_tools(
    domain: str, key: str
) -> None:
    """Provider calls are proposals, not HA device execution."""
    client = _client(domain)
    if domain == "llama_cpp":
        fn = client.chat.completions.create
        fn.return_value = _model_response(
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "intent__HassTurnOff",
                                        "arguments": '{"name":"Wohnzimmerlampe"}',
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        )
    elif domain == "ollama":
        fn = client.chat
        fn.return_value = _model_response(
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "intent__HassTurnOff",
                                "arguments": {"name": "Wohnzimmerlampe"},
                            }
                        }
                    ]
                },
                "done_reason": "stop",
            }
        )
    else:
        fn = client.responses.create
        fn.return_value = _model_response(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "intent__HassTurnOff",
                        "arguments": '{"name":"Wohnzimmerlampe"}',
                    }
                ],
            }
        )

    entry = _entry(domain, key, client)
    hass = _ha(entry)
    provider = HomeAssistantModelProvider(
        hass, encode_selection(entry.entry_id, "model-1"), 60
    )
    result, diagnostics = await provider.async_complete(
        tools=[
            {
                "name": "intent__HassTurnOff",
                "description": "Turn off",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        query="Schalte die Wohnzimmerlampe aus",
        language="de-DE",
        stage="arguments",
    )
    route = approve_openai_route(
        result, allowed_tools={"intent__HassTurnOff"}
    )
    assert route.arguments == {"name": "Wohnzimmerlampe"}
    assert diagnostics["provider"] == domain
    fn.assert_awaited_once()
    # No Home Assistant service/tool execution is part of the provider.
    assert not hass.services.async_call.called


def test_openai_rejects_plain_chat_only_client() -> None:
    """An OpenAI entry without its Responses API must not be selectable."""
    entry = _entry(
        "openai_conversation",
        "chat_model",
        _client("llama_cpp"),
    )
    assert available_provider_models(_ha(entry)) == {}
