"""Search heuristics for the planners: polarity hypotheses and contrast
ladder bookkeeping.

Pure functions moved off the old evolution god-object. They decide *which
owners may be proposed* (gold-polarity pool), *which prior attempt a retry
must not repeat* (abstention ladder completion), and *which neighbour
replay counts as a contrast inversion* — all deterministic, closed-
vocabulary, and case-agnostic. No candidate text or raw provider output is
consumed here.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# The polarity pool is owned by the causal layer (single trigger-context
# source); this module re-exports it under its historical name for callers.
from sehd_rise.causal.cut import signal_polarity_pool as _signal_polarity_pool

_CONTRAST_RESOLVED_STATES = frozenset({"verified_error", "verified_clean"})

# Per-type cap for the contrast registry: rows are persisted in every
# checkpoint and searched linearly per source, so an unbounded registry
# would grow without limit on long runs (mirrors the stable-canary cap).
_CONTRAST_REGISTRY_MAX_ROWS = 40


def _task_state_score(state: str, gold_label: bool) -> int:
    """Rank task outcomes without exposing the label to mutation prompts.

    2 = correct definite decision, 1 = unresolved, 0 = wrong definite
    decision. Inlined from the old fitness helper: the planning layer only
    needs the rank, the fitness module owns the evaluation-side scoring.
    """
    if state == "unresolved":
        return 1
    if state == "verified_error":
        return 2 if gold_label else 0
    if state == "verified_clean":
        return 0 if gold_label else 2
    raise ValueError(f"invalid detector state: {state!r}")


def _trim_contrast_registry(registry: dict[str, list[dict[str, Any]]]) -> None:
    """Keep the most recent rows per type bucket, dropping the oldest."""
    for key in list(registry):
        bucket = registry[key]
        if len(bucket) > _CONTRAST_REGISTRY_MAX_ROWS:
            registry[key] = bucket[-_CONTRAST_REGISTRY_MAX_ROWS:]


def _select_contrast_neighbor(
    entries: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
    require_independent_owner: bool = False,
    current_owner: str = "",
) -> Mapping[str, Any] | None:
    """Pick the nearest stable same-type neighbour for a contrast replay.

    ``entries`` are same-type registry rows in processing order; nearest is
    the most recently registered row with a resolved verdict that is not the
    triggering case itself.  Deterministic and replay-cheap: neighbours are
    drawn only from cases the current run already detected.

    ``require_independent_owner`` is the deterministic consumer of the meta
    ``search_prior.require_independent_anchor`` token: a contrast replay is
    only an independent overfitting guard when the neighbour's failure
    anchored on a different owner, so same-owner rows are skipped.  No
    qualifying neighbour yields ``None`` (the replay is skipped, never
    relaxed).
    """
    for entry in reversed(list(entries or [])):
        if str(entry.get("case_id") or "") == case_id:
            continue
        if str(entry.get("state") or "") not in _CONTRAST_RESOLVED_STATES:
            continue
        if require_independent_owner:
            owner = str(entry.get("owner") or "")
            if not owner or owner == str(current_owner or ""):
                continue
        return entry
    return None


def _contrast_inversion(
    *,
    before_state: str,
    after_state: str,
    gold_label: bool | None,
) -> bool:
    """Whether a neighbour replay flip is a contrast inversion.

    A polarity flip (verified_error <-> verified_clean) is always an
    inversion.  With a gold label any task-score regression (including a
    resolved verdict demoting towards the wrong side) is an inversion.
    Without gold, transitions through ``unresolved`` are telemetry only:
    the self-supervised run has no correctness authority to call them.
    """
    if before_state == after_state:
        return False
    if (
        before_state in _CONTRAST_RESOLVED_STATES
        and after_state in _CONTRAST_RESOLVED_STATES
    ):
        return True
    if gold_label is not None:
        try:
            return _task_state_score(
                after_state, gold_label,
            ) < _task_state_score(before_state, gold_label)
        except ValueError:
            return False
    return False


__all__ = [
    "_contrast_inversion",
    "_select_contrast_neighbor",
    "_signal_polarity_pool",
    "_task_state_score",
    "_trim_contrast_registry",
]
