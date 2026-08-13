"""Gold-locked polarity routing: false positives and false negatives.

A false positive (error verdict on a clean case) and a false-negative-clean
(clean verdict on an error case) are the two gold-locked verdict flips the
causal cut must route onto the L1 evolvable surface — otherwise the
self-evolution loop silently skips the exact cases it most needs to repair.
These tests lock the end-to-end routing: signal -> cut -> episode ->
repairability, plus the ``hallucination_step`` gold-step fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.causal.cut import (
    REASON_GOLD_FALSE_POSITIVE,
    build_causal_cut,
)
from sehd_rise.causal.episode import build_causal_episode
from sehd_rise.causal.repairability import (
    RepairabilityStatus,
    analyze as analyze_repairability,
)
from sehd_rise.causal.signals import project_detection
from sehd_rise.loop.runner import _infer_evolution_direction
from sehd_rise.planning.candidates import _allowed_l1_modules
from sehd_rise.platform.gold import _evaluator_gold_steps
from sehd_rise.skills.vocabulary.contracts import LAYER_L1, LAYER_NONE


def _result(state: str, *, surviving: list[str] | None = None) -> dict:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "proof_adjudication": {
            "state": state,
            "decision_authority": "authoritative",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": list(surviving or []),
            "responsible_event_id": "event_001",
        },
    }


def _verdict(cut, signal):
    if not cut.actionable:
        return None
    direction = _infer_evolution_direction(signal.state, signal.gold_label)
    modules = _allowed_l1_modules(
        signal, cut.owner, cut.evidence.get("artifact_frontier", ())
    )
    episode = build_causal_episode(
        signal=signal, cut=cut, record_key="c",
        evolution_direction=direction, allowed_modules=modules,
    )
    return analyze_repairability(
        episode, allowed_modules=modules, evolution_direction=direction,
    )


def test_gold_steps_falls_back_to_hallucination_step() -> None:
    """AgentHallu dumps carry only ``hallucination_step``; the join must
    surface it as the localization-correctness step."""
    assert _evaluator_gold_steps({"hallucination_step": 5}) == 5
    assert _evaluator_gold_steps({"gold_steps": [1, 2]}) == [1, 2]
    assert _evaluator_gold_steps({"gold_steps": [], "hallucination_step": 5}) == []
    assert _evaluator_gold_steps({}) is None


def test_false_positive_routes_to_prosecution_evidence() -> None:
    """gold=false + verified_error -> evidence_obligation, REPAIRABLE."""
    signal = project_detection(
        _result("verified_error", surviving=["e1"]),
        use_gold=True,
    )
    signal = project_detection(
        {**_result("verified_error", surviving=["e1"]), "gold_label": False},
        use_gold=True,
    )
    cut = build_causal_cut(signal)
    assert cut.skill_layer == LAYER_L1
    assert cut.owner == "evidence_obligation"
    assert cut.reason_code == REASON_GOLD_FALSE_POSITIVE
    verdict = _verdict(cut, signal)
    assert verdict is not None
    assert verdict.status is RepairabilityStatus.REPAIRABLE


def test_false_negative_clean_is_non_evolvable() -> None:
    """gold=true + verified_clean is NON_EVOLVABLE until Phase 3 designs a
    deterministic clean->error action for evidence_obligation.

    ``require_evidence_field`` / ``enforce_certificate_invariant`` were removed
    (their postconditions only read court audits, never executed a transform),
    so ``evidence_obligation`` no longer carries a clean->error opcode and the
    L1 executable-completeness gate demotes the FN-clean cut to ``LAYER_NONE``.
    """
    signal = project_detection(
        {**_result("verified_clean"), "gold_label": True},
        use_gold=True,
    )
    cut = build_causal_cut(signal)
    assert cut.skill_layer == LAYER_NONE
    assert cut.owner == ""
    assert cut.reason_code == "demoted_l1_no_executable_obligation"


def test_gold_polarity_partner_module_stays_legal() -> None:
    """The cut's secondary owner (the surgical polarity partner) must remain
    a legal L1 target alongside the primary owner.

    A gold-locked FP fix has two legal surfaces — ``evidence_obligation``
    (``keep_coverage_incomplete``) and ``atomic_verifier_instruction``
    (``withhold_certificate``, the surgical single-carrier fix the reflection
    prompt's VERDICT-CARRIER RULE depends on). Before the secondary-owner
    wiring was threaded through ``_allowed_l1_modules`` the partner module was
    dropped, forcing every FP repair through the blunt whole-surface route.
    """
    signal = project_detection(
        {**_result("verified_error", surviving=["e1"]), "gold_label": False},
        use_gold=True,
    )
    cut = build_causal_cut(signal)
    assert cut.secondary_owner == "atomic_verifier_instruction"
    modules = _allowed_l1_modules(signal, cut.owner, (), cut.secondary_owner)
    assert cut.owner in modules
    assert "atomic_verifier_instruction" in modules


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
