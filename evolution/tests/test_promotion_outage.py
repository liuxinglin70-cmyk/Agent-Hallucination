"""Promotion must not adjudicate on an arm that could not be measured.

The first real promotion run exposed the hazard these tests lock: an
infrastructure failure is not symmetric between the arms. An unresolved
control row counts as "not correct" while an unresolved candidate row can
still come back correct, so scoring an outage lets a transport failure
masquerade as a marginal gain. The cycle protocol freezes on this condition;
the promotion gate must refuse to score it and say so.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from evolution.promotion import (
    PromotionRun,
)
from evolution.tests.test_promotion import (  # reuse the offline harness
    _FakeLedger,
    _FakeRegistry,
    _ScriptedBackend,
    _standard_protocol,
    _candidate,
)


class _OutageBackend(_ScriptedBackend):
    """The control arm cannot decide one benefit case (a provider outage)."""

    def __init__(self, case: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.case = case

    def evaluate_fresh(self, cases, **kwargs):
        rows = super().evaluate_fresh(cases, **kwargs)
        entries = list((kwargs.get("registry") or {}).get("interventions") or ())
        if not entries:  # the control arm carries no intervention
            for row in rows:
                if row["case"] == self.case:
                    row["prediction"] = None
                    row["authority"] = "invalid"
                    row["state"] = "unresolved"
                    row["result"] = {
                        "case_id": self.case,
                        "state": "unresolved",
                        "decision_authority": "invalid",
                        "runtime_valid": False,
                        "stage_status": {"judge": {"state": "provider_error"}},
                        "proof": {},
                    }
        return rows


def _run(tmp_path: Path, protocol, backend):
    run = PromotionRun(
        candidate=_candidate(),
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    return run, run.run()


def test_control_outage_is_not_scored_as_a_candidate_gain(tmp_path):
    """An unresolved control row must not become a marginal gain.

    Without the guard this is exactly what happens: the control row is not
    "correct", the candidate row is, and ``marginal_cases`` rises by one on a
    case where nothing but a transport failure occurred.
    """
    protocol = _standard_protocol(
        tmp_path, gold_steps={"A/001.json": 6, "B/001.json": 14},
    )
    backend = _OutageBackend(
        "A/001.json",
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    run, report = _run(tmp_path, protocol, backend)
    assert report["state"] == "promotion_unresolved"
    assert report["admitted"] is False
    assert "could not be completed honestly" in report["terminal_reason"]
    assert run.registry.writes == []
    # The diagnostic delta is recorded, but it did not adjudicate anything.
    assert report["benefit"]["note"].startswith("recorded for diagnosis only")
    assert report["arm_health"]["blocked"]


def test_an_unmeasured_arm_is_not_persisted_for_resume(tmp_path):
    """A failed arm must be retried, not frozen into the protocol forever."""
    protocol = _standard_protocol(tmp_path)
    backend = _OutageBackend(
        "A/001.json",
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    _run(tmp_path, protocol, backend)
    arms = tmp_path / "out" / "arms"
    assert not (arms / "control.json").exists(), (
        "an arm with an infrastructure failure must not be stored as a "
        "completed measurement"
    )
    assert (arms / "candidate.json").exists()


def test_batch_deadline_fail_closes_to_unresolved_not_to_a_gain(tmp_path):
    """Expiry must resolve as unresolved, which the run then refuses to score.

    The overrun is simulated the way ``DetectionBackend.evaluate`` handles one:
    the case runs past the deadline, then fail-closes to an unresolved,
    non-authoritative row instead of reporting a prediction. ``_ScriptedBackend``
    replaces that per-case loop, so it has to reproduce the fail-close itself --
    delegating to the parent would return fully scored rows and quietly assert
    nothing about the deadline.
    """
    protocol = _standard_protocol(tmp_path)
    protocol["timeout_seconds"] = 0.001

    class _Slow(_ScriptedBackend):
        def evaluate_fresh(self, cases, **kwargs):
            time.sleep(0.05)
            rows = super().evaluate_fresh(cases, **kwargs)
            for row in rows:
                row.update({
                    "prediction": None,
                    "state": "unresolved",
                    "authority": "invalid",
                    "measurement": "infrastructure_failure",
                })
            return rows

    run, report = _run(
        tmp_path, protocol,
        _Slow(control_prediction={"A/001.json": False, "B/001.json": False}),
    )
    assert report["state"] == "promotion_unresolved"
    assert run.registry.writes == []


def test_freeze_records_the_batch_budget_and_floor(tmp_path):
    """The frozen protocol must carry its own wall-clock budget and floor."""
    protocol = _standard_protocol(tmp_path)
    assert protocol["timeout_seconds"] == 600
    assert protocol["coverage_floor"]["min_protected_tp"] == 2
    assert protocol["coverage_floor"]["min_protected_tn"] == 2
    assert set(protocol["gold"]) == set(protocol["roles"])
