"""The cycle must score a localization claim by its declared objective.

Companion to ``test_localization_expressible``. Fixing the producer (teaching
``target_kind``) is necessary but not sufficient: the cycle's M gate computed a
*detection* delta for every candidate, so a tracer directive — which by design
leaves the binary verdict alone — would have been scored as "no marginal gain"
even when it corrected the named step. That is the wrong question asked of the
right candidate, and it would have made the localization objective look
impossible for the same reason it looked impossible before.

These tests pin the dispatch: ``target_kind`` selects the gate pair, and the
detection arm still uses the detection pair.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.gates import candidate_target_kind  # noqa: E402
from evolution.metrics import paired_localization_delta  # noqa: E402


def _row(case, *, gold, prediction, step, authority="authoritative"):
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "responsible_step": step,
        "state": "verified_error" if prediction else "verified_clean",
        "result": {"runtime_valid": True, "stage_status": {}},
    }


GOLD_STEPS = {"A.json": 3, "B.json": 5}


def test_candidate_target_kind_defaults_to_detection_when_absent():
    assert candidate_target_kind({"claim": {}}) == "detection"
    assert candidate_target_kind({"claim": {"target_kind": "localization"}}) == "localization"
    assert candidate_target_kind({"claim": {"target_kind": "joint"}}) == "joint"


def test_a_corrected_step_is_a_localization_gain_with_no_detection_change():
    """The exact shape a tracer directive produces, and the old gate missed."""
    baseline = [
        # convicted correctly, wrong step -> the thing to repair
        _row("A.json", gold=True, prediction=True, step=1),
        # convicted correctly, right step -> must be preserved
        _row("B.json", gold=True, prediction=True, step=5),
    ]
    candidate = [
        _row("A.json", gold=True, prediction=True, step=3),
        _row("B.json", gold=True, prediction=True, step=5),
    ]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["joint_improved"] == 1
    assert delta["joint_broken"] == 0
    # And the detection reading is genuinely flat, which is why scoring the
    # localization claim with the detection delta measured nothing.
    assert delta["localizable_n"] == 2


def test_the_localization_delta_can_go_the_wrong_way():
    baseline = [_row("A.json", gold=True, prediction=True, step=3)]
    candidate = [_row("A.json", gold=True, prediction=True, step=1)]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["joint_improved"] == 0
    assert delta["joint_broken"] == 1


def test_localization_requires_conviction_so_a_miss_cannot_buy_a_gain():
    # A candidate that stops convicting cannot 'gain' localization credit:
    # the denominator is the frozen batch and a hit needs prediction True.
    baseline = [_row("A.json", gold=True, prediction=True, step=1)]
    candidate = [_row("A.json", gold=True, prediction=False, step=3)]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["joint_improved"] == 0
    assert delta["localizable_n"] == 1


def test_unannotated_cases_stay_out_of_the_localization_batch():
    baseline = [_row("C.json", gold=True, prediction=True, step=1)]
    candidate = [_row("C.json", gold=True, prediction=True, step=9)]
    delta = paired_localization_delta(
        baseline, candidate, gold_steps={"A.json": 3},
    )
    assert delta["localizable_n"] == 0


def test_gold_negative_cases_are_not_localization_targets():
    baseline = [_row("A.json", gold=False, prediction=True, step=1)]
    candidate = [_row("A.json", gold=False, prediction=False, step=None)]
    delta = paired_localization_delta(baseline, candidate, gold_steps=GOLD_STEPS)
    assert delta["localizable_n"] == 0
