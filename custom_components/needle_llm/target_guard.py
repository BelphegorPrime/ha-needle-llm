"""Conservative target reconciliation for Needle-generated entity filters.

A wrong optional device_class can silently exclude a correctly named device.
Only fix it when the user explicitly named exactly one currently exposed entity.
Never guess a target and never relax an explicit device-class constraint.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any


class TargetGuardRejected(Exception):
    """The generated target filters cannot be safely reconciled."""


def _normal(value: str) -> str:
    """Normalize names for exact case-insensitive comparison."""
    return " ".join(value.casefold().split())


def _mentions_exact_name(query: str, name: str) -> bool:
    """Check the whole device name, not a substring of another target."""
    target = _normal(name)
    return bool(target) and re.search(
        rf"(?<!\w){re.escape(target)}(?!\w)", _normal(query)
    ) is not None


def reconcile_named_target(
    query: str,
    arguments: dict[str, Any],
    schema: dict[str, Any],
    exposed_entities: Collection[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconcile a bogus class against a uniquely named exposed entity.

    This never changes the target name/area or tool, and only operates when
    an explicit entity name appears verbatim in the user's original request.
    """
    modified = dict(arguments)
    details: dict[str, Any] = {"status": "unchanged"}

    name = arguments.get("name")
    if not isinstance(name, str) or not _mentions_exact_name(query, name):
        return modified, details

    matches = [
        entity
        for entity in exposed_entities
        if _normal(entity["name"]) == _normal(name)
    ]
    if len(matches) != 1:
        details["status"] = "not_unique_or_not_exposed"
        details["match_count"] = len(matches)
        return modified, details

    entity = matches[0]
    domain = entity["entity_id"].split(".", 1)[0]
    actual_class = entity.get("device_class")
    classes = arguments.get("device_class")
    if classes is None:
        return modified, details

    classes_list = classes if isinstance(classes, list) else [classes]
    if not all(isinstance(value, str) for value in classes_list):
        raise TargetGuardRejected("Invalid device_class filter")

    if actual_class is not None and all(
        _normal(value) == _normal(actual_class) for value in classes_list
    ):
        return modified, details

    # A class explicitly requested by the user cannot be silently discarded.
    if any(
        re.search(rf"(?<!\w){re.escape(_normal(value))}(?!\w)", _normal(query))
        for value in classes_list
    ):
        raise TargetGuardRejected(
            "Needle's device_class contradicts the uniquely named "
            "Home Assistant entity and was explicitly requested"
        )

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    existing_domain = modified.get("domain")
    if existing_domain is not None:
        values = (
            existing_domain if isinstance(existing_domain, list)
            else [existing_domain]
        )
        if values != [domain]:
            raise TargetGuardRejected(
                "Needle provided a conflicting domain filter"
            )

    modified.pop("device_class")
    # Narrow, never widen: constrain execution to the exact entity's domain.
    if "domain" in properties:
        modified["domain"] = [domain]

    details = {
        "status": "repaired_unrequested_device_class",
        "entity_id": entity["entity_id"],
        "original_device_class": classes_list,
        "actual_device_class": actual_class,
        "domain_constraint": domain if "domain" in properties else None,
    }
    return modified, details



def find_unique_mentioned_entity(
    query: str,
    exposed_entities: Collection[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find exactly one exposed entity whose full name is in the utterance.

    This is a conservative, language-independent literal match, not fuzzy
    matching, translation or a guess about what the user intended.
    """
    matches = [
        entity
        for entity in exposed_entities
        if isinstance(entity.get("name"), str)
        and _mentions_exact_name(query, entity["name"])
    ]
    return matches[0] if len(matches) == 1 else None
