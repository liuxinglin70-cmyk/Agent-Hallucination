"""Single source of truth for gold-label coercion.

Gold labels arrive from different dataset dumps either as real booleans or as
strings ("true"/"1"/"yes" — AgentHallu records carry ``is_hallucination`` as
the string form). Silently dropping the string form loses labels on opt-in
gold paths; this helper centralizes the conversion so every consumer behaves
identically. Unrecognizable values return ``None`` (fail-closed: no label is
ever fabricated).
"""

from __future__ import annotations

from typing import Any

_TRUE_TOKENS = frozenset({"true", "1", "yes"})
_FALSE_TOKENS = frozenset({"false", "0", "no"})


def coerce_gold_label(value: Any) -> bool | None:
    """Coerce a raw gold label to ``bool``; ``None`` when unrecognizable.

    Booleans pass through untouched. Strings are stripped/lower-cased and
    matched against the closed token sets (true/1/yes, false/0/no). Exact
    numeric 1/0 are accepted (some dumps carry integer labels). Every other
    value — including ``None`` — yields ``None``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_TOKENS:
            return True
        if normalized in _FALSE_TOKENS:
            return False
        return None
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return None
