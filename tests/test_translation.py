"""Tests for language-independent Home Assistant literal name masking."""

import pytest

from custom_components.needle_llm.translation import (
    TranslationRejected,
    mask_literal_names,
    restore_literal_names,
)


def test_generic_light_word_that_is_also_an_entity_is_protected() -> None:
    """A real entity named 'Licht' must not block the English fallback."""
    source = "schalte licht im wohnzimmer ein"
    masked, literals = mask_literal_names(source, ["Licht", "Wohnzimmer"])
    assert masked == "schalte HA_LITERAL_0 im HA_LITERAL_1 ein"
    assert literals == {"HA_LITERAL_0": "licht", "HA_LITERAL_1": "wohnzimmer"}
    assert restore_literal_names(
        "Turn on HA_LITERAL_0 in HA_LITERAL_1", literals
    ) == "Turn on licht in wohnzimmer"


def test_longest_entity_name_wins_and_preserves_repeated_mentions() -> None:
    """Overlapping labels and multiple occurrences never produce mixed names."""
    source = "Schalte Wohnzimmerlampe und Wohnzimmerlampe ein"
    masked, literals = mask_literal_names(
        source, ["Lampe", "Wohnzimmerlampe", "Wohnzimmer"]
    )
    assert masked == "Schalte HA_LITERAL_0 und HA_LITERAL_1 ein"
    assert restore_literal_names(
        "Turn on HA_LITERAL_0 and HA_LITERAL_1", literals
    ) == "Turn on Wohnzimmerlampe and Wohnzimmerlampe"


@pytest.mark.parametrize(
    "translation",
    [
        "Turn on the living room light",
        "Turn on HA_LITERAL_0 and HA_LITERAL_0",
        "Turn on HA_LITERAL_0 and HA_LITERAL_1",
    ],
)
def test_missing_duplicated_or_invented_literal_is_rejected(
    translation: str,
) -> None:
    """A model cannot remove a protected target or invent another target."""
    _, literals = mask_literal_names("Wohnzimmerlampe ein", ["Wohnzimmerlampe"])
    with pytest.raises(TranslationRejected):
        restore_literal_names(translation, literals)


def test_nonmatching_partial_name_is_not_protected() -> None:
    """Do not treat the 'Licht' substring in 'Lichtschalter' as a name."""
    query, names = mask_literal_names(
        "Schalte Lichtschalter ein", ["Licht"]
    )
    assert query == "Schalte Lichtschalter ein"
    assert names == {}


def test_reserved_placeholder_in_user_request_is_rejected() -> None:
    """Never allow user text to collide with internally generated markers."""
    with pytest.raises(TranslationRejected, match="reserved"):
        mask_literal_names("Turn on HA_LITERAL_0", ["Licht"])
