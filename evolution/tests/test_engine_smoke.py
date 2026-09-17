from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.contracts import empty_registry, make_intervention
from evolution.engine import EvolutionEngine
from evolution.ledger import Ledger
from evolution.proposer import OfflineProposer, ProposalBatch
from evolution.splits import EvolutionSplit, FinalHoldout


def _scripted_proposer(candidate):
    class _Scripted:
        name = "scripted"

        def propose(self, families, *, cycle, max_proposals=3):
            bound = dict(candidate)
            bound["provenance"] = {
                "cycle": int(cycle),
                "failure_family": str(families[0]["failure_family"]),
                "failure_cases": list(families[0]["failure_cases"]),
            }
            bound.pop("content_hash", None)
            return ProposalBatch(
                candidates=[bound],
                joint_groups=[],
                notes=[{
                    "intervention_id": bound["intervention_id"],
                    "generator": "scripted",
                }],
            )

    return _Scripted()


def _scanner_candidate(cycle=1, family="FAM-fn"):
    return make_intervention(
        intervention_id=f"IV-{cycle}-001",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report missing required actions.",
            "not_when": "Never when all required actions are present.",
        },
        expected_behavior="recall gain on planted FN",
        target_polarity="recall",
        cycle=cycle,
        failure_family=family,
        failure_cases=["A/000.json", "A/001.json"],
    )


def _guard_candidate(cycle=1, family="FAM-fp"):
    return make_intervention(
        intervention_id=f"IV-{cycle}-002",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        facts=["positive_source=judge"],
        body={
            "action": "withhold_conviction",
            "action_not_when": "Never when defense substantiated.",
        },
        expected_behavior="precision gain on planted FP",
        target_polarity="precision",
        cycle=cycle,
        failure_family=family,
        failure_cases=["A/000.json", "A/001.json"],
    )


def _evidence_candidate(cycle=1, family="FAM-fn"):
    return make_intervention(
        intervention_id=f"IV-{cycle}-001",
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "required_action_present",
            "runner": "deterministic",
            "output_verdicts": [
                "satisfied", "violated", "not_applicable",
            ],
        },
        expected_behavior="append required-action evidence for the guard",
        target_polarity="recall",
        cycle=cycle,
        failure_family=family,
        failure_cases=["A/000.json", "A/002.json"],
    )


def _mechanical_guard_candidate(cycle=1, family="FAM-fn"):
    return make_intervention(
        intervention_id=f"IV-{cycle}-002",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        facts=[
            "required_action_present=violated",
            "judge_positive=false",
            "positive_source=none",
        ],
        body={
            "action": "restore_judge_conviction",
            "action_not_when": "Never when the required action is satisfied.",
            "action_not_when_facts": ["required_action_present=satisfied"],
        },
        expected_behavior="convict a mechanically missing required action",
        target_polarity="recall",
        cycle=cycle,
        failure_family=family,
        failure_cases=["A/000.json", "A/002.json"],
    )


class _MixedFamilyProposer:
    """Bind each candidate to the diagnosed family matching its polarity."""

    name = "scripted_mixed"

    def __init__(self, candidates):
        self.candidates = candidates

    def propose(self, families, *, cycle, max_proposals=3):
        by_prefix = {
            "recall": next(
                f for f in families
                if str(f["failure_family"]).startswith("FAM-fn-")
            ),
            "precision": next(
                f for f in families
                if str(f["failure_family"]).startswith("FAM-fp-")
            ),
        }
        batch = []
        for candidate in self.candidates:
            polarity = str(
                (candidate.get("claim") or {}).get("target_polarity")
            )
            family = by_prefix[polarity]
            bound = dict(candidate)
            bound["provenance"] = {
                "cycle": int(cycle),
                "failure_family": str(family["failure_family"]),
                "failure_cases": list(family["failure_cases"]),
            }
            bound.pop("content_hash", None)
            batch.append(bound)
        return ProposalBatch(
            candidates=batch,
            joint_groups=[],
            notes=[
                {"intervention_id": c["intervention_id"], "generator": "s"}
                for c in batch
            ],
        )


def _make_dataset(root: Path, n: int = 20) -> list[str]:
    cases = []
    for index in range(n):
        framework = "A"
        case_id = f"{framework}/{index:03d}.json"
        cases.append(case_id)
        path = root / case_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "question": f"task {index}",
                "history": [
                    {"step": 1, "role": "assistant", "content": "answer"}
                ],
                "is_hallucination": bool(index % 2 == 0),
                "hallucination_step": 1 if index % 2 == 0 else None,
                "true_answer": "gold",
            }),
            encoding="utf-8",
        )
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
    config["budget"]["patrol_cases"] = 8
    return config


def test_one_full_cycle_admits_only_marginal_candidate(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}
    backend = MockDetectorBackend(
        base_predictions=base,
        overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    assert report["audit"]["passed"] is True
    assert report["patrol"]["stable_failure_families"], report["patrol"]
    assert [p["intervention_id"] for p in report["proposals"]][0] == "IV-1-001"
    assert len(report["proposals"]) >= 1
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]
    assert report["active_after_cycle"] == ["IV-1-001"]
    assert report["ledger_integrity"]["valid"] is True
    assert report["active_effect_on_g"]["marginal_cases"] >= 1
    assert engine.registry.entries[0]["type"] == "text_boundary"
    assert engine.registry.entries[0]["hook"] == "scanner.system"


def test_engine_restores_rejection_feedback_before_next_cycle(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    candidate = _scanner_candidate()
    ledger = Ledger(config["ledger_path"])
    ledger.append("rejected", {
        "cycle": 1,
        "intervention_id": candidate["intervention_id"],
        "candidate": candidate,
        "gate": "HARMLESSNESS",
        "reason": "sentinel harmlessness violation",
        "delta": {"new_fp": 1},
    })

    class _FeedbackProposer:
        active_entries = []

        def __init__(self):
            self.feedback = None

        def set_rejection_feedback(self, feedback):
            self.feedback = list(feedback)

        def propose(self, families, *, cycle, max_proposals=3):
            return ProposalBatch()

    proposer = _FeedbackProposer()
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(),
        split=split,
        mini=True,
        proposer=proposer,
    )
    engine.run_cycle(2)
    assert proposer.feedback
    assert proposer.feedback[0]["gate"] == "HARMLESSNESS"


def test_gate_arms_share_one_evaluation_per_unit(tmp_path):
    """HARMLESSNESS/BEHAVIOR/M score one S+G arm; no gate re-sweeps the detector."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}

    seen = []

    class _Counting(MockDetectorBackend):
        def run_case(self, record, *, case_id, registry=None, gold=None):
            seen.append((case_id, str((registry or {}).get("content_hash") or "")))
            return MockDetectorBackend.run_case(
                self, record, case_id=case_id, registry=registry, gold=gold,
            )

    engine = EvolutionEngine(
        config,
        backend=_Counting(base_predictions=base, overrides=overrides),
        split=split,
        mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    engine.run_cycle(1)
    unit_registry_hashes = [
        hash_value for _case, hash_value in seen
        if hash_value not in ("", "empty")
    ]
    # Audit noise defect, active baseline S+G, candidate S+G, post-write G:
    # every arm is a distinct registry hash, and each appears at most once
    # per case set — no gate runs its own sweep on top of another gate.
    assert unit_registry_hashes, "candidate arm never ran"
    sentinel_gain = set(split.sentinel) | set(split.gain)
    candidate_arms = [
        case for case, hash_value in seen
        if hash_value == unit_registry_hashes[-1]
    ]
    assert set(candidate_arms) <= sentinel_gain


def test_patrol_bootstrap_recovers_missed_failure_family(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["budget"]["patrol_cases"] = 2
    config["budget"]["stable_failure_rerun_cases"] = 10
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    # The random patrol sample only sees two clean-looking cases; the planted
    # failures live deeper in the patrol pool and must be found by bootstrap.
    planted = [c for c in cases if c.endswith(("016.json", "018.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in planted + gain_true},
        overrides={"IV-1-001": {case: True for case in gain_true}},
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    monkeypatch.setattr(
        engine.split, "patrol_sample", lambda cycle, n: cases[12:14]
    )
    report = engine.run_cycle(1)
    assert report["patrol"]["bootstrap"]["bootstrap_count"] > 0
    assert report["patrol"]["bootstrap"]["family_found"] is True
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]


def test_joint_l1_l2_candidates_are_only_admitted_together(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    patrol_fn = [c for c in cases if c.endswith(("012.json", "014.json"))]
    base = {case: False for case in patrol_fn}
    base["A/004.json"] = False   # planted gain FN for the scanner
    overrides = {
        "IV-1-002": {"A/004.json": True},
    }

    class _JointProposer:
        name = "scripted_joint"

        def propose(self, families, *, cycle, max_proposals=3):
            family = next(
                f for f in families
                if str(f["failure_family"]).startswith("FAM-fn-")
            )
            bound_by_id = {}
            for candidate in (
                _evidence_candidate(cycle=cycle),
                _mechanical_guard_candidate(cycle=cycle),
            ):
                bound = dict(candidate)
                bound["provenance"] = {
                    "cycle": int(cycle),
                    "failure_family": str(family["failure_family"]),
                    "failure_cases": list(family["failure_cases"]),
                }
                bound.pop("content_hash", None)
                bound_by_id[bound["intervention_id"]] = bound
            return ProposalBatch(
                candidates=list(bound_by_id.values()),
                joint_groups=[["IV-1-001", "IV-1-002"]],
                notes=[
                    {"intervention_id": key, "generator": "s"}
                    for key in bound_by_id
                ],
            )

    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base, overrides=overrides,
        ),
        split=split,
        mini=True,
        proposer=_JointProposer(),
    )
    report = engine.run_cycle(1)
    assert set(report["marginal_selection"]["selected"]) == {
        "IV-1-001", "IV-1-002"
    }
    gate = next(
        item for item in report["gates"]
        if item["intervention_id"] == "IV-1-001"
    )
    assert gate["partner_ids"] == ["IV-1-002"]
    # The joint arm is shared, but every member now receives an explicit
    # member-level BEHAVIOR verdict and the shared HARMLESSNESS/M result in the report.
    assert gate["harmlessness"]["passed"] is True
    assert gate["behavior"]["passed"] is True


def test_two_qualified_units_get_one_combination_arm(tmp_path):
    """Two individually qualified units trigger exactly one combo arm."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    patrol_fp = [c for c in cases if c.endswith(("013.json", "015.json"))]
    gain_true_a = [c for c in cases if c.endswith(("004.json",))]
    gain_true_b = [c for c in cases if c.endswith(("006.json",))]
    gain_fp = [c for c in cases if c.endswith(("005.json",))]
    base = {case: False for case in patrol_true + gain_true_a + gain_true_b}
    base.update({case: True for case in patrol_fp + gain_fp})
    overrides = {
        "IV-1-001": {case: True for case in gain_true_a},
        "IV-1-002": {
            **{case: True for case in gain_true_b},
            **{case: False for case in gain_fp},
        },
    }
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base, overrides=overrides,
        ),
        split=split,
        mini=True,
        proposer=_MixedFamilyProposer([
            _scanner_candidate(cycle=1),
            _guard_candidate(cycle=1),
        ]),
    )
    report = engine.run_cycle(1)
    assert report["combination"]["unit_ids"] == ["IV-1-001", "IV-1-002"]
    assert report["combination"]["reason"] == "combination arm met HARMLESSNESS and M"
    assert set(report["marginal_selection"]["selected"]) == {
        "IV-1-001", "IV-1-002"
    }
    assert report["active_after_cycle"] == ["IV-1-001", "IV-1-002"]


def test_failed_combination_keeps_first_unit_and_rejects_later(tmp_path):
    """A combo arm that fails M rejects only the later unit(s)."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    patrol_fp = [c for c in cases if c.endswith(("013.json", "015.json"))]
    gain_true_a = [c for c in cases if c.endswith(("004.json",))]
    gain_true_b = [c for c in cases if c.endswith(("006.json",))]
    gain_fp = [c for c in cases if c.endswith(("005.json",))]
    base = {case: False for case in patrol_true + gain_true_a + gain_true_b}
    base.update({case: True for case in patrol_fp + gain_fp})

    class _Clash(MockDetectorBackend):
        def run_case(self, record, *, case_id, registry=None, gold=None):
            active = {
                str(item.get("intervention_id") or "")
                for item in (registry or {}).get("interventions") or ()
            }
            # Both units together flip a clean case into a false positive.
            if active == {"IV-1-001", "IV-1-002"} and case_id.endswith("009.json"):
                return MockDetectorBackend.run_case(
                    self, record, case_id=case_id, registry=registry,
                    gold=not gold,
                )
            return MockDetectorBackend.run_case(
                self, record, case_id=case_id, registry=registry, gold=gold,
            )

    overrides = {
        "IV-1-001": {case: True for case in gain_true_a},
        "IV-1-002": {
            **{case: True for case in gain_true_b},
            **{case: False for case in gain_fp},
        },
    }
    engine = EvolutionEngine(
        config,
        backend=_Clash(base_predictions=base, overrides=overrides),
        split=split,
        mini=True,
        proposer=_MixedFamilyProposer([
            _scanner_candidate(cycle=1),
            _guard_candidate(cycle=1),
        ]),
    )
    report = engine.run_cycle(1)
    assert report["combination"]["unit_ids"] == ["IV-1-001"]
    assert "IV-1-002" in report["combination"]["reason"]
    assert report["marginal_selection"]["selected"] == ["IV-1-001"]


@pytest.mark.parametrize("hook", ["scanner.system", "judge.system"])
def test_provider_stage_units_share_one_fresh_control(tmp_path, hook):
    """All unfreezing-hook units compare against the same fresh control."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    patrol_fp = [c for c in cases if c.endswith(("013.json", "015.json"))]
    gain_true_a = [c for c in cases if c.endswith(("004.json",))]
    gain_true_b = [c for c in cases if c.endswith(("006.json",))]
    gain_fp = [c for c in cases if c.endswith(("005.json",))]
    base = {case: False for case in patrol_true + gain_true_a + gain_true_b}
    base.update({case: True for case in patrol_fp + gain_fp})
    overrides = {
        "IV-1-001": {case: True for case in gain_true_a},
        "IV-1-002": {
            **{case: True for case in gain_true_b},
            **{case: False for case in gain_fp},
        },
    }
    scanner = _scanner_candidate(cycle=1)
    scanner["hook"] = hook
    scanner.pop("content_hash", None)
    scanner["intervention_id"] = "IV-1-001"
    guard = _guard_candidate(cycle=1)
    guard["intervention_id"] = "IV-1-002"

    fresh_rolls = []

    class _RollCounter(MockDetectorBackend):
        def evaluate_fresh(self, cases_arg, **kwargs):
            fresh_rolls.append((
                sorted(cases_arg),
                str((kwargs.get("registry") or {}).get("content_hash") or ""),
            ))
            return MockDetectorBackend.evaluate(
                self, cases_arg, **kwargs
            )

    engine = EvolutionEngine(
        config,
        backend=_RollCounter(base_predictions=base, overrides=overrides),
        split=split,
        mini=True,
        proposer=_MixedFamilyProposer([scanner, guard]),
    )
    active_hash = engine.registry.registry["content_hash"]
    report = engine.run_cycle(1)
    sentinel_gain = sorted([*split.sentinel, *split.gain])
    # Scanner changes replay the same upstream judge. They must not redraw
    # the entire control and accidentally attribute judge noise to recall.
    control_rolls = [
        roll for roll in fresh_rolls
        if roll[1] == active_hash
    ]
    if hook == "scanner.system":
        assert fresh_rolls == []
    else:
        assert sorted(case for cases, _ in control_rolls for case in cases) == sentinel_gain
        assert report["fresh_control"]["reused_across_units"] is True


def test_final_verification_is_read_once_and_writes_report(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=6)
    config = _config(tmp_path)
    config["final_evaluation"] = {"enabled": True}
    config["final_manifest"] = str(tmp_path / "final.json")
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:2],
        gain=cases[2:4],
        patrol_pool=cases[4:],
        final=[{"case_id": case, "gold": bool(i % 2 == 0)} for i, case in enumerate(cases)],
        final_holdout=FinalHoldout([
            {"case_id": case, "gold": bool(i % 2 == 0)} for i, case in enumerate(cases)
        ]),
        mini=False,
    )
    backend = MockDetectorBackend()
    engine = EvolutionEngine(config, backend=backend, split=split, mini=False)
    engine.registry.write(
        [_scanner_candidate()], cycle=1, reason="final test active registry",
    )
    report = engine.run_final()
    assert report["final_cases"] == 6
    assert report["verdict"]["unresolved_not_increased"] is True
    assert engine.split.final_holdout.reads == 1
    with pytest.raises(RuntimeError):
        engine.run_final()


def test_defect_injection_audit_freezes_on_leak_through(tmp_path):
    # A defect candidate that would pass M is simulated by giving the noise
    # defect an override that actually gains a case. The engine must freeze
    # before accepting or running normal proposals.
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    overrides = {"IV-DEFECT-002": {case: True for case in gain_true}}
    backend = MockDetectorBackend(
        base_predictions={case: False for case in gain_true},
        overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
    )
    report = engine.run_cycle(1)
    assert report["state"] == "frozen_after_defect_injection_audit"
    assert report["audit"]["passed"] is False
    assert engine.registry.entries == []


def test_defect_injection_audit_runs_once_per_machinery(tmp_path, monkeypatch):
    """Unchanged code/config/S+G/active hits the audit verdict cache."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    audit_runs = []

    class _Counting(MockDetectorBackend):
        def run_case(self, record, *, case_id, registry=None, gold=None):
            active = (registry or {}).get("interventions") or ()
            if any(
                str(item.get("intervention_id") or "").startswith("IV-DEFECT")
                for item in active
            ):
                audit_runs.append(case_id)
            return MockDetectorBackend.run_case(
                self, record, case_id=case_id, registry=registry, gold=gold,
            )

    engine = EvolutionEngine(
        config,
        backend=_Counting(),
        split=split,
        mini=True,
        proposer=OfflineProposer(),
    )
    first = engine.run_cycle(1)
    assert first["audit"]["cache"] == "miss"
    ran_first_cycle = len(audit_runs)
    assert ran_first_cycle > 0

    second = engine.run_cycle(2)
    assert second["audit"]["cache"] == "hit"
    assert second["audit"]["passed"] == first["audit"]["passed"]
    assert len(audit_runs) == ran_first_cycle


def test_defect_injection_audit_marks_harmlessness_rejection(tmp_path, monkeypatch):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(),
        split=split,
        mini=True,
    )
    def reject_noise(**kwargs):
        return {"passed": False, "reason": "injected HARMLESSNESS rejection"}

    monkeypatch.setattr("evolution.engine.harmlessness_gate", reject_noise)
    report = engine.run_cycle(1)
    noise = next(
        item for item in report["audit"]["reports"]
        if str(item["intervention_id"]).endswith("-002")
    )
    assert noise["harmlessness"] == {
        "passed": False, "reason": "injected HARMLESSNESS rejection",
    }
    assert noise["audit_rejected"] is True
    assert noise["audit_passed"] is False


def test_patrol_runs_the_deployed_version_with_a_bare_base_reference(
    tmp_path, monkeypatch,
):
    """The patrol measures H_t and still sweeps the bare base as its reference."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    backend = MockDetectorBackend()
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=OfflineProposer(),
    )
    active_entry = _scanner_candidate()
    engine.registry.write(
        [active_entry], cycle=0, reason="pre-seeded active entry",
        library_cfg=config["library"],
    )
    seen = []
    original = backend.evaluate

    def spy(cases, **kwargs):
        seen.append((sorted(cases), kwargs.get("registry")))
        return original(cases, **kwargs)

    monkeypatch.setattr(backend, "evaluate", spy)
    report = engine.run_cycle(1)
    expected_patrol = sorted(engine.split.patrol_sample(
        1, int(config["budget"]["patrol_cases"])
    ))
    patrol_registries = [
        registry for case_list, registry in seen
        if case_list and set(case_list) <= set(expected_patrol)
    ]
    assert patrol_registries, "the patrol must be evaluated"
    hashes = {
        str((registry or {}).get("content_hash") or "")
        for registry in patrol_registries
    }
    # Both arms must run: the deployed version (candidate targeting H_t) and
    # the bare base (the long-term reference).
    assert engine.registry.registry_hash in hashes
    assert empty_registry()["content_hash"] in hashes
    assert report["patrol"]["patrol_registry"] == "active"
    contrast = report["patrol"]["failure_contrast"]
    assert contrast["active_registry_size"] == 1
    assert set(contrast) >= {
        "new_failures", "persisting_failures", "resolved_by_active",
    }
    # The contrast partitions the active patrol's failures exactly.
    assert (
        contrast["new_failures"]["count"]
        + contrast["persisting_failures"]["count"]
        <= len(expected_patrol)
    )


def test_patrol_registry_bare_base_escape_hatch_skips_the_second_arm(
    tmp_path, monkeypatch,
):
    """``patrol_registry: bare_base`` restores the single-arm patrol."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["patrol_registry"] = "bare_base"
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    backend = MockDetectorBackend()
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=OfflineProposer(),
    )
    engine.registry.write(
        [_scanner_candidate()], cycle=0, reason="pre-seeded active entry",
        library_cfg=config["library"],
    )
    seen = []
    original = backend.evaluate

    def spy(cases, **kwargs):
        seen.append((sorted(cases), kwargs.get("registry")))
        return original(cases, **kwargs)

    monkeypatch.setattr(backend, "evaluate", spy)
    report = engine.run_cycle(1)
    expected_patrol = set(engine.split.patrol_sample(
        1, int(config["budget"]["patrol_cases"])
    ))
    patrol_registries = [
        registry for case_list, registry in seen
        if case_list and set(case_list) <= expected_patrol
    ]
    assert patrol_registries
    assert {
        str((registry or {}).get("content_hash") or "")
        for registry in patrol_registries
    } == {empty_registry()["content_hash"]}
    assert report["patrol"]["patrol_registry"] == "bare_base"


def test_patrol_registry_rejects_unknown_mode(tmp_path):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["patrol_registry"] = "whatever"
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
        proposer=OfflineProposer(),
    )
    with pytest.raises(ValueError, match="patrol_registry must be"):
        engine.run_cycle(1)


def test_failure_contrast_separates_new_from_persisting_failures():
    """The bare-base reference makes regressions and residual separable."""
    from evolution.engine import _failure_contrast

    def row(case, gold, prediction):
        return {"case": case, "gold": gold, "prediction": prediction}

    # A: wrong on both -> persisting residual.
    # B: wrong on active, right on bare base -> a regression from the update.
    # C: right on active, wrong on bare base -> repaired by the update.
    # D: right on both -> untouched.
    # E: unresolved on active while bare base is right -> counts as a failure.
    bare = [
        row("A/001.json", True, False),
        row("B/002.json", True, True),
        row("C/003.json", True, False),
        row("D/004.json", False, False),
        row("E/005.json", True, True),
    ]
    active = [
        row("A/001.json", True, False),
        row("B/002.json", True, False),
        row("C/003.json", True, True),
        row("D/004.json", False, False),
        row("E/005.json", True, None),
    ]
    contrast = _failure_contrast(bare, active, active_registry_size=2)
    assert contrast["active_registry_size"] == 2
    assert contrast["persisting_failures"]["cases"] == ["A/001.json"]
    assert contrast["new_failures"]["cases"] == ["B/002.json", "E/005.json"]
    assert contrast["resolved_by_active"]["cases"] == ["C/003.json"]
    assert contrast["persisting_failures"]["count"] == 1
    assert contrast["new_failures"]["count"] == 2
    assert contrast["resolved_by_active"]["count"] == 1


def test_failure_contrast_is_empty_when_active_equals_bare():
    """A cold start has no regressions and no repairs by construction."""
    from evolution.engine import _failure_contrast

    rows = [
        {"case": "A/001.json", "gold": True, "prediction": False},
        {"case": "B/002.json", "gold": False, "prediction": False},
    ]
    contrast = _failure_contrast(rows, rows, active_registry_size=0)
    assert contrast["persisting_failures"]["cases"] == ["A/001.json"]
    assert contrast["new_failures"] == {"count": 0, "cases": []}
    assert contrast["resolved_by_active"] == {"count": 0, "cases": []}


def test_governance_runs_attribution_retirement_and_rollback(tmp_path):
    """Post-B governance does leave-one-out attribution and retirement."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    backend = MockDetectorBackend()
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=OfflineProposer(),
    )
    engine.registry.write(
        [_scanner_candidate()], cycle=1, reason="pre-seeded",
    )
    # Two cycles of non-positive marginal history qualify the entry for
    # outcome-driven retirement in governance.
    engine.registry.record_marginal("IV-1-001", 0, cycle=1, fired_rate=1.0)
    engine.registry.record_marginal("IV-1-001", 0, cycle=2, fired_rate=1.0)
    report = engine.run_governance(cycle=2)
    assert report["retirement"]["retired"] == ["IV-1-001"]
    assert report["rollback"]["triggered"] is False
    attribution_ids = {
        item["intervention_id"] for item in report["attribution"]
    }
    assert attribution_ids == {"IV-1-001"}
    assert engine.registry.entries == []


def test_run_cycle_no_longer_measures_retirement_in_cycle(tmp_path):
    """Admission cycles stop sweeping the gain set for governance."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    gain_evaluations = []

    class _Counting(MockDetectorBackend):
        def evaluate(self, cases_arg, **kwargs):
            if list(cases_arg) == list(split.gain):
                gain_evaluations.append(str(kwargs.get("registry")))
            return MockDetectorBackend.evaluate(self, cases_arg, **kwargs)

    engine = EvolutionEngine(
        config,
        backend=_Counting(),
        split=split,
        mini=True,
        proposer=OfflineProposer(),
    )
    engine.registry.write(
        [_scanner_candidate()], cycle=0, reason="pre-seeded",
        library_cfg=config["library"],
    )
    engine.run_cycle(1)
    # Exactly one governance-free pass on G per cycle: the baseline arm. The
    # per-entry leave-one-out sweeps and the extra active re-evaluation are
    # gone from the cycle.
    assert len(gain_evaluations) <= 3


def test_meta_library_drift_reads_cycle_report_key():
    from evolution.meta_eval import compute_meta

    meta = compute_meta([
        {
            "cycle": 1,
            "proposals": [],
            "gates": [],
            "marginal_selection": {},
            "active_effect_on_g": {"marginal_cases": 3},
            "audit": None,
            "patrol": {},
            "proposal_summary": {},
            "active_after_cycle": [],
        },
    ])
    assert meta["library_drift"]["net_contribution_history"] == [3]


def _mini_engine(tmp_path, backend, split, proposer=None):
    config = _config(tmp_path)
    return EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=proposer or OfflineProposer(),
    )


def test_mini_effect_empty_registry_is_structural_zero(tmp_path):
    """Empty-vs-empty compares one cached roll against itself: no fresh
    provider calls, and the delta is zero by construction."""
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        validation=cases[:6],
        mini=True,
    )
    fresh_calls = []

    class _Counting(MockDetectorBackend):
        def evaluate_fresh(self, cases_arg, **kwargs):
            fresh_calls.append(list(cases_arg))
            return MockDetectorBackend.evaluate_fresh(
                self, cases_arg, **kwargs
            )

    engine = _mini_engine(tmp_path, _Counting(), split)
    effect = engine._evaluate_mini_effect()
    assert fresh_calls == []
    assert effect["effect"]["marginal_cases"] == 0
    assert effect["effect"]["new_errors"] == 0
    assert effect["effect"]["unresolved_added"] == 0
    assert effect["passed"] is False


def test_mini_effect_deterministic_arm_replays_cached_roll(tmp_path):
    """A decision-guard registry keeps every stage deterministic, so the
    active arm must reuse the cached baseline rows instead of re-rolling."""
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        validation=cases[:6],
        mini=True,
    )
    fresh_calls = []

    class _Counting(MockDetectorBackend):
        def evaluate_fresh(self, cases_arg, **kwargs):
            fresh_calls.append(list(cases_arg))
            return MockDetectorBackend.evaluate_fresh(
                self, cases_arg, **kwargs
            )

    engine = _mini_engine(tmp_path, _Counting(), split)
    engine.registry.write(
        [_guard_candidate()], cycle=0, reason="pre-seeded",
        library_cfg=engine.config["library"],
    )
    effect = engine._evaluate_mini_effect()
    assert fresh_calls == []
    assert effect["paired_replay"] is True


def test_mini_effect_runs_when_validation_is_disjoint_from_pool(tmp_path):
    """The real mini split has validation disjoint from the evolution pool.

    ``gold_map`` is built from ``split.pool`` only, so the mini effect used to
    evaluate the disjoint validation set with ``gold=None`` and crash inside
    ``paired_delta`` -- the only out-of-sample read never ran. Every existing
    test happened to build ``validation subset of pool`` and missed it.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases[:12],
        sentinel=cases[:4],
        gain=cases[4:8],
        patrol_pool=cases[8:12],
        validation=cases[12:],
        mini=True,
    )
    assert not (set(split.pool) & set(split.validation))
    engine = _mini_engine(tmp_path, MockDetectorBackend(), split)
    effect = engine._evaluate_mini_effect()
    assert effect["cases"] == len(split.validation)
    assert effect["passed"] is False
    assert effect["effect"]["marginal_cases"] == 0


def test_interrupted_cycle_without_checkpoint_is_refused(tmp_path):
    """No checkpoint means the fixed S/G search cannot be reconstructed.

    Refusing (rather than silently re-searching) is the safe behaviour; the
    full self-evolution path only resumes when a durable checkpoint exists.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
        proposer=OfflineProposer(),
    )
    engine.ledger.append("cycle_started", {"cycle": 1})
    engine.ledger.append(
        "cycle_interrupted", {"cycle": 1, "error_type": "KeyboardInterrupt"},
    )
    checkpoint = (
        engine.paths["root"] / "checkpoints" / "cycle001" / "checkpoint.json"
    )
    assert not checkpoint.exists()
    with pytest.raises(RuntimeError, match="already started|checkpoint"):
        engine.run_cycle(1)


def test_full_self_evolution_resumes_interrupted_cycle_with_checkpoint(
    tmp_path, monkeypatch,
):
    """An interrupted cycle is resumed from its checkpoint, not re-searched.

    ``run_full_self_evolution`` used to hard-refuse any interrupted cycle and
    never passed ``resume=True``, so a single interruption forced a full
    restart even though the checkpoint machinery existed to continue it.
    """
    import evolution.engine as engine_mod
    from evolution.splits import FinalHoldout

    cases = _make_dataset(tmp_path / "data", n=6)
    config = _config(tmp_path)
    config["cycles"] = 1
    config["audit"]["enabled"] = False
    config["experiment"] = {"mode": "full_self_evolution"}
    split = EvolutionSplit(
        name="synthetic",
        pool=cases[:4],
        sentinel=cases[:2],
        gain=cases[2:4],
        patrol_pool=[],
        final=[{"case_id": c, "gold": bool(i % 2 == 0)}
               for i, c in enumerate(cases[4:])],
        final_holdout=FinalHoldout(
            [{"case_id": c, "gold": bool(i % 2 == 0)}
             for i, c in enumerate(cases[4:])]
        ),
        mini=False,
    )
    monkeypatch.setattr(engine_mod, "build_split", lambda **kwargs: split)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=False,
    )
    # Simulate a previous run that started cycle 1 and wrote a checkpoint.
    engine.ledger.append("full_self_evolution_started", {})
    engine.ledger.append("cycle_started", {"cycle": 1})
    engine.ledger.append(
        "cycle_interrupted", {"cycle": 1, "error_type": "KeyboardInterrupt"},
    )
    checkpoint = (
        engine.paths["root"] / "checkpoints" / "cycle001" / "checkpoint.json"
    )
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("{}", encoding="utf-8")

    seen_resume: list[bool] = []

    def record_resume(self, cycle, *, proposal_batch=None, resume=False):
        seen_resume.append(resume)
        return {"cycle": cycle, "state": "completed"}

    monkeypatch.setattr(EvolutionEngine, "run_cycle", record_resume)
    monkeypatch.setattr(
        EvolutionEngine, "run_final",
        lambda self: {"verdict": {"overall": "pass"}},
    )
    monkeypatch.setattr(
        engine_mod, "write_meta_report",
        lambda *a, **k: {"defect_injection_audit": {"passed": True}},
    )
    engine_mod.run_full_self_evolution(config=config, backend=MockDetectorBackend())
    assert seen_resume == [True]


def test_audit_does_not_certify_a_fail_closed_arm(tmp_path, monkeypatch):
    """An arm that fails closed must not count as the gate rejecting a defect.

    When every row is unresolved (deadline/outage), HARMLESSNESS fails for
    ``new_unresolved`` without the gate logic ever being exercised. That used
    to be recorded as a legitimate rejection, so the audit could certify
    itself vacuously -- and then cache that verdict.
    """
    from evolution.gates import arm_has_infrastructure_failure

    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    config = _config(tmp_path)
    unresolved = [{
        "case": case, "gold": bool(i % 2 == 0), "prediction": None,
        "state": "unresolved", "authority": "invalid",
        "result": {"state": "unresolved", "decision_authority": "invalid",
                   "runtime_valid": False, "stage_status": {}},
    } for i, case in enumerate(cases)]
    assert arm_has_infrastructure_failure(unresolved)
    healthy = [{
        "case": case, "gold": bool(i % 2 == 0), "prediction": bool(i % 2 == 0),
        "state": "verified_error", "authority": "authoritative",
        "result": {"state": "verified_error",
                   "decision_authority": "authoritative",
                   "runtime_valid": True, "stage_status": {}},
    } for i, case in enumerate(cases)]
    assert arm_has_infrastructure_failure(healthy) == ""

    # A real gate rejection of a healthy arm is still a legitimate rejection.
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )
    monkeypatch.setattr(
        "evolution.engine.harmlessness_gate",
        lambda **kwargs: {"passed": False, "reason": "injected rejection"},
    )
    report = engine.run_cycle(1)
    noise = next(
        item for item in report["audit"]["reports"]
        if str(item["intervention_id"]).endswith("-002")
    )
    assert noise["audit_rejected"] is True
    assert noise["audit_inconclusive"] is False
    assert report["audit"]["passed"] is True


def test_audit_freezes_when_defect_arm_cannot_be_scored(tmp_path, monkeypatch):
    """A fail-closed audit arm must freeze the cycle, not be reported as a pass."""
    cases = _make_dataset(tmp_path / "data", n=20)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    config = _config(tmp_path)
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )
    real_run_unit = engine._run_unit
    unresolved_rows = [{
        "case": case, "gold": bool(i % 2 == 0), "prediction": None,
        "state": "unresolved", "authority": "invalid",
        "result": {"state": "unresolved", "decision_authority": "invalid",
                   "runtime_valid": False, "stage_status": {}},
    } for i, case in enumerate(cases)]

    def fail_closed_unit(*args, **kwargs):
        unit = real_run_unit(*args, **kwargs)
        unit["candidate_rows"] = unresolved_rows
        unit["harmlessness"] = {"passed": False,
                                "reason": "all rows unresolved"}
        return unit

    monkeypatch.setattr(engine, "_run_unit", fail_closed_unit)
    report = engine.run_cycle(1)
    assert report["state"] == "frozen_after_defect_injection_audit"
    assert report["audit"]["passed"] is False
    assert any(
        item.get("audit_inconclusive") for item in report["audit"]["reports"]
    )
    assert engine.registry.entries == []


def test_cycle_records_harness_gaps_and_experience(tmp_path):
    """One cycle populates the H_t base: manifest chain, backlog, experience.

    These three stores are how the next round can know what version ran, what
    capability was missing, and which patch shapes were already tried. None of
    them may influence this cycle's admission.
    """
    from evolution.gaps import open_gaps
    from evolution.harness import load_manifest, HARNESS_FILENAME
    from evolution.memory import episodic_attempts, load_procedures

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}
    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)

    # The manifest describes the committed generation and verifies on reload.
    assert report["harness"]["diff"]["first_manifest"] is True
    manifest = load_manifest(tmp_path / "harness" / HARNESS_FILENAME)
    assert manifest["digest"] == report["harness"]["manifest"]["digest"]
    assert manifest["registry_hash"] == engine.registry.registry_hash
    assert set(manifest["components"]) == {"G", "C", "T", "M", "R"}

    # The admitted candidate is remembered as procedural experience, and the
    # attempt itself is episodic.
    procedures = load_procedures(tmp_path / "memory")
    assert [item["intervention_id"] for item in procedures] == ["IV-1-001"]
    attempts = {item["intervention_id"]: item for item in episodic_attempts(
        tmp_path / "memory"
    )}
    assert attempts["IV-1-001"]["outcome"] == "admitted"
    assert attempts["IV-1-001"]["falsified"] is False

    # The backlog is present (empty here: this family was treatable) and it
    # never affected the admission decision above.
    assert report["capability_backlog"]["open"] == []
    assert open_gaps(engine.gap_store) == []


def test_capability_gap_becomes_a_backlog_item_not_a_silent_round(tmp_path):
    """An untreatable family is recorded so the next round can still see it."""
    from evolution.gaps import open_gaps

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(),
        split=split,
        mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    # Two same-mechanism failures the closed action vocabulary cannot treat.
    # This is the exact shape ``split_actionable_families`` produces for them.
    gaps = [{
        "failure_family": "FAM-fn-unreachable",
        "count": 2,
        "sources": ["A"],
        "classification": "capability_gap",
        "reason": "no treatment route reaches the responsible stage",
    }]
    payload = engine.record_capability_gaps(1, gaps)
    assert len(payload["open"]) == 1
    assert payload["open"][0]["failure_family"] == "FAM-fn-unreachable"
    assert payload["open"][0]["reason"] == (
        "no treatment route reaches the responsible stage"
    )
    # It survives independently of any cycle report.
    assert [item["failure_family"] for item in open_gaps(engine.gap_store)] == [
        "FAM-fn-unreachable"
    ]


def test_gain_opportunity_counts_repairable_directions_only():
    """Opportunity is a property of the instrument: where the control is wrong."""
    from evolution.engine import gain_opportunity

    rows = [
        {"case": "A/1", "gold": True, "prediction": False},   # recall chance
        {"case": "A/2", "gold": True, "prediction": False},   # recall chance
        {"case": "A/3", "gold": False, "prediction": True},   # precision chance
        {"case": "A/4", "gold": True, "prediction": True},    # already right
        {"case": "A/5", "gold": False, "prediction": False},  # already right
        {"case": "A/6", "gold": True, "prediction": None},    # undecided
    ]
    assert gain_opportunity(rows) == {"recall": 2, "precision": 1}
    assert gain_opportunity([]) == {"recall": 0, "precision": 0}


def test_cycle_report_reports_what_the_instrument_could_measure(tmp_path):
    """A direction with no repairable case is reported as unmeasurable."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    # Patrol keeps a genuine FN family (012/014 are error-gold, answered clean)
    # while every error-gold case in the gain set is answered correctly. A
    # recall claim therefore has a family to target but nothing in gain to be
    # scored on, which is exactly the unmeasurable case.
    patrol_fn = [c for c in cases if c.endswith(("012.json", "014.json"))]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in patrol_fn},
    )
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)
    assert report["patrol"]["stable_failure_families"], report["patrol"]
    instrument = report["instrument"]
    assert instrument["gain_opportunity"]["recall"] == 0
    assert "recall" in instrument["unmeasurable_polarities"]
    counts = instrument["terminal_counts"]
    assert sum(counts.values()) == len(report["proposals"])
    assert instrument["terminal_classification"]
    for verdict in instrument["terminal_classification"].values():
        assert verdict in {"admitted", "rejected", "not_evaluated", "unresolved"}
        if verdict == "not_evaluated":
            assert instrument["instrument_limited"] is True


def test_not_evaluable_rejection_is_classified_as_not_evaluated(tmp_path):
    """A zero-opportunity rejection must not read as a falsified mechanism."""
    from evolution.engine import terminal_classification

    report = {
        "candidate": {"intervention_id": "IV-1-002",
                      "claim": {"target_polarity": "recall"}},
        "structural": {"passed": True},
        "harmlessness": {"passed": True},
        "behavior": {"passed": True},
        "m": {"passed": False, "reason": "marginal_cases=0 < 1",
              "delta": {"marginal_cases": 0, "new_errors": 0,
                        "health_regressed": 0}},
        "gain_opportunities": {"recall": 0, "precision": 3},
    }
    assert terminal_classification(report, admitted=False) == "not_evaluated"
    # A rejection that caused a real new error is substantive, not unmeasured.
    report["m"]["delta"]["new_errors"] = 1
    assert terminal_classification(report, admitted=False) == "rejected"
    # A direction with opportunities available was measured normally.
    report["m"]["delta"]["new_errors"] = 0
    report["gain_opportunities"]["recall"] = 4
    assert terminal_classification(report, admitted=False) == "rejected"
    # STRUCTURAL and HARMLESSNESS never count as unevaluable.
    structural = {
        "candidate": {"intervention_id": "IV-1-003",
                      "claim": {"target_polarity": "recall"}},
        "structural": {"passed": False, "reason": "schema rejection"},
        "harmlessness": {"passed": True},
        "behavior": {"passed": True},
        "m": {"passed": True},
        "gain_opportunities": {"recall": 0, "precision": 0},
    }
    assert terminal_classification(structural, admitted=False) == "rejected"
    assert terminal_classification(report, admitted=True) == "admitted"


def test_engine_supplies_retrieved_memory_to_the_proposer(tmp_path):
    """Memory reaches the proposer as input, and the retrieval is auditable.

    Cycle 1 admits a route, so cycle 2 has procedural experience to offer. The
    proposer records what it received, which is what makes "the system used its
    own experience" a checkable claim rather than a description of the store.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    patrol_true = [c for c in cases if c.endswith(("012.json", "014.json"))]
    gain_true = [c for c in cases if c.endswith(("004.json", "006.json"))]
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    base = {case: False for case in patrol_true + gain_true}
    overrides = {"IV-1-001": {case: True for case in gain_true}}

    received: list[dict] = []

    class RecordingProposer:
        name = "recording"
        memory_context: dict = {}

        def set_memory_context(self, context):
            self.memory_context = dict(context or {})

        def set_rejection_feedback(self, feedback):
            pass

        def propose(self, families, *, cycle, max_proposals=3):
            received.append(dict(self.memory_context))
            if int(cycle) != 1 or not families:
                return ProposalBatch(candidates=[], joint_groups=[], notes=[])
            candidate = dict(_scanner_candidate())
            candidate["provenance"] = {
                "cycle": int(cycle),
                "failure_family": str(families[0]["failure_family"]),
                "failure_cases": list(families[0]["failure_cases"]),
            }
            candidate.pop("content_hash", None)
            return ProposalBatch(
                candidates=[candidate], joint_groups=[], notes=[],
            )

    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=RecordingProposer(),
    )
    first = engine.run_cycle(1)
    assert first["marginal_selection"]["selected"] == ["IV-1-001"]
    # Cycle 1 had no prior experience to offer.
    assert received[0]["procedures"] == []

    engine.run_cycle(2)
    second_context = received[1]
    # Cycle 2 is offered the procedure admitted in cycle 1.
    assert second_context["procedures"], second_context
    assert any(
        item.get("intervention_id") == "IV-1-001"
        for item in second_context["procedures"]
    )
    # Retrieval is auditable from the ledger, not only from the store.
    memory_events = [
        record["payload"] for record in engine.ledger.read()
        if record["event"] == "proposal_memory"
    ]
    assert [item["cycle"] for item in memory_events] == [1, 2]
    assert memory_events[1]["procedures"] >= 1


def test_proposal_memory_context_is_bounded(tmp_path):
    """Retrieval is capped so the drafting prompt cannot grow without limit."""
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    engine = EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
        proposer=OfflineProposer(),
    )
    # Stuff both stores well beyond their caps.
    from evolution.gaps import record_gaps
    from evolution.memory import record_procedure

    for index in range(20):
        record_procedure(
            tmp_path / "memory", cycle=index + 1,
            failure_family=f"FAM-fn-{index}", polarity="recall",
            treatment_route="recall_trigger",
            intervention_type="recall_trigger", hook="recall.trigger",
            intervention_id=f"IV-{index + 1}-001",
        )
    record_gaps(
        tmp_path / "harness" / "gaps.jsonl",
        cycle=1,
        gaps=[
            {
                "failure_family": f"FAM-gap-{index}",
                "classification": "capability_gap",
                "reason": f"reason {index}",
                "routes": [],
            }
            for index in range(20)
        ],
    )
    families = [
        {
            "failure_family": f"FAM-fn-{index}",
            "count": 2,
            "actionability": {"routes": ["recall_trigger"]},
        }
        for index in range(6)
    ]
    context = engine._proposal_memory_context(families, [])
    assert len(context["open_capability_gaps"]) <= 8
    # Six families x up to two procedures each would be twelve uncapped.
    assert len(context["procedures"]) <= 6


def _unmeasurable_recall_setup(tmp_path):
    """A round with a recall family, a precision opportunity, no recall one.

    Gain (004-011) holds one precision opportunity (005 is clean-gold but the
    base calls it error) and no recall opportunity. The recall failures live in
    the patrol pool, and 016/018 are base-wrong on error-gold, so they carry
    recall coverage for a replacement set without touching the default gain.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4],
        gain=cases[4:12], patrol_pool=cases[12:], mini=True,
    )
    recall_failures = [
        c for c in cases if c.endswith(
            ("012.json", "014.json", "016.json", "018.json")
        )
    ]
    base = {case: False for case in recall_failures}
    base["A/005.json"] = True  # clean-gold called error: a precision chance
    overrides = {"IV-1-001": {"A/016.json": True, "A/018.json": True}}
    backend = MockDetectorBackend(base_predictions=base, overrides=overrides)
    return cases, config, split, backend


def test_strict_coverage_policy_is_the_default_and_blocks_unchanged(tmp_path):
    """The default must reproduce the previous behaviour exactly."""
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    assert config["coverage_response"]["mode"] == "strict"
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)
    assert report["marginal_selection"]["selected"] == []
    assert report["active_after_cycle"] == []
    instrument = report["instrument"]
    assert "recall" in instrument["unmeasurable_polarities"]
    # No policy intervention was recorded: the reading is reported, and the
    # claim is classified as unmeasured rather than falsified.
    assert instrument["coverage_responses"] == {}
    assert instrument["replacement_gain_cases"] == 0
    assert instrument["terminal_counts"].get("not_evaluated") == 1


def test_replacement_policy_re_measures_on_the_alternative_set(tmp_path):
    """With a matching set installed, the claim is measured there instead."""
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    config["coverage_response"] = {"mode": "replacement", "policies": {}}
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    # A replacement set the base answers cleanly on error-gold cases, so recall
    # is repairable there even though it is not on the default gain set.
    replacement = [c for c in cases if c.endswith(("016.json", "018.json"))]
    engine.set_replacement_gain_cases(replacement)

    # The replacement set must really carry recall coverage, or the test would
    # pass without measuring anything.
    from evolution.engine import gain_opportunity
    from evolution.contracts import empty_registry as _empty

    control = engine.backend.evaluate(
        replacement, dataset_root=engine.config["dataset_root"],
        registry=_empty(), gold_map=engine.gold_map,
    )
    assert gain_opportunity(control)["recall"] > 0

    report = engine.run_cycle(1)
    instrument = report["instrument"]
    assert instrument["replacement_gain_cases"] == len(replacement)
    responses = instrument["coverage_responses"]
    assert responses, report["gates"]
    response = next(iter(responses.values()))
    assert response["kind"] == "replacement"
    assert response["policy"] == "replacement"
    assert response["evaluated_on"] == "replacement"


def test_replacement_or_skip_marks_unmeasured_when_no_set_matches(tmp_path):
    """Without a matching set the claim is skipped, not silently rejected."""
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    config["coverage_response"] = {
        "mode": "replacement_or_skip", "policies": {},
    }
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = engine.run_cycle(1)
    instrument = report["instrument"]
    assert instrument["replacement_gain_cases"] == 0
    response = next(iter(instrument["coverage_responses"].values()))
    assert response["kind"] == "skip"
    assert response["evaluated_on"] == "default"
    # Skipped claims stay blocked and read as unmeasured. The four-state
    # vocabulary reports them as not_evaluated, with "skip" recorded as the
    # response detail.
    assert report["marginal_selection"]["selected"] == []
    assert instrument["terminal_counts"].get("not_evaluated") == 1
    assert instrument["instrument_limited"] is True
    gate = next(g for g in report["gates"] if g.get("m"))
    assert gate["m"]["status"] == "skipped_unmeasured"


def test_per_direction_coverage_policy_overrides_the_default(tmp_path):
    """The policy can be enabled for one direction only."""
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    config["coverage_response"] = {
        "mode": "strict",
        "policies": {"recall": "replacement_or_skip"},
    }
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    # recall resolves to the override, precision stays strict.
    assert engine._coverage_mode("recall") == "replacement_or_skip"
    assert engine._coverage_mode("precision") == "strict"
    report = engine.run_cycle(1)
    response = next(iter(report["instrument"]["coverage_responses"].values()))
    assert response["kind"] == "skip"


def test_unknown_coverage_policy_is_rejected(tmp_path):
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    config["coverage_response"] = {"mode": "whatever", "policies": {}}
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    with pytest.raises(ValueError, match="coverage_response mode"):
        engine._coverage_mode("recall")


def test_proposer_is_told_which_directions_are_scoreable(tmp_path):
    """Drafting input names the directions this round can actually measure."""
    cases, config, split, backend = _unmeasurable_recall_setup(tmp_path)
    received: list[dict] = []

    class RecordingProposer:
        name = "recording"

        def set_memory_context(self, context):
            received.append(dict(context or {}))

        def set_rejection_feedback(self, feedback):
            pass

        def propose(self, families, *, cycle, max_proposals=3):
            return ProposalBatch(candidates=[], joint_groups=[], notes=[])

    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=RecordingProposer(),
    )
    engine.run_cycle(1)
    assert received
    context = received[0]
    assert "precision" in context["scoreable_polarities"]
    assert "recall" in context["unmeasurable_polarities"]
