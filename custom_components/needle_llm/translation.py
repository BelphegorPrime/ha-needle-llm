"""Protect literal Home Assistant names when translating routing requests.

This is not a domain-specific translator. We only preserve literal identifiers;
the existing HA model translates the surrounding utterance, and Needle still
has to approve the action independently.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


class TranslationRejected(ValueError):
    """English normalization could not preserve literal identifiers safely."""


_TOKEN_RE = re.compile(r"(?<!\w)HA_LITERAL_\d+(?!\w)", re.IGNORECASE)


def mask_literal_names(
    query: str, names: Iterable[str]
) -> tuple[str, dict[str, str]]:
    """Replace whole, case-insensitive literal name occurrences with markers.

    Longest names take precedence over overlapping shorter labels. Each
    occurrence has its own marker, allowing the restoration check to detect
    dropped, duplicated or invented names without guessing translations.
    """
    if _TOKEN_RE.search(query):
        raise TranslationRejected("Request contains a reserved HA literal marker")

    unique = {
        name.strip().casefold(): name.strip()
        for name in names
        if isinstance(name, str) and name.strip()
    }
    if not unique:
        return query, {}

    ordered = sorted(unique.values(), key=lambda name: (-len(name), name.casefold()))
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(name) for name in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )
    markers: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        marker = f"HA_LITERAL_{len(markers)}"
        markers[marker] = match.group(0)
        return marker

    return pattern.sub(replace, query), markers


def restore_literal_names(
    translated: str, markers: dict[str, str]
) -> str:
    """Restore every protected name exactly once or reject the translation."""
    for marker, original in markers.items():
        pattern = re.compile(
            r"(?<!\w)" + re.escape(marker) + r"(?!\w)",
            re.IGNORECASE,
        )
        if len(pattern.findall(translated)) != 1:
            raise TranslationRejected(
                f"English normalization omitted or duplicated {marker}"
            )
        translated = pattern.sub(lambda _match: original, translated)

    if _TOKEN_RE.search(translated):
        raise TranslationRejected(
            "English normalization introduced an unknown HA literal marker"
        )
    return translated
