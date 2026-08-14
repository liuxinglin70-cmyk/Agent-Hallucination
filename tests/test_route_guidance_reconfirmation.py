"""D3 decisive re-confirmation for route-guidance edits.

A route-guidance edit has no deterministic backend, so its acceptance
evidence is a single provider re-run — indistinguishable from sampling
drift. The re-confirmation re-runs the same candidate genome and requires
the terminal to reach the same improved state before the improvement counts
as decisive; a disagreement downgrades the assessment to no_effect.
"""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.evaluation.fitness import FitnessAssessment
from sehd_rise.loop.runner import _decisive_route_guidance_reconfirmation


def _assessment() -> FitnessAssessment:
    return FitnessAssessment(
        "local_improvement",
        ("gold_assisted_task_score_improved",),
        0,
        2,
        0.0,
        2.0,
    )


def _loop(terminals: list[str]) -> SimpleNamespace:
    calls: list[dict] = []

    def run_detection(*, budget_kind, genome, case_id, policy_genome,
                      base_result, **kwargs):
        calls.append({
            "budget_kind": budget_kind,
            "base_result": base_result,
        })
        return {"state": terminals[len(calls) - 1]}

    policy_genome = SimpleNamespace(items_for_scope=lambda _scope: ())

    return SimpleNamespace(
        run_detection=run_detection,
        calls=calls,
        state=SimpleNamespace(policy_genome=policy_genome),
    )


def test_decisive_reconfirmation_keeps_improvement() -> None:
    loop = _loop(["verified_error"])
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        candidate_genome=SimpleNamespace(),
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
    )
    assert assessment.decision == "local_improvement"
    assert audit["status"] == "decisive"
    assert loop.calls[0]["budget_kind"] == "source"
    assert loop.calls[0]["base_result"] == {"state": "verified_clean"}


def test_drifted_reconfirmation_downgrades_to_no_effect() -> None:
    loop = _loop(["verified_clean"])  # the re-run falls back to the base
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        candidate_genome=SimpleNamespace(),
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
    )
    assert assessment.decision == "no_effect"
    assert assessment.reasons == ("route_guidance_not_decisive",)
    assert audit["status"] == "not_decisive"


def test_runtime_error_downgrades_to_no_effect() -> None:
    loop = _loop([])
    loop.run_detection = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        candidate_genome=SimpleNamespace(),
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
    )
    assert assessment.decision == "no_effect"
    assert assessment.reasons == ("route_guidance_reconfirmation_runtime_error",)
    assert audit["status"] == "runtime_error"
