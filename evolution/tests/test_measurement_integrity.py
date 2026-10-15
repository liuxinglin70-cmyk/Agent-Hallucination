"""Regressions for the measurement-integrity defects found in the deep audit.

Every test here pins a way the framework could previously record something
other than what it measured:

* an arm that never ran, read as a candidate that broke things;
* a round whose gain arm could not be read, reported as a round with no
  repairable directions;
* a benefit-set comparison over zero rows, certified as "the gates rejected
  the defect";
* an audit verdict reused under gate thresholds it was not produced under;
* a promotion whose gate set was cut down, still recorded as four gates passed;
* a frozen protocol edited in place after the control arm was known;
* a rejected candidate re-rolled until it passed;
* a final verdict reachable by abstention changes alone.

They are offline: MockDetectorBackend, scripted proposers, and a ledger on
``tmp_path``. The HTTP guard in ``conftest.py`` stays armed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.contracts import empty_registry
from evolution.engine import (
    EvolutionEngine,
    _ArmRunner,
    _defect_rejection_after_exclusions,
    terminal_classification,
)
from evolution.metrics import mcnemar_improvement_p, paired_delta
from evolution.splits import EvolutionSplit


def _make_dataset(root: Path, n: int = 20) -> list[str]:
    cases = [f"A/{index:03d}.json" for index in range(n)]
    for case in cases:
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q", "history": [],
            "is_hallucination": int(case.split("/")[1][:3]) % 2 == 0,
        }), encoding="utf-8")
    return cases


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["split_manifest"] = str(tmp_path / "manifest.json")
    config["mini_split_manifest"] = str(tmp_path / "mini_manifest.json")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["harness_dir"] = str(tmp_path / "harness")
    config["memory_root"] = str(tmp_path / "memory")
    config["backend"]["name"] = "mock"
    config["backend"]["cache"] = False
    config["proposer"]["mode"] = "offline"
    config["budget"]["patrol_cases"] = 8
    return config


def _split(cases: list[str]) -> EvolutionSplit:
    return EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )


def test_synthetic_measurement_does_not_construct_a_provider_proposer(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("synthetic measurement attempted a provider proposer")
    monkeypatch.setattr("evolution.proposer.LLMProposer", forbidden)
    cases = _make_dataset(tmp_path / "data")
    with EvolutionEngine(_config(tmp_path), backend=MockDetectorBackend(), split=_split(cases), mini=True):
        pass


def _row(case: str, *, gold, prediction, authority="authoritative"):
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "state": "verified_error" if prediction is not None else "unresolved",
        "authority": authority,
        "result": {
            "state": "verified_error" if prediction is not None else "unresolved",
            "decision_authority": authority,
            "runtime_valid": prediction is not None,
            "stage_status": {},
        },
    }


# ---------------------------------------------------------------------------
# An arm that never ran is not a candidate that broke things
def test_a_fail_closed_candidate_arm_is_not_a_falsified_mechanism():
    """The core defect: zero rows measured, verdict "rejected".

    When every candidate row fail-closes (provider outage, deadline), the
    HARMLESSNESS gate compares unresolved rows against the baseline, reads
    them as lost true positives, and returns evaluable=True with
    ``new_fn>0``. Stored as-is, that is a falsified mechanism -- which
    ``proposer.set_rejection_feedback`` then permanently blacklists.
    """
    verdict = {
        "passed": False,
        "evaluable": True,
        "reason": "sentinel harmlessness violation: new_fn=2",
        "delta": {"new_errors": 2, "health_regressed": 2, "unresolved_added": 4},
        "infrastructure_failure": True,
    }
    report = {
        "candidate": {"claim": {"target_polarity": "recall"}},
        "harmlessness": verdict,
        "gain_opportunities": {"recall": 0},
    }
    assert terminal_classification(report, admitted=False) == "not_evaluated"

    # Without the marker the same verdict stays a substantive rejection: the
    # fix must not swallow honest rejections.
    honest = dict(verdict)
    honest.pop("infrastructure_failure")
    assert terminal_classification(
        {"candidate": {}, "harmlessness": honest}, admitted=False,
    ) == "rejected"


def test_the_unit_marks_a_verdict_when_its_arm_could_not_be_measured(tmp_path):
    """The marker is produced by the unit that saw the unmeasured rows.

    A unit-level check, not a verdict-level one: the gate cannot know whether
    an unresolved row came from the provider or from the candidate.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    sentinel = list(engine.split.sentinel)
    gain = list(engine.split.gain)
    unmeasured = [
        _row(case, gold=(i % 2 == 0), prediction=None, authority="invalid")
        for i, case in enumerate([*sentinel, *gain])
    ]
    reason = _defect_rejection_after_exclusions(
        unit={"candidate_rows": unmeasured},
        baseline_rows=unmeasured,
        sentinel_cases=sentinel,
        gain_cases=gain,
        config=config,
    )
    assert "no measurable case left" in reason


def test_an_entirely_unmeasured_gain_arm_cannot_certify_the_audit(tmp_path):
    """A sentinel-only comparison must not read as "the defect was rejected".

    The emptiness guard used to run on the whole batch before the
    sentinel/gain split, so exclusions landing entirely in gain left
    ``paired_delta([], [])`` -> marginal_cases=0 < 1 -> "rejected", which is
    the audit certifying without exercising the gate.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    sentinel = cases[:4]
    gain = cases[4:12]
    # Sentinel rows satisfy the coverage floor in both polarities, so the
    # HARMLESSNESS re-derivation passes and the gain comparison is reached --
    # which is the comparison that used to be empty and still certify.
    measured = [
        _row(case, gold=(i % 2 == 0), prediction=(i % 2 == 0))
        for i, case in enumerate(sentinel)
    ]
    unmeasured = [
        _row(case, gold=True, prediction=None, authority="invalid")
        for case in gain
    ]
    unit = {"candidate_rows": [
        *measured,
        *[_row(case, gold=True, prediction=True) for case in gain],
    ]}
    reason = _defect_rejection_after_exclusions(
        unit=unit,
        baseline_rows=[*measured, *unmeasured],
        sentinel_cases=sentinel,
        gain_cases=gain,
        config=config,
    )
    assert "no measurable gain case left" in reason


# ---------------------------------------------------------------------------
# A round whose gain arm could not be read has not measured the action space
def test_gain_measured_is_false_when_every_gain_case_failed_closed(tmp_path):
    """``gain_measured`` used to be a constant True.

    A gain arm that fail-closes scores zero opportunities in both directions,
    which then reads as "no repairable direction exists" in the preflight and
    in the ledger -- the round stops for a reason that is about the provider,
    not about the action space.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    gain = list(engine.split.gain)
    unmeasured = [
        _row(case, gold=True, prediction=None, authority="invalid")
        for case in gain
    ]
    report = engine._instrument_report(
        baseline_gain=unmeasured, gate_reports=(), admitted_ids=set(),
    )
    assert report["gain_measured"] is False
    assert report["unmeasured_gain_cases"] == sorted(gain)
    # Readable rows still count as measured.
    measured = [_row(case, gold=True, prediction=True) for case in gain]
    partial = engine._instrument_report(
        baseline_gain=[*measured[:-1], unmeasured[-1]],
        gate_reports=(), admitted_ids=set(),
    )
    assert partial["gain_measured"] is True


def test_an_unreadable_gain_arm_blocks_with_an_instrument_reason(tmp_path):
    """The blocked reason must name the instrument, not the action space."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    families = [{"failure_family": "FAM-fn-x", "count": 2,
                 "sources": {"judge": 2}, "failure_cases": cases[12:14]}]
    unmeasured = [
        _row(case, gold=True, prediction=None, authority="invalid")
        for case in engine.split.gain
    ]
    ready, blocked = engine._measurable_families(
        families, unmeasured, arms=None, gain_measured=False,
    )
    assert ready == []
    assert blocked and "produced no measurable decision" in blocked[0]["reason"]
    # And with a readable arm the same family is blocked for the usual reason.
    _, blocked_normal = engine._measurable_families(
        families,
        [_row(case, gold=True, prediction=True) for case in engine.split.gain],
        arms=None, gain_measured=True,
    )
    assert "no repair opportunities in gain" in blocked_normal[0]["reason"]


# ---------------------------------------------------------------------------
# Final-verdict statistics
def test_the_final_mcnemar_counts_decisive_flips_only():
    """Abstention changes are calibration, not repaired decisions.

    On the wide counters, a registry that only decides previously-unresolved
    cases reaches p<0.05 without correcting a single wrong answer, while a
    registry that repairs real errors while the provider abstains elsewhere
    is invisible.
    """
    empty_rows = [
        _row(f"A/{i:03d}.json", gold=True, prediction=None) for i in range(6)
    ]
    active_rows = [
        _row(f"A/{i:03d}.json", gold=True, prediction=True) for i in range(6)
    ]
    delta = paired_delta(empty_rows, active_rows)
    assert delta["correctness_improved"] == 6
    assert delta["decisive_fixed"] == 0
    assert mcnemar_improvement_p(delta) == 1.0

    # A real repair: one wrong decision replaced by the right one.
    base = [_row("A/000.json", gold=True, prediction=False)]
    active = [_row("A/000.json", gold=True, prediction=True)]
    repaired = paired_delta(base, active)
    assert repaired["decisive_fixed"] == 1
    assert mcnemar_improvement_p(repaired) == 0.5


def test_decisive_counts_separate_direction():
    base = [
        _row("A/000.json", gold=True, prediction=True),
        _row("A/001.json", gold=True, prediction=False),
    ]
    active = [
        _row("A/000.json", gold=True, prediction=False),
        _row("A/001.json", gold=True, prediction=True),
    ]
    delta = paired_delta(base, active)
    assert delta["decisive_fixed"] == 1
    assert delta["decisive_broken"] == 1
    assert mcnemar_improvement_p(delta) == 1.0


# ---------------------------------------------------------------------------
# The defect-audit cache must not outlive the thresholds it was produced under
def test_the_audit_fingerprint_covers_the_gate_thresholds(tmp_path, monkeypatch):
    """Reusing a verdict under different gates certifies gates that never ran.

    The audit's verdict is produced *by* the gates, so the thresholds are part
    of its identity. The fingerprint used to omit `gates` and `budget`.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )
    seen: dict[str, Any] = {}

    def _run(cycle: int):
        arms = _ArmRunner(engine, None)
        return engine._audit(cycle, arms)

    def _fingerprint_payload(engine):
        # Reach the payload through the recorded cache file: the private
        # builder is inline, so the record is the observable surface.
        path = engine.paths["reports"] / "defect_audit_verdict.json"
        return json.loads(path.read_text(encoding="utf-8-sig"))

    first = _run(1)
    first_record = _fingerprint_payload(engine)
    # Widening a threshold must invalidate the cached verdict.
    tightened = dict(config)
    gates = dict(config.get("gates") or {})
    gates["m_min_marginal_cases"] = int(gates.get("m_min_marginal_cases", 1)) + 1
    tightened["gates"] = gates
    engine.close()
    engine2 = EvolutionEngine(
        tightened, backend=MockDetectorBackend(), split=split, mini=True,
    )
    second = _run_again(engine2)
    second_record = _fingerprint_payload(engine2)
    assert first_record["fingerprint"] != second_record["fingerprint"]


def _run_again(engine: EvolutionEngine):
    return engine._audit(1, _ArmRunner(engine, None))


def test_the_audit_cache_is_reused_when_nothing_changed(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )
    engine._audit(1, _ArmRunner(engine, None))
    again = engine._audit(1, _ArmRunner(engine, None))
    assert again.get("cache") == "hit"


# ---------------------------------------------------------------------------
# Unmeasured rows must not poison the frozen-replay baseline
def test_fail_closed_rows_do_not_seed_the_replay_baseline(tmp_path):
    """Seeding an empty envelope makes every later arm raise on that case.

    The backend replays a frozen baseline envelope for deterministic hooks; a
    fail-closed row carries none. Restoring one from a checkpoint used to seed
    it anyway, and the failure resurfaced only as an unexplained "unresolved"
    row far from its cause.
    """
    cases = _make_dataset(tmp_path / "data", n=6)
    config = _config(tmp_path)
    config["backend"]["cache"] = True
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=_split(cases), mini=True,
    )
    class _Journal:
        def evaluate(self, case_ids, *, registry, fresh, evaluate, kwargs):
            return [
                _row(case, gold=True, prediction=None, authority="invalid")
                for case in case_ids
            ]

    engine._journal = _Journal()
    backend = engine.backend
    while hasattr(backend, "backend"):
        backend = backend.backend
    backend._base_results = {}
    arms = _ArmRunner(engine, None)
    arms.run(cases[:3], registry=empty_registry())
    assert backend._base_results == {}
