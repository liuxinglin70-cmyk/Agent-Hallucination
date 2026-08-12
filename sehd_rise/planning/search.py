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

from sehd_rise.skills.action import CLEAN_TO_ERROR_ACTIONS, ERROR_TO_CLEAN_ACTIONS

# Error-type string vocabulary (the classifier's closed ``error_type``
# values, kept as strings so this layer needs no typing import).
_ERRORWARD_ERROR_TYPES = frozenset({
    "false_negative",
    "false_negative_clean",
    "false_negative_unresolved",
    "unresolved_should_error",
})
_CLEANWARD_ERROR_TYPES = frozenset({
    "false_positive",
    "unresolved_should_clean",
})

_FN_POLARITY_MODULES = frozenset(CLEAN_TO_ERROR_ACTIONS)
_FP_POLARITY_MODULES = frozenset(ERROR_TO_CLEAN_ACTIONS)

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


def _abstention_ladder_rung_complete(
    *,
    target_module: str,
    before_state: str,
    after_state: str,
    gold_label: bool | None,
    ladder_used: bool,
    assessment_passed: bool,
) -> bool:
    """Whether rung 1 of the abstention ladder completed on this edit.

    An FN source (gold hallucination) whose verdict was a confident but
    thin-evidence ``verified_clean`` becomes recoverable through two
    sequential, individually gated edits instead of one impossible edit.
    Rung 1 completes when an accepted ``abstention_rule`` edit demotes the
    clean verdict to ``unresolved``; the re-queued source then fires
    ``unresolved_should_error`` and routes ``evidence_obligation`` (rung 2).
    Every rung still passes the same structural gate, source replay and
    canary checks; no rung may be merged or credited together.  The ladder
    fires at most once per source (``ladder_used``).
    """
    return (
        target_module == "abstention_rule"
        and before_state == "verified_clean"
        and after_state == "unresolved"
        and gold_label is True
        and assessment_passed
        and not ladder_used
    )


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
) -> Mapping[str, Any] | None:
    """Pick the nearest stable same-type neighbour for a contrast replay.

    ``entries`` are same-type registry rows in processing order; nearest is
    the most recently registered row with a resolved verdict that is not the
    triggering case itself.  Deterministic and replay-cheap: neighbours are
    drawn only from cases the current run already detected.
    """
    for entry in reversed(list(entries or [])):
        if str(entry.get("case_id") or "") == case_id:
            continue
        if str(entry.get("state") or "") in _CONTRAST_RESOLVED_STATES:
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


def _signal_polarity_pool(signal: Any) -> frozenset[str] | None:
    """Return the editable module side required by the failure direction."""
    error_type = str(getattr(signal, "error_type", None) or "")
    if error_type in _ERRORWARD_ERROR_TYPES:
        return _FN_POLARITY_MODULES
    if error_type in _CLEANWARD_ERROR_TYPES:
        return _FP_POLARITY_MODULES
    if error_type:
        return None

    gold_label = getattr(signal, "gold_label", None)
    state = str(getattr(signal, "state", "") or "")
    if gold_label is True and state in {"verified_clean", "unresolved"}:
        return _FN_POLARITY_MODULES
    if gold_label is False and state in {"verified_error", "unresolved"}:
        return _FP_POLARITY_MODULES
    return None


__all__ = [
    "_abstention_ladder_rung_complete",
    "_contrast_inversion",
    "_select_contrast_neighbor",
    "_signal_polarity_pool",
    "_task_state_score",
    "_trim_contrast_registry",
]
