"""A bounded, replayable tie-breaker for events that share a signal priority.

The measured problem this addresses: ``select_causal_candidates`` ranks by
``(priority, -verified_count, position)``. ``verified_count`` is produced only by
``arithmetic_check``, which requires a calculator interaction -- there are zero
in the corpus -- so the key degenerates to ``(priority, position)`` and every
event in a band is separated by nothing but its position. On the 693-case corpus
63.4% of positive cases have more than one event at the top priority, so the
top-3 window is decided inside a tie the detector cannot see into.

The scorer here does not decide anything. It orders candidates *inside one
priority band*; it cannot promote an unsupported claim above a tool failure, and
it cannot remove an event from consideration. That restriction is deliberate:
the priority table encodes a semantic ordering that this module has no evidence
to overturn, while the band's internal order is where the measured loss is.

With no artifact the score is zero for every row and the sort key reduces to the
previous one exactly, so an unconfigured deployment keeps its recorded ordering
and its recorded results.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

SCORER_ARTIFACT_SCHEMA = "detector.candidate_scorer.v1"

#: Features read from a risk-profile row. Each is a bounded integer, so a
#: learned weight can never amplify one case past another's whole band.
FEATURES = frozenset({
    "signal_count",
    "verified_signals",
    "distinct_signal_kinds",
    "observation_signals",
    "agent_signals",
    "earlier_position",
    "explicit_failure",
})

#: Exact arithmetic: weights arrive as strings and are read as ``Decimal``, so
#: two runs of the same artifact over the same rows cannot disagree on a tie.


class ScorerArtifactError(ValueError):
    """An artifact that cannot be applied must refuse to load, not score zero."""


def load_scorer(artifact: str | Path | None) -> dict[str, Any] | None:
    """Read and validate a scorer artifact; ``None`` means the built-in order."""
    if artifact is None:
        return None
    path = Path(artifact)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScorerArtifactError(f"scorer artifact is unreadable: {exc}") from None
    if not isinstance(raw, Mapping):
        raise ScorerArtifactError("scorer artifact must be an object")
    if raw.get("schema_version") != SCORER_ARTIFACT_SCHEMA:
        raise ScorerArtifactError(
            f"scorer artifact schema must be {SCORER_ARTIFACT_SCHEMA}")
    weights = raw.get("weights")
    if not isinstance(weights, Mapping) or not weights:
        raise ScorerArtifactError("scorer artifact must declare feature weights")
    from decimal import Decimal, InvalidOperation

    resolved: dict[str, Any] = {}
    for name, value in weights.items():
        if name not in FEATURES:
            # An unknown feature is a defect, not a no-op: silently ignoring it
            # would report the artifact as active while scoring something else.
            raise ScorerArtifactError(f"unknown scorer feature: {name}")
        try:
            weight = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ScorerArtifactError(f"scorer weight for {name} is not a number") from None
        if not weight.is_finite():
            raise ScorerArtifactError(f"scorer weight for {name} is not finite")
        resolved[name] = weight
    bias = raw.get("bias", "0")
    try:
        resolved["__bias__"] = Decimal(str(bias))
    except (InvalidOperation, ValueError):
        raise ScorerArtifactError("scorer bias is not a number") from None
    if not resolved["__bias__"].is_finite():
        raise ScorerArtifactError("scorer bias is not finite")
    return {"weights": resolved, "id": str(raw.get("id") or "")}


def row_features(
    row: Mapping[str, Any], *, position_count: int,
) -> dict[str, int]:
    """Read the bounded features of one risk-profile row.

    Everything here is already recorded on the row, so scoring a candidate adds
    no provider call and cannot change what the stages are allowed to see.
    """
    signals: Sequence[Mapping[str, Any]] = list(row.get("risk_signals") or ())
    verified = sum(1 for signal in signals
                   if str(signal.get("verification_status")) == "verified")
    kinds = {str(signal.get("signal")) for signal in signals}
    observation = sum(1 for signal in signals
                      if str(signal.get("signal")) in
                      {"unverified_observation", "observed_tool_failure",
                       "external_guidance", "bound_field_conflict"})
    agent = sum(1 for signal in signals
                if str(signal.get("signal")) in {"unverified_action", "unsupported_claim"})
    position = int(row.get("position") or 0)
    # Earlier events tie-break forward, but as a bounded share of the trajectory
    # so a 400-event case cannot outrank a 5-event one on length alone.
    span = max(1, int(position_count))
    earlier = max(0, span - position)
    return {
        "signal_count": len(signals),
        "verified_signals": verified,
        "distinct_signal_kinds": len(kinds),
        "observation_signals": observation,
        "agent_signals": agent,
        "earlier_position": earlier,
        "explicit_failure": int(
            any(str(signal.get("signal")) == "observed_tool_failure" for signal in signals)),
    }


def score_row(features: Mapping[str, int], scorer: Mapping[str, Any] | None) -> Any:
    """Exact weighted sum, in ``Decimal``, so no pair of rows can collapse.

    The result stays exact rather than being converted to ``int`` or ``float``.
    Both alternatives lose resolution: scaling to a fixed number of decimals
    rounds a weight smaller than the quantum down to zero -- silently restoring
    the positional order for exactly the rows the artifact exists to separate --
    and a float can make two genuinely different sums compare equal. ``Decimal``
    is totally ordered and compares exactly against the integer ``0`` returned
    when there is no scorer, so the sort key stays a single well-typed tuple.
    """
    if scorer is None:
        return 0
    total = scorer["weights"]["__bias__"]
    for name, weight in scorer["weights"].items():
        if name == "__bias__":
            continue
        total += weight * int(features.get(name) or 0)
    return total
