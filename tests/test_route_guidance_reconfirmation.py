"""D3 causal control for route-guidance edits.

A route-guidance edit has no deterministic backend, so its acceptance evidence
is a provider re-run. A single guided terminal flip is indistinguishable from
sampling drift. The causal control re-runs the frozen route replay WITHOUT the
guidance and requires the guided replay to reach the improved terminal while
the control stays at baseline; a control that flips on its own means the flip
is sampling drift and the improvement is downgraded to no_effect.
"""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.evaluation.fitness import FitnessAssessment
from sehd_rise.loop import runner
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


def _edit() -> SimpleNamespace:
    return SimpleNamespace(target_module="evidence_obligation")


def _loop(control_state: str | None, *, monkeypatch) -> SimpleNamespace:
    def _control(baseline, *, owner, guidance_blocks, record, config,
                 provider_usage_ledger):
        assert owner == "evidence_obligation"
        assert guidance_blocks == ()
        if control_state is None:
            raise RuntimeError("boom")
        return {"state": control_state}

    monkeypatch.setattr(runner, "replay_route_guidance_focused", _control)
    return SimpleNamespace(
        records={"case_1": {}},
        config={},
        provider_usage_ledger=None,
    )


def test_control_stays_at_baseline_keeps_improvement(monkeypatch) -> None:
    loop = _loop("verified_clean", monkeypatch=monkeypatch)
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
        edit=_edit(),
    )
    assert assessment.decision == "local_improvement"
    assert audit["status"] == "decisive"
    assert audit["control_terminal"] == "verified_clean"


def test_control_flip_downgrades_to_no_effect(monkeypatch) -> None:
    loop = _loop("verified_error", monkeypatch=monkeypatch)
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
        edit=_edit(),
    )
    assert assessment.decision == "no_effect"
    assert assessment.reasons == ("route_guidance_not_decisive",)
    assert audit["status"] == "not_decisive"


def test_runtime_error_downgrades_to_no_effect(monkeypatch) -> None:
    loop = _loop(None, monkeypatch=monkeypatch)
    assessment, audit = _decisive_route_guidance_reconfirmation(
        loop,
        case_id="case_1",
        detection_result={"state": "verified_clean"},
        expected_terminal="verified_error",
        assessment=_assessment(),
        edit=_edit(),
    )
    assert assessment.decision == "no_effect"
    assert assessment.reasons == ("route_guidance_control_runtime_error",)
    assert audit["status"] == "runtime_error"
