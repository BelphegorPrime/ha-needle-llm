"""Narrow deterministic no-action preflight for six common HA languages.

This guard catches *explicit* prohibitions and counterfactual questions before
either routing backend can execute a tool. It is a safety backstop, NOT a full
intent parser: it cannot prove an utterance is safe, infer device identity or
override Needle's calibrated confidence/target checks.

Prefer a false rejection to a false execution; never use this to select or
replace the user's requested action. Polite actionable questions without
prohibitions (e.g. "Kannst du bitte ...?") remain eligible for normal routing.
"""

from __future__ import annotations

import re

# Explicit requests not to perform the mentioned action, including
# "do X, not Y": in a one-action router the restricted target must not be
# silently substituted. The expression is intentionally language-bound.
_NEGATIONS = (
    re.compile(
        r"\b(?:nicht|niemals|nie|kein(?:e|en|em|er|es)?|auf keinen fall)\b"
    ),
    re.compile(r"\b(?:don't|do not|never|not)\b"),
    re.compile(r"\b(?:ne\s+\S+\s+pas|n['’]\w+.{0,80}\bpas|pas|jamais|surtout pas)\b"),
    re.compile(r"\b(?:no|nunca|jam[aá]s|ni se te ocurra)\b"),
    re.compile(r"\b(?:non|mai)\b"),
    re.compile(r"\b(?:niet|geen|nooit)\b"),
)

# A conditional hypothetical ("If you could, would you unlock?") is a
# question about possibility, not an instruction to operate a lock.
_HYPOTHETICALS = (
    re.compile(r"^wenn du könntest\b.*\bwürdest du\b"),
    re.compile(r"^if you could\b.*\bwould you\b"),
    re.compile(
        r"^si tu pouvais\b.*\b"
        r"(?:ferais|pourrais|déverrouillerais|verrouillerais|ouvrirais)\b"
    ),
    re.compile(r"^si pudieras\b.*"),
    re.compile(r"^se potessi\b.*"),
    re.compile(r"^als je kon\b.*"),
)


def no_action_reason(query: str) -> str | None:
    """Explain only clearly recognized no-action shapes; otherwise defer.

    No entity, tool name, or action is ever invented or reinterpreted.
    This intentionally errs on the side of rejecting ambiguous requests.
    """
    if not isinstance(query, str) or not query.strip():
        return "The user request is empty or invalid"
    normalized = " ".join(query.casefold().split())
    if any(pattern.search(normalized) for pattern in _HYPOTHETICALS):
        return "Hypothetical question is not an executable instruction"
    if any(pattern.search(normalized) for pattern in _NEGATIONS):
        return "Explicit prohibition or exclusion in the user request"
    return None
