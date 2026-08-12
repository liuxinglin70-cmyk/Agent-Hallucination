"""Replay-anchor canaries: regression guards on stable (non-evolvable) detections.

A canary is a detection that was stable (non-triggered) under the registering
genome. After a candidate edit runs, the same case is replayed: the canary is
preserved only when the verdict, runtime validity, and trigger state all match
the registering context. A regression — a previously stable case becoming
evolvable, or a verdict flip — rejects the edit.

C10 reconnection (plan §2.12): the old ``attribute_failure(payload).should_evolve``
read the trigger straight off the attribution signal. Phase 4 absorbed that
trigger into the Repairability Authority, so the trigger is now derived from
the full causal chain: ``project_detection`` -> ``build_causal_cut`` ->
``build_causal_episode`` -> ``repairability.analyze``. A detection is triggered
iff the episode is ``REPAIRABLE``. The registering context (``use_gold`` /
``gold_label``) is threaded through both parent and child so a gold-correct
case is judged identically on both sides — the split that previously
misclassified every gold-correct preserved canary as a regression.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from sehd_rise.causal.cut import build_causal_cut
from sehd_rise.causal.episode import build_causal_episode
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
)
from sehd_rise.causal.signals import resolve_evolution_state, project_detection
from sehd_rise.platform.gold import _evaluator_gold
from sehd_rise.skills.genome import SkillEdit, SkillOp


def _triggered(result: Mapping[str, Any], *, use_gold: bool, gold_label: bool | None) -> bool:
    """Whether a detection result is an evolvable (triggered) episode.

    C10: the trigger is the Repairability Authority's ``REPAIRABLE`` verdict,
    not a boolean on the signal. Building the episode is the honest cost of
    asking "should this case enter evolution?" once ``should_evolve`` is gone.
    """
    payload = dict(result)
    if use_gold and gold_label is not None:
        payload["gold_label"] = gold_label
    signal = project_detection(payload, use_gold=use_gold)
    if signal.diagnostic_only:
        return False
    cut = build_causal_cut(signal)
    episode = build_causal_episode(
        signal=signal,
        cut=cut,
        record_key=str(payload.get("case_id") or ""),
    )
    verdict = analyze_repairability(episode)
    return verdict.status is RepairabilityStatus.REPAIRABLE


def canary_preserved(
    parent_result: Mapping[str, Any],
    child_result: Mapping[str, Any],
    *,
    use_gold: bool = False,
    gold_label: bool | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Require both verdict and process stability on a replay anchor.

    ``use_gold`` / ``gold_label`` carry the SAME attribution context that
    registered the anchor. Checking the child with a hard-coded gold-free
    context splits the two paths: a gold-correct case (e.g. verified_error with
    gold=true) is stable under the registering gold context (non-triggered)
    but may report triggered under a gold-free signal, so every edit that
    faithfully preserves it would be misclassified as a canary regression. The
    replay anchor therefore is re-checked under the same context that admitted
    it; callers that registered under gold pass the case gold through so parent
    and child are judged identically.
    """
    parent_payload = dict(parent_result)
    child_payload = dict(child_result)
    if use_gold and gold_label is not None:
        parent_payload["gold_label"] = gold_label
        child_payload["gold_label"] = gold_label
    try:
        parent_state = resolve_evolution_state(parent_payload)
        child_state = resolve_evolution_state(child_payload)
    except ValueError as exc:
        # Distinct sentinels, never None: an audit consumer recomputing
        # stability as ``before_state == after_state`` must not read two
        # unresolved states as a preserved anchor.
        return False, {
            "before_state": "unresolved_state_error",
            "after_state": "unresolved_state_error_after",
            "preserved": False,
            "error": f"canary_state:{exc}",
        }
    try:
        parent_triggered = _triggered(
            parent_payload, use_gold=use_gold, gold_label=gold_label,
        )
        child_triggered = _triggered(
            child_payload, use_gold=use_gold, gold_label=gold_label,
        )
    except Exception as exc:
        return False, {
            "before_state": parent_state,
            "after_state": child_state,
            "preserved": False,
            "error": f"canary_attribution:{type(exc).__name__}:{exc}",
        }
    preserved = (
        parent_state == child_state
        and bool(child_payload.get("runtime_valid"))
        and not parent_triggered
        and not child_triggered
    )
    return preserved, {
        "before_state": parent_state,
        "after_state": child_state,
        "before_triggered": parent_triggered,
        "after_triggered": child_triggered,
        "runtime_valid": bool(child_payload.get("runtime_valid")),
        "preserved": preserved,
        "canary_context": "gold" if use_gold else "label_free",
    }


def fitness_gold_label(
    case_id: str, *,
    records: Mapping[str, dict[str, Any]], fitness_use_gold: bool,
) -> bool | None:
    if not fitness_use_gold:
        return None
    return _evaluator_gold(records[case_id])


def selected_canaries(
    edit: SkillEdit, *,
    stable_canaries: dict[str, dict[str, Any]], canary_limit: int,
    genome_content_hash: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Deterministically sample a small risk-adaptive canary set."""
    if not stable_canaries or canary_limit == 0:
        return []
    requested = canary_limit
    if edit.operation == SkillOp.ADD_RULE:
        requested = min(requested, 1)
    ranked = sorted(
        stable_canaries.items(),
        key=lambda item: hashlib.sha256(
            f"{genome_content_hash}:{edit.edit_id}:{item[0]}".encode("utf-8")
        ).hexdigest(),
    )
    return ranked[:requested]


def evict_stable_canaries(
    stable_canaries: dict[str, dict[str, Any]], canary_limit: int,
) -> None:
    """Bound the stable-canary pool with FIFO eviction.

    The bound is deliberate, not a retention strategy: ``selected_canaries``
    re-ranks the surviving anchors by (genome, edit) hash at selection time, so
    the eviction order cannot bias which anchors a regression check sees.
    """
    while len(stable_canaries) > max(8, canary_limit * 4):
        stable_canaries.pop(next(iter(stable_canaries)))


__all__ = [
    "canary_preserved",
    "evict_stable_canaries",
    "fitness_gold_label",
    "selected_canaries",
]
