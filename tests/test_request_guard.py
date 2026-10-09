"""Six-language fail-closed tests for explicit no-action utterances."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.needle_llm.ha_pipeline import (
    PipelineRejected,
    async_provider_route,
)
from custom_components.needle_llm.request_guard import no_action_reason


@pytest.mark.parametrize("query", [
    "Öffne bitte das Einfahrtstor, nicht die Haustür",
    "Open the driveway gate, not the front door",
    "Ouvre le portail de l'allée, pas la porte d'entrée",
    "Abre la puerta de entrada de coches, no la puerta de casa",
    "Apri il cancello del vialetto, non la porta di casa",
    "Open de opritpoort, niet de voordeur",
    "Setze bloß keinen Timer für zehn Minuten",
    "Definitely don't set a timer for ten minutes",
    "Ne mets surtout pas de minuteur de dix minutes",
    "Ni se te ocurra poner un temporizador de diez minutos",
    "Non impostare assolutamente un timer di dieci minuti",
    "Zet beslist geen timer van tien minuten",
    "Wenn du könntest, würdest du das Haustürschloss entriegeln?",
    "If you could, would you unlock the front door?",
    "Si tu pouvais, déverrouillerais-tu la porte d'entrée ?",
    "Si pudieras, ¿abrirías la cerradura de la puerta?",
    "Se potessi, sbloccheresti la porta d'ingresso?",
    "Als je kon, zou je de voordeur ontgrendelen?",
])
def test_known_critical_no_action_examples_blocked(query):
    assert no_action_reason(query) is not None


@pytest.mark.parametrize("query", [
    "Kannst du bitte das Licht im Flur einschalten?",
    "Could you turn on the hallway light now, please?",
    "Peux-tu allumer la lumière de l'entrée, s'il te plaît ?",
    "¿Puedes encender la luz de la cocina ahora?",
    "Puoi accendere la luce dello studio, per favore?",
    "Kun je het licht in de woonkamer aandoen?",
    "Bitte verriegle jetzt die Haustür.",
    "Would you please lock the side door?",
    "Starte einen Timer für drei Minuten.",
])
def test_positive_commands_and_polite_questions_not_blocked(query):
    assert no_action_reason(query) is None


@pytest.mark.asyncio
async def test_provider_pipeline_fails_before_any_network_or_ha_access():
    with pytest.raises(PipelineRejected, match="Explicit prohibition"):
        await async_provider_route(
            hass=None,
            context=SimpleNamespace(language="de"),
            query="Bitte das Schloss nicht entriegeln",
            tools=[],
            needle=None,
            provider=None,
            strategy="needle_preselection",
            minimum_confidence=.8,
            diagnostics={},
        )


def test_unknown_request_does_not_gain_any_action():
    assert no_action_reason("Ich hätte gern den Status") is None
    assert no_action_reason("") is not None
