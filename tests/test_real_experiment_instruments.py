"""The large-scale experiment's own instruments, tested offline.

Three scripts carry the experiment's headline numbers, and each has one
property that is easy to get wrong in a way that silently flatters or damns the
result:

* ``freeze_measurement_manifest`` -- the order *is* the pre-registration. A
  re-sorted or non-rotating list would mean the run measures a different sample
  than the one registered, which is precisely the "every archived reading came
  from the same ten cases" failure §33.2 records.
* ``paired_endpoint`` -- the endpoint must count a decisive flip, and must
  report zero net for an arm identical to its baseline. A reader that always
  answered "no effect" would pass a smoke test and destroy the experiment.
* ``read_multigen`` -- must read the per-candidate verdict from ``gates`` (the
  candidates that were scored), not from ``proposals`` (the candidates that
  were drafted). §31 run 1 drafted candidates and scored none.

Every test here is offline: the corpus labels come from ``datasets/`` and the
run trees are built in ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.result import RESULT_SCHEMA  # noqa: E402
from detector.runner import safe_case_id  # noqa: E402
from evolution.splits import load_gold  # noqa: E402
from scripts import freeze_measurement_manifest as freeze  # noqa: E402
from scripts import paired_endpoint as endpoint_mod  # noqa: E402
from scripts import read_multigen  # noqa: E402

DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
CONFIG = ROOT / "evolution/configs/selfevo_real_dev2_20260923.json"


# --------------------------------------------------------------------------
# freeze_measurement_manifest
# --------------------------------------------------------------------------


def test_rotation_covers_every_source_at_every_prefix_depth():
    """The property the whole design rests on: any prefix is stratified.

    ``run_batch`` submits in list order, so if the order clustered by source a
    stopped run would measure the first few sources only -- a selection bias
    introduced by the clock. Round-robin is what removes it, and this asserts
    it directly rather than trusting the implementation.
    """
    buckets = {
        "A": [f"A/{n:03d}.json" for n in range(5)],
        "B": [f"B/{n:03d}.json" for n in range(3)],
        "C": [f"C/{n:03d}.json" for n in range(4)],
    }
    cases = [case for group in buckets.values() for case in group]
    ordered = freeze.rotation_order(cases, seed=7)
    assert sorted(ordered) == sorted(cases), "rotation lost or duplicated a case"
    # Depth 1 must already contain one case from every source.
    assert len({case.split("/")[0] for case in ordered[:3]}) == 3
    # No source may appear twice before every source has appeared once.
    for depth in range(1, 4):
        prefix = ordered[:depth]
        assert len(set(prefix)) == depth, "a source repeated inside one lap"
    # The short source drains without stalling the others.
    assert ordered[-1].startswith("A") or ordered[-1].startswith("C")


def test_rotation_is_deterministic_and_seed_sensitive():
    cases = [f"{s}/{n:02d}.json" for s in ("A", "B", "C") for n in range(4)]
    assert freeze.rotation_order(cases, seed=1) == freeze.rotation_order(cases, seed=1)
    assert freeze.rotation_order(cases, seed=1) != freeze.rotation_order(cases, seed=2)


def test_case_list_digest_is_order_sensitive():
    """A re-sorted list is a different experiment and must not share a digest."""
    a = freeze.case_list_digest(["A/1.json", "B/2.json"])
    b = freeze.case_list_digest(["B/2.json", "A/1.json"])
    assert a != b
    assert a == freeze.case_list_digest(["A/1.json", "B/2.json"])


def test_manifest_excludes_the_evolution_loops_own_exposure_surface():
    """The 40 mini-split cases must not be measured by the experiment.

    Including them is what would make a deployed-arm improvement
    indistinguishable from "the loop already looked at these cases".
    """
    manifest = freeze.build_manifest(
        config_path=CONFIG, dataset_root=DATASET_ROOT, seed=20260923,
    )
    from evolution.config import load_config
    from evolution.splits import build_split

    split = build_split(
        config=load_config(CONFIG), dataset_root=DATASET_ROOT, mini=True,
    )
    excluded = set(split.sentinel) | set(split.gain) | set(
        split.patrol_pool
    ) | set(split.validation)
    assert len(excluded) == 40
    assert not (set(manifest["cases"]) & excluded)
    assert manifest["counts"]["corpus"] == 693
    assert manifest["counts"]["measurable"] == 693 - len(excluded)
    # The validation set is the part a pool-only read would silently keep.
    assert set(split.validation) & set(manifest["cases"]) == set()


# --------------------------------------------------------------------------
# paired_endpoint
# --------------------------------------------------------------------------


def _write_case(run_root: Path, case: str, *, state: str, authoritative: bool = True):
    result = {
        "schema_version": RESULT_SCHEMA,
        "case_id": case,
        "state": state,
        "occurrence_state": state,
        "decision_authority": "authoritative" if authoritative else "degraded",
        "runtime_valid": True,
        "incident_occurred": state == "verified_error",
        "providers": {},
    }
    from detector.runner import record_case
    record_case(runs_root=run_root.parent, run_id=run_root.name, case_id=case,
                input_record={"question": "q", "history": []}, result=result, elapsed_seconds=1)


def _make_arm(root: Path, cases: list[str], verdicts: dict[str, str]) -> Path:
    arm = root / "arm"
    arm.mkdir(parents=True, exist_ok=True)
    (arm / "run.json").write_text(
        json.dumps({"schema_version": "detector.run_manifest.v1",
                    "run_id": "arm", "cases": cases}),
        encoding="utf-8",
    )
    for case in cases:
        _write_case(arm, case, state=verdicts[case])
    return arm


def _three_cases():
    """One gold-positive and two gold-negative cases that exist in the corpus."""
    from evolution.splits import load_gold

    exposure = json.loads(
        (ROOT / "datasets/protocols/development_exposure.json").read_text(
            encoding="utf-8-sig"
        )
    )["cases"]
    positives = [c for c in exposure if load_gold(DATASET_ROOT, c) is True]
    negatives = [c for c in exposure if load_gold(DATASET_ROOT, c) is False]
    return [positives[0], negatives[0], negatives[1]]


def test_endpoint_reports_no_effect_when_the_arms_are_identical(tmp_path):
    cases = _three_cases()
    verdicts = {cases[0]: "verified_error",
                cases[1]: "verified_error",
                cases[2]: "verified_clean"}
    arm = _make_arm(tmp_path, cases, verdicts)
    payload = endpoint_mod.endpoint(
        baseline=arm, baseline_roll=arm, deployed=arm,
        dataset_root=DATASET_ROOT, json_out=None,
    )
    assert payload["verdict"]["decisive_fixed"] == 0
    assert payload["verdict"]["decisive_broken"] == 0
    assert payload["verdict"]["effect_established"] is False
    assert payload["noise_floor"]["flip_rate"] == 0.0


def test_endpoint_counts_a_decisive_fix_and_a_decisive_break(tmp_path):
    """One fixed false positive must move the verdict; one lost true positive must too.

    A reader that could not see a flip would report "no effect" for every
    experiment, including one that worked.
    """
    cases = _three_cases()
    poisoned = {cases[0]: "verified_error",   # gold positive, wrong -> should be fixed
                cases[1]: "verified_error",   # gold negative, wrong -> should be fixed
                cases[2]: "verified_error"}   # gold negative, wrong
    baseline = _make_arm(tmp_path / "base", cases, poisoned)
    fixed = dict(poisoned)
    fixed[cases[1]] = "verified_clean"       # a false positive corrected
    deployed = _make_arm(tmp_path / "dep", cases, fixed)
    payload = endpoint_mod.endpoint(
        baseline=baseline, baseline_roll=baseline, deployed=deployed,
        dataset_root=DATASET_ROOT, json_out=None,
    )
    assert payload["verdict"]["decisive_fixed"] == 1
    assert payload["verdict"]["decisive_broken"] == 0
    assert payload["verdict"]["effect_established"] is True

    broken = dict(poisoned)
    broken[cases[0]] = "verified_clean"      # a true positive lost
    deployed = _make_arm(tmp_path / "dep2", cases, broken)
    payload = endpoint_mod.endpoint(
        baseline=baseline, baseline_roll=baseline, deployed=deployed,
        dataset_root=DATASET_ROOT, json_out=None,
    )
    assert payload["verdict"]["decisive_fixed"] == 0
    assert payload["verdict"]["decisive_broken"] == 1
    assert payload["verdict"]["effect_established"] is False


def test_endpoint_excludes_unstable_cases_from_the_stable_reading(tmp_path):
    """A case the baseline itself reads two ways cannot score as an improvement."""
    cases = _three_cases()
    verdicts = {cases[0]: "verified_error", cases[1]: "verified_clean",
                cases[2]: "verified_clean"}
    roll1 = _make_arm(tmp_path / "r1", cases, verdicts)
    roll2_verdicts = dict(verdicts)
    roll2_verdicts[cases[1]] = "verified_error"   # baseline flips on this case
    roll2 = _make_arm(tmp_path / "r2", cases, roll2_verdicts)
    deployed_verdicts = dict(verdicts)
    deployed_verdicts[cases[1]] = "verified_error"  # deployed "gains" on the flip
    deployed = _make_arm(tmp_path / "d", cases, deployed_verdicts)
    payload = endpoint_mod.endpoint(
        baseline=roll1, baseline_roll=roll2, deployed=deployed,
        dataset_root=DATASET_ROOT, json_out=None,
    )
    assert payload["noise_floor"]["flipped_cases"] == [cases[1]]
    assert payload["unstable_cases"] == [cases[1]]
    assert payload["stable_cases_only"]["paired_n"] == 2
    assert payload["all_cases"]["paired_n"] == 3


def test_endpoint_refuses_a_deployed_arm_with_a_different_case_set(tmp_path):
    """A shorter deployed arm must not shrink its own denominator silently."""
    cases = _three_cases()
    verdicts = {case: "verified_clean" for case in cases}
    baseline = _make_arm(tmp_path / "b", cases, verdicts)
    deployed = _make_arm(tmp_path / "d", cases[:2], {
        case: "verified_clean" for case in cases[:2]
    })
    with pytest.raises(SystemExit):
        endpoint_mod.endpoint(
            baseline=baseline, baseline_roll=None, deployed=deployed,
            dataset_root=DATASET_ROOT, json_out=None,
        )


# --------------------------------------------------------------------------
# read_multigen
# --------------------------------------------------------------------------


def test_multigen_reads_verdicts_from_gates_not_proposals(tmp_path):
    """Drafted-but-unscored candidates must not be reported as verdicts.

    §31 run 1 is the case: candidates were drafted and none reached a gate. A
    reader that counted ``proposals`` would report it as if the gates had run
    and rejected them.
    """
    run = tmp_path / "run"
    (run / "cycles").mkdir(parents=True)
    report = {
        "cycle": 1,
        "state": "completed",
        "proposals": [{"intervention_id": "IV-1-001"}, {"intervention_id": "IV-1-002"}],
        "gates": [],
        "instrument": {"terminal_classification": {}},
    }
    (run / "cycles" / "cycle001.json").write_text(
        json.dumps(report), encoding="utf-8",
    )
    payload = read_multigen.read_run(run)
    assert payload["cycles"][0]["proposals_drafted"] == 2
    assert payload["summary"]["candidates_total"] == 0


def test_multigen_separates_unmeasured_from_falsified(tmp_path):
    """The §32.3 distinction: an arm that never ran is not a refutation.

    The marker is ``infrastructure_failure`` -- the key the engine's own
    ``gate_rejection_is_unevaluable`` reads. A reader looking for a key the
    engine never sets would report "measured" for every fail-closed arm, which
    is the defect §32.3 fixed.
    """
    run = tmp_path / "run"
    (run / "cycles").mkdir(parents=True)
    (run / "cycles" / "cycle001.json").write_text(json.dumps({
        "cycle": 1,
        "state": "completed",
        "proposals": [],
        "gates": [
            {
                "intervention_id": "IV-1-001",
                "structural": {"passed": True, "reason": ""},
                "harmlessness": {
                    "passed": False,
                    "reason": "candidate arm failed closed on every case",
                    "infrastructure_failure": True,
                },
                "behavior": None,
                "m": None,
            },
        ],
        "instrument": {"terminal_classification": {}},
    }), encoding="utf-8")
    payload = read_multigen.read_run(run)
    row = payload["cycles"][0]["candidates"][0]
    # Not measurable -> the classifier must not call it rejected...
    assert row["classification"] in ("not_evaluated", "unresolved")
    # ...and the arm's failure to run is named rather than folded into a fail.
    assert row["arm_failed_closed"] is True
    assert row["unmeasurable_markers"]["infrastructure_failure"] is True


def test_multigen_calls_a_measured_regression_rejected(tmp_path):
    """The other direction: a real, measured harm is a refutation.

    Without this the reader could pass the test above by never saying
    "rejected" at all, which would erase the distinction instead of drawing it.
    """
    run = tmp_path / "run"
    (run / "cycles").mkdir(parents=True)
    (run / "cycles" / "cycle001.json").write_text(json.dumps({
        "cycle": 1,
        "state": "completed",
        "proposals": [],
        "gates": [{
            "intervention_id": "IV-1-001",
            "structural": {"passed": True, "reason": ""},
            "harmlessness": {
                "passed": False,
                "reason": "sentinel harmlessness violation: new_fn=2",
                "infrastructure_failure": False,
                "evaluable": True,
                "delta": {"new_fn": 2, "new_errors": 2},
            },
            "behavior": None,
            "m": None,
        }],
        "instrument": {"terminal_classification": {}},
    }), encoding="utf-8")
    payload = read_multigen.read_run(run)
    row = payload["cycles"][0]["candidates"][0]
    assert row["classification"] == "rejected"
    assert row["arm_failed_closed"] is False


def test_multigen_flags_candidates_that_lack_a_five_question_diagnosis(tmp_path):
    run = tmp_path / "run"
    (run / "cycles").mkdir(parents=True)
    (run / "cycles" / "cycle001.json").write_text(json.dumps({
        "cycle": 1,
        "state": "completed",
        "proposals": [],
        "gates": [{
            "intervention_id": "IV-1-001",
            "structural": {"passed": False, "reason": "jaccard too low"},
            "harmlessness": None, "behavior": None, "m": None,
        }],
        "instrument": {"terminal_classification": {}},
    }), encoding="utf-8")
    payload = read_multigen.read_run(run)
    assert payload["summary"]["candidates_without_a_diagnosis"] == 1
    assert payload["p2_answered"] is False
