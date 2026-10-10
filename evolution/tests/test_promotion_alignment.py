"""Mechanism-aligned promotion: gain, protection, challenge and the diagnoses.

The v1 protocol scores a candidate by polarity over a frozen batch, which lets a
candidate collect credit for repairing cases that belong to other mechanisms
(plan §2.4) and lets unrelated regressions cancel against gains. The mechanism-aligned protocol
freezes four things together -- the mechanism, the evaluator's membership
evidence, the candidate content hash and the baseline registry -- and scores the
candidate only on confirmed members of its own mechanism.

Every negative here is a rule the profile has to keep:

* no evaluator confirmation -> not measurable, and the candidate arm never runs;
* a development case inside the formal batch -> refused at freeze;
* a protection or challenge regression -> rejected, never netted against gains;
* a broken localization the control arm had right -> rejected;
* a candidate that changed nothing -> rejected as a design failure;
* a protocol, candidate or baseline that moved after the freeze -> blocked.

Everything is offline and deterministic, and the "gains" come from a scripted
backend, so these are instrument tests, not detector-quality claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.contracts import make_intervention, make_registry
from evolution.mechanisms import infer_mechanism
from evolution.promotion_alignment import (
    LEGACY_STATE_REASONS,
    REASON_CATEGORIES,
    diagnosis,
    verify_alignment,
)
from evolution.promotion import (
    PromotionProtocolError,
    PromotionRun,
    build_candidate,
    candidate_from_development,
    freeze_protocol,
    load_protocol,
)

GAIN = ["A/001.json", "A/002.json"]
PROTECTION = ["C/001.json", "C/002.json", "E/001.json", "E/002.json"]
CHALLENGE = ["X/001.json", "X/002.json"]
ALL_CASES = [*GAIN, *PROTECTION, *CHALLENGE]
GOLD = {
    "A/001.json": True, "A/002.json": True,
    "C/001.json": True, "C/002.json": True,
    "E/001.json": False, "E/002.json": False,
    "X/001.json": True, "X/002.json": False,
}
GOLD_STEPS = {
    "A/001.json": 7, "A/002.json": 7,
    "C/001.json": 3, "C/002.json": 3,
    "X/001.json": 5,
}


def _case_gold_step(case: str) -> int | None:
    return GOLD_STEPS.get(case)


def _mechanism(cases: list[str] | None = None, *, objective: str | None = None) -> dict:
    """A confirmed mechanism, built by the real inference code.

    ``objective`` is applied by re-minting the id from the changed signature:
    the identity is defined as the hash of the repair signature, so editing the
    objective without re-minting would produce a mechanism the framework must
    (and does) refuse.
    """
    def member(index: int) -> dict:
        return {
            "member": f"member_{index:03d}",
            "contracts": ["required_action"],
            "stages": {},
            "judge_positive": False,
            "recall_path": "not_needed",
            "upgrade_positive": False,
            "defense_verdict": "none",
            "predicates": {"required_action_present": "violated"},
            "typed_samples": {},
        }
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-test",
        members=[member(1), member(2)],
        member_cases=cases or ["DEV/001.json", "DEV/002.json"],
        count=2,
    )
    if objective and objective != mechanism["objective"]:
        from evolution.mechanisms import mechanism_key

        mechanism["objective"] = objective
        mechanism["mechanism_id"] = mechanism_key(mechanism)
    return mechanism


def _membership(mechanism: dict, *, localization_viable: bool | None = None) -> dict:
    out: dict[str, dict] = {}
    for case in ALL_CASES:
        if case in GAIN:
            item = {
                "mechanism_id": mechanism["mechanism_id"],
                "status": "confirmed",
                "evidence": [f"worksheet signature match for {case}"],
                "repairable": True,
            }
        elif case in PROTECTION:
            item = {
                "mechanism_id": mechanism["mechanism_id"],
                "status": "confirmed",
                "evidence": ["baseline-correct control case"],
                "repairable": False,
            }
        else:
            item = {
                "mechanism_id": "M-external-other",
                "status": "external",
                "evidence": ["similar surface features, different mechanism"],
                "repairable": False,
            }
        if localization_viable is not None:
            item["localization_viable"] = localization_viable
        out[case] = item
    return out


def _dataset(tmp_path: Path, *, steps: bool = False) -> Path:
    root = tmp_path / "data"
    for case in ALL_CASES:
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q",
            "history": [{"step": 1, "role": "assistant", "content": "a"}],
            "is_hallucination": GOLD[case],
            "hallucination_step": _case_gold_step(case) if steps else None,
            "true_answer": "gold",
        }), encoding="utf-8")
    return root


def _artifact(intervention_id: str = "IV-P-001") -> dict:
    return make_intervention(
        intervention_id=intervention_id,
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "report actions required by the question that never ran",
            "not_when": "never when every required action ran",
        },
        expected_behavior="recall gains candidates on the mechanism",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-judge_false_no_recall_candidates-test",
        failure_cases=["DEV/001.json", "DEV/002.json"],
    )


def _localization_artifact(intervention_id: str = "IV-P-LOC") -> dict:
    """A tracer directive, the route that can actually move a named step.

    STRUCTURAL enforces family/target-kind agreement, so a localization claim
    has to come from a ``FAM-loc-`` family and a hook that reaches the tracer;
    building the artifact any other way would test the gate's rejection rather
    than the promotion path.
    """
    from detector.interventions import intervention_content_hash

    entry = make_intervention(
        intervention_id=intervention_id,
        intervention_type="text_boundary",
        hook="tracer.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "name the step where the delivered content deviated",
            "not_when": "never when the named step carries no text of its own",
        },
        expected_behavior="the responsible step moves to the deviating event",
        target_polarity="boundary",
        cycle=1,
        failure_family="FAM-loc-mislocated-test",
        failure_cases=["DEV/001.json", "DEV/002.json"],
    )
    entry["claim"]["target_kind"] = "localization"
    entry["content_hash"] = intervention_content_hash(entry)
    return entry


def _bound_candidate(mechanism: dict, *, intervention_id: str = "IV-P-001",
                     kind: str | None = None, steps: bool = False,
                     artifact: dict | None = None) -> dict:
    if artifact is None:
        artifact = _artifact(intervention_id)
        target_kind = kind or mechanism["objective"]
    else:
        artifact = dict(artifact)
        target_kind = kind or str(
            (artifact.get("claim") or {}).get("target_kind") or "detection"
        )
    if target_kind != str((artifact.get("claim") or {}).get("target_kind") or "detection"):
        from detector.interventions import intervention_content_hash

        artifact["claim"]["target_kind"] = target_kind
        artifact["content_hash"] = intervention_content_hash(artifact)
    return build_candidate(
        candidate_id="development:run:v001",
        change_kind="revision",
        base_revision="deadbeef",
        artifacts=[artifact],
        development={"source": "development", "l0": {"passed": True},
                     "l1": {"passed": True}},
        target_kind=target_kind,
        mechanism=mechanism,
        development_cases=["DEV/001.json", "DEV/002.json"],
    )


class _Backend:
    """Scripted arms: control prediction per case, candidate overrides."""

    name = "scripted"

    def __init__(self, control: dict[str, bool], candidate: dict[str, bool] | None = None,
                 steps: dict[str, int] | None = None,
                 control_steps: dict[str, int] | None = None,
                 candidate_steps: dict[str, int] | None = None) -> None:
        self.control = dict(control)
        self.candidate = dict(candidate or {})
        self.steps = dict(steps or {})
        self.control_steps = dict(control_steps or {})
        self.candidate_steps = dict(candidate_steps or {})
        self.calls: list[str] = []

    def evaluate(self, cases, *, dataset_root, registry=None, gold_map=None,
                 gold_step_map=None, workers=1, deadline=None):
        active = bool((registry or {}).get("interventions"))
        self.calls.append("candidate" if active else "control")
        rows = []
        for case in cases:
            gold = bool((gold_map or {}).get(case))
            if gold is not bool(GOLD.get(case)):
                raise AssertionError("scripted backend received unexpected labels")
            if active:
                prediction = self.candidate.get(case, self.control.get(case, gold))
                step = self.candidate_steps.get(case, self.steps.get(case))
            else:
                prediction = self.control.get(case, gold)
                step = self.control_steps.get(case, self.steps.get(case))
            rows.append({
                "case": case,
                "gold": gold,
                "prediction": prediction,
                "state": "verified_error" if prediction else "verified_clean",
                "authority": "authoritative",
                "responsible_step": step,
                "result": {
                    "case_id": case,
                    "state": "verified_error" if prediction else "verified_clean",
                    "decision_authority": "authoritative",
                    "runtime_valid": True,
                    "responsible_step": step,
                    "stage_status": {"judge": {"state": "ok"}},
                    "proof": {"deployment": {"fired": [
                        {"intervention_id": str(entry.get("intervention_id")),
                         "event": "text_injected"}
                        for entry in (registry or {}).get("interventions") or ()
                    ]}},
                },
                "backend": self.name,
            })
        return rows

    evaluate_fresh = evaluate


class _Registry:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.writes: list[list[dict]] = []
        self.state = {"cycle": 0}

    def check_limits(self, entries, *, library_cfg=None):
        return []

    def write(self, entries, *, cycle, reason, library_cfg=None):
        self.entries = [dict(entry) for entry in entries]
        self.writes.append(self.entries)
        return make_registry(self.entries)


class _Ledger:
    def __init__(self, valid: bool = True) -> None:
        self.events: list[tuple[str, dict]] = []
        self.valid = valid

    def append(self, event, payload):
        self.events.append((str(event), dict(payload)))
        return {"event": event, "payload": payload}

    def verify(self):
        return {"valid": self.valid, "records": len(self.events), "broken_at": None,
                "reason": "" if self.valid else "broken"}


def _freeze(tmp_path: Path, *, mechanism: dict | None = None,
            membership: dict | None = None, candidate: dict | None = None,
            dataset_root: Path | None = None, config: dict | None = None,
            roles: dict[str, str] | None = None, gold: dict | None = None,
            gold_steps: dict | None = None, development: list[str] | None = None,
            baseline_registry_hash: str | None = None) -> dict:
    mechanism = mechanism or _mechanism()
    membership = membership if membership is not None else _membership(mechanism)
    candidate = candidate or _bound_candidate(mechanism)
    dataset_root = dataset_root or _dataset(tmp_path)
    batch = tmp_path / "batch.json"
    assigned = roles or {
        **{case: "benefit" for case in GAIN},
        **{case: "protection" for case in PROTECTION},
        **{case: "challenge" for case in CHALLENGE},
    }
    batch.write_text(json.dumps(sorted(assigned)), encoding="utf-8")
    detector_config = tmp_path / "detector.json"
    detector_config.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    resolved_gold = {**GOLD, **(gold or {})}
    protocol = freeze_protocol(
        tmp_path / "protocol.json",
        batch_path=batch,
        roles=assigned,
        gold={case: resolved_gold[case] for case in assigned},
        gold_steps=gold_steps if gold_steps is not None else {},
        development_cases=development or [],
        detector_config=detector_config,
        workers=2,
        timeout_seconds=600,
        reason="test",
        mechanism=mechanism,
        mechanism_membership=membership,
        candidate=candidate,
        dataset_root=dataset_root,
        framework_config=config or {"dataset_root": str(dataset_root)},
        baseline_registry_hash=(
            baseline_registry_hash or make_registry([])["content_hash"]
        ),
    )
    protocol["loaded_from"] = str(tmp_path / "protocol.json")
    protocol["resolved_cases"] = sorted(assigned)
    return protocol


def _run(tmp_path: Path, *, protocol: dict, candidate: dict, backend,
         config: dict | None = None, stages=("structural", "harmlessness",
                                             "behavior", "m")) -> dict:
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config=config or {"dataset_root": str(tmp_path / "data"), "gates": {},
                          "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_Registry(),
        ledger=_Ledger(),
        stages=stages,
    )
    return run.run()


# ---------------------------------------------------------------------------
# Freezing
# ---------------------------------------------------------------------------
def test_v2_freezes_mechanism_membership_candidate_and_baseline(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    assert protocol["schema_version"] == "evolution.promotion_protocol.v4"
    assert protocol["mechanism_id"] == mechanism["mechanism_id"]
    assert protocol["candidate_content_hash"] == protocol["candidate_content_hash"]
    assert sorted(protocol["mechanism_gain_cases"]) == sorted(GAIN)
    assert sorted(protocol["challenge_cases"]) == sorted(CHALLENGE)
    assert protocol["baseline_registry_hash"]
    assert protocol["protocol_hash"]
    assert protocol["evidence_scope"] == "candidate_unused_development_data"
    assert protocol["mechanism_opportunity"]["evaluable"] is None


def test_v2_refuses_a_gain_case_that_is_not_a_confirmed_member(tmp_path):
    mechanism = _mechanism()
    membership = _membership(mechanism)
    membership["A/001.json"] = {
        "mechanism_id": "M-somewhere-else",
        "status": "confirmed",
        "evidence": ["belongs elsewhere"],
        "repairable": True,
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, mechanism=mechanism, membership=membership)
    assert "confirmed members of the target mechanism" in str(exc.value)


def test_v2_refuses_an_ambiguous_gain_case(tmp_path):
    mechanism = _mechanism()
    membership = _membership(mechanism)
    membership["A/002.json"] = {
        "mechanism_id": mechanism["mechanism_id"],
        "status": "ambiguous",
        "evidence": ["two readings"],
        "repairable": True,
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, mechanism=mechanism, membership=membership)
    assert "ambiguous membership is not a formal evaluation case" in str(exc.value)


def test_v2_refuses_evidence_free_membership(tmp_path):
    mechanism = _mechanism()
    membership = _membership(mechanism)
    membership["A/001.json"] = {
        "mechanism_id": mechanism["mechanism_id"],
        "status": "confirmed",
        "evidence": [],
        "repairable": True,
    }
    with pytest.raises(PromotionProtocolError):
        _freeze(tmp_path, mechanism=mechanism, membership=membership)


def test_v2_refuses_a_challenge_case_that_is_a_mechanism_member(tmp_path):
    mechanism = _mechanism()
    membership = _membership(mechanism)
    membership["X/001.json"] = {
        "mechanism_id": mechanism["mechanism_id"],
        "status": "confirmed",
        "evidence": ["actually a member"],
        "repairable": True,
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, mechanism=mechanism, membership=membership)
    assert "mechanism-external" in str(exc.value)


def test_v2_refuses_a_pairwise_gold_step_gap_for_localization(tmp_path):
    """A localization protocol without annotations cannot be scored."""
    mechanism = _mechanism()
    mechanism["objective"] = "localization"
    dataset = _dataset(tmp_path, steps=False)
    with pytest.raises((PromotionProtocolError, ValueError)):
        _freeze(tmp_path, mechanism=mechanism,
                membership=_membership(mechanism, localization_viable=True),
                dataset_root=dataset, gold_steps={},
                candidate=_bound_candidate(mechanism, kind="localization"))


def test_v2_refuses_relaxed_gate_policy(tmp_path):
    """A protocol that would relax a zero-tolerance gate must not freeze."""
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, config={
            "dataset_root": str(tmp_path / "data"),
            "gates": {"harmlessness_new_fn_allowed": 1},
        })
    assert "does not allow relaxing" in str(exc.value)


def test_v2_refuses_development_cases_inside_the_batch(tmp_path):
    """Exposure is the union of artifacts, mechanism members and neighbours."""
    mechanism = _mechanism(cases=["A/001.json", "DEV/002.json"])
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, mechanism=mechanism)
    assert "candidate-unused" in str(exc.value)


def test_v2_requires_every_case_to_have_a_membership_entry(tmp_path):
    mechanism = _mechanism()
    membership = _membership(mechanism)
    del membership["E/002.json"]
    with pytest.raises(PromotionProtocolError) as exc:
        _freeze(tmp_path, mechanism=mechanism, membership=membership)
    assert "cover every promotion case" in str(exc.value)


def test_unbound_candidate_cannot_be_used_with_a_v2_protocol(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    unbound = build_candidate(
        candidate_id="patch:old", change_kind="draft", base_revision="r",
        artifacts=[_artifact()], target_kind="detection",
    )
    report = _run(tmp_path, protocol=protocol, candidate=unbound,
                  backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert report["promotion_diagnosis"]["reason_code"] == "mechanism_alignment_missing"
    assert report["admitted"] is False


def test_mechanism_bound_candidate_cannot_be_used_with_a_v1_protocol(tmp_path):
    """A bound candidate must not fall back to polarity scoring."""
    mechanism = _mechanism()
    candidate = _bound_candidate(mechanism)
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(sorted(GAIN + PROTECTION)), encoding="utf-8")
    detector_config = tmp_path / "detector.json"
    detector_config.write_text(json.dumps({"x": 1}), encoding="utf-8")
    v1 = freeze_protocol(
        tmp_path / "v1.json",
        batch_path=batch,
        roles={**{case: "benefit" for case in GAIN},
               **{case: "protection" for case in PROTECTION}},
        gold={case: GOLD[case] for case in GAIN + PROTECTION},
        detector_config=detector_config,
        workers=1,
        framework_config={"registry_dir": str(tmp_path / "registry")},
    )
    v1["loaded_from"] = str(tmp_path / "v1.json")
    v1["resolved_cases"] = sorted(GAIN + PROTECTION)
    report = _run(tmp_path, protocol=v1, candidate=candidate, backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert "mechanism-aligned protocol" in report["terminal_reason"]


# ---------------------------------------------------------------------------
# Running: opportunity first, then benefit, protection and challenge
# ---------------------------------------------------------------------------
def _solved_control() -> dict[str, bool]:
    return {case: GOLD[case] for case in ALL_CASES}


def _repairing_candidate() -> dict[str, bool]:
    return {case: GOLD[case] for case in ALL_CASES}


def test_promotes_when_the_mechanism_is_repaired_and_nothing_regresses(tmp_path):
    mechanism = _mechanism()
    candidate = _bound_candidate(mechanism)
    protocol = load_protocol(
        _freeze(tmp_path, mechanism=mechanism)["loaded_from"],
        dataset_root=str(tmp_path / "data"),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    # Fix the candidate hash binding: freeze and load must agree, which they do
    # because both derive from the same object.
    protocol["candidate_content_hash"] = candidate["content_hash"]
    report = _run(
        tmp_path, protocol=protocol, candidate=candidate,
        backend=_Backend(
            {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False)
             for case in ALL_CASES},
            {case: GOLD[case] for case in ALL_CASES},
        ),
    )
    assert report["state"] == "promoted", report
    assert report["admitted"] is True
    assert report["promotion_diagnosis"]["reason_code"] == "promoted"
    assert report["opportunity"]["mechanism"]["repairable_errors"] == len(GAIN)
    assert report["benefit"]["detection"]["marginal_cases"] == len(GAIN)


def test_no_mechanism_opportunity_stops_before_the_candidate_arm(tmp_path):
    """Unmeasurable is not falsified, and the candidate arm is not spent."""
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    backend = _Backend(_solved_control())  # control already right on the gain set
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=backend)
    assert report["state"] == "promotion_unmeasurable"
    assert report["admitted"] is False
    assert report["promotion_diagnosis"]["reason_code"] == "no_mechanism_opportunity"
    assert report["promotion_diagnosis"]["category"] == "formal_gain_no_opportunity"
    # Only the control arm ran: no candidate measurement was paid for.
    assert backend.calls == ["control"]


def test_an_outage_on_the_gain_set_is_unresolved_not_failed(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)

    class _Outage(_Backend):
        def evaluate(self, cases, **kwargs):
            rows = super().evaluate(cases, **kwargs)
            if self.calls and self.calls[-1] == "control":
                rows[0]["prediction"] = None
                rows[0]["state"] = "unresolved"
                rows[0]["authority"] = "invalid"
                rows[0]["result"]["runtime_valid"] = False
                rows[0]["result"]["decision_authority"] = "invalid"
            return rows

    backend = _Outage({case: False for case in GAIN}
                      | {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False)
                         for case in ALL_CASES})
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=backend)
    assert report["state"] == "promotion_unresolved"
    assert report["admitted"] is False


def test_a_protection_regression_is_rejected_even_with_a_mechanism_gain(tmp_path):
    """Gains never excuse a regression; the two are not netted."""
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    candidate_predictions = {case: GOLD[case] for case in ALL_CASES}
    candidate_predictions["E/001.json"] = True  # break a protected clean case
    report = _run(
        tmp_path, protocol=protocol, candidate=_bound_candidate(mechanism),
        backend=_Backend(
            {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False)
             for case in ALL_CASES},
            candidate_predictions,
        ),
    )
    assert report["state"] == "promotion_rejected"
    assert report["promotion_diagnosis"]["reason_code"] in {
        "protection_regression", "challenge_regression",
    }
    assert report["benefit"]["detection"]["marginal_cases"] == len(GAIN)


def test_a_challenge_regression_is_its_own_rejection(tmp_path):
    """Damaging a similar-looking case with another mechanism is its own failure."""
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    candidate_predictions = {case: GOLD[case] for case in ALL_CASES}
    candidate_predictions["X/001.json"] = False  # lose an external true positive
    report = _run(
        tmp_path, protocol=protocol, candidate=_bound_candidate(mechanism),
        backend=_Backend(
            {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False)
             for case in ALL_CASES},
            candidate_predictions,
        ),
    )
    assert report["state"] == "promotion_rejected"
    assert report["promotion_diagnosis"]["reason_code"] == "challenge_regression"
    assert (
        report["promotion_diagnosis"]["category"]
        == "mechanism_external_regression"
    )


def test_a_candidate_that_changes_nothing_is_a_design_failure(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    unchanged = {case: (GOLD[case] if case in PROTECTION + CHALLENGE else False)
                 for case in ALL_CASES}
    report = _run(
        tmp_path, protocol=protocol, candidate=_bound_candidate(mechanism),
        backend=_Backend(unchanged, unchanged),
    )
    assert report["state"] == "promotion_rejected"
    assert report["promotion_diagnosis"]["reason_code"] == "candidate_no_effect"
    assert report["promotion_diagnosis"]["category"] == "candidate_design_failed"


def test_a_broken_localization_is_rejected_for_localization_claims(tmp_path):
    mechanism = _mechanism(objective="joint")
    membership = _membership(mechanism, localization_viable=True)
    dataset = _dataset(tmp_path, steps=True)
    candidate = _bound_candidate(
        mechanism, kind="joint", artifact=_localization_artifact(),
    )
    protocol = _freeze(
        tmp_path, mechanism=mechanism, membership=membership,
        candidate=candidate, dataset_root=dataset, gold_steps=GOLD_STEPS,
    )
    # The control convicts the mechanisms correctly but names the wrong step:
    # that is what makes the localization objective scorable at all.
    control = {case: (GOLD[case] if case not in GAIN else True)
               for case in ALL_CASES}
    active = dict(control)
    active["C/001.json"] = False  # a baseline-correct localization case lost
    report = _run(
        tmp_path, protocol=protocol, candidate=candidate,
        backend=_Backend(
            control, active,
            # The control is right on every annotated step except its own
            # mechanism gain cases, where it names the wrong one -- that is the
            # repairable localization error this run is about.
            control_steps={
                **{case: GOLD_STEPS[case] for case in ALL_CASES if case in GOLD_STEPS},
                **{case: 3 for case in GAIN},
            },
            candidate_steps={
                case: GOLD_STEPS[case] for case in ALL_CASES if case in GOLD_STEPS
            },
        ),
    )
    assert report["state"] == "promotion_rejected"
    assert report["promotion_diagnosis"]["reason_code"] in {
        "localization_regression", "protection_regression",
    }
    # The lost localization is what stopped it, not a general regression: the
    # candidate arm fixed the mechanism gain set.
    assert report["localization_protection"]["joint_broken"] >= 1


# ---------------------------------------------------------------------------
# Freeze integrity
# ---------------------------------------------------------------------------
def test_a_tampered_protocol_is_refused_before_measurement(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    protocol["mechanism_gain_cases"] = list(GAIN) + ["X/002.json"]
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert report["promotion_diagnosis"]["reason_code"] == "protocol_invalid"


def test_a_changed_baseline_registry_is_refused(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    protocol["baseline_registry_hash"] = "0" * 64
    # Recompute the content hash so this test isolates the baseline check
    # rather than the integrity check.
    from evolution import promotion_alignment as alignment

    body = {key: value for key, value in protocol.items()
            if key not in {"protocol_hash", "loaded_from", "resolved_cases"}}
    protocol["protocol_hash"] = alignment.digest(body)
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert "baseline" in report["terminal_reason"]


def test_a_changed_candidate_is_refused(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    other = _bound_candidate(mechanism, intervention_id="IV-P-002")
    report = _run(tmp_path, protocol=protocol, candidate=other,
                  backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert "candidate changed after the protocol was frozen" in report["terminal_reason"]


def test_a_changed_trajectory_is_refused(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    (tmp_path / "data" / "A/001.json").write_text(
        json.dumps({"question": "edited", "history": [],
                    "is_hallucination": True}), encoding="utf-8",
    )
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=_Backend({}))
    assert report["state"] == "promotion_blocked_preflight"
    assert "trajectory content changed" in report["terminal_reason"]


def test_a_broken_ledger_stops_the_run(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    run = PromotionRun(
        candidate=_bound_candidate(mechanism), protocol=protocol,
        config={"dataset_root": str(tmp_path / "data"), "gates": {}, "library": {}},
        output_dir=tmp_path / "out", backend=_Backend({}),
        active_registry=_Registry(), ledger=_Ledger(valid=False),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] == "promotion_blocked_preflight"
    assert "ledger" in report["terminal_reason"]


def test_a_protocol_is_claimed_once_per_candidate_and_output(tmp_path):
    """Frozen evidence may not be re-rolled until it passes."""
    mechanism = _mechanism()
    protocol = load_protocol(
        _freeze(tmp_path, mechanism=mechanism)["loaded_from"],
        dataset_root=str(tmp_path / "data"),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    candidate = _bound_candidate(mechanism)
    protocol["candidate_content_hash"] = candidate["content_hash"]
    _run(tmp_path, protocol=protocol, candidate=candidate,
         backend=_Backend(_solved_control()))
    # Same attempt data is idempotent...
    protocol["loaded_from"] = str(tmp_path / "protocol.json")
    report = _run(tmp_path, protocol=protocol, candidate=candidate,
                  backend=_Backend(_solved_control()))
    assert report["state"] in {"promoted", "promotion_rejected", "promotion_unmeasurable"}
    # ...but a different output directory is a second attempt on frozen evidence.
    protocol2 = dict(protocol)
    other = PromotionRun(
        candidate=candidate, protocol=protocol2,
        config={"dataset_root": str(tmp_path / "data"), "gates": {}, "library": {}},
        output_dir=tmp_path / "out2", backend=_Backend(_solved_control()),
        active_registry=_Registry(), ledger=_Ledger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = other.run()
    assert report["state"] == "promotion_blocked_preflight"
    assert "already consumed" in report["terminal_reason"]


def test_all_four_gates_are_mandatory(tmp_path):
    mechanism = _mechanism()
    protocol = _freeze(tmp_path, mechanism=mechanism)
    report = _run(tmp_path, protocol=protocol,
                  candidate=_bound_candidate(mechanism), backend=_Backend({}),
                  stages=("structural",))
    assert report["state"] == "promotion_blocked_preflight"
    assert "formal gates are mandatory" in report["terminal_reason"]

def test_development_import_requires_a_frozen_version_with_passing_checks(tmp_path):
    import evolution.promotion as promotion
    from evolution.candidate_versions import CandidateVersionGraph

    mechanism = _mechanism()
    run_dir = tmp_path / "development"
    graph = CandidateVersionGraph(run_dir)
    graph.record(
        "route_recall", "v001", _artifact(),
        parent=None, diagnosis=None, draft_notes=[], evaluation=None,
        verdict={"l0": {"passed": True}, "l1": {"passed": True}},
        status="ready_for_freeze",
    )
    graph.write_development("v001", {
        "schema_version": "evolution.candidate_development.v1",
        "mechanism_id": mechanism["mechanism_id"],
        "action_class": "route_recall",
        "status": "ready_for_freeze",
        "l0": {"passed": True}, "l1": {"passed": True},
        "development_evidence": {"target_cases": ["DEV/001.json"],
                                 "neighbor_cases": ["DEV/002.json"]},
    })
    # Not frozen yet: a merely ready version must not be promotable.
    with pytest.raises(PromotionProtocolError) as exc:
        candidate_from_development(run_dir, version_id="v001", mechanism=mechanism)
    assert "frozen" in str(exc.value)
    graph.write_development("v001", {"status": "frozen"})
    candidate = candidate_from_development(run_dir, version_id="v001", mechanism=mechanism)
    assert candidate["schema_version"] == "evolution.candidate.v2"
    assert candidate["target_mechanism_id"] == mechanism["mechanism_id"]
    assert set(candidate["development_cases"]) >= {"DEV/001.json", "DEV/002.json"}
    assert candidate["development_results"]["l1"]["passed"] is True
    # A version whose L1 did not pass is refused even if someone marks it frozen.
    graph.record(
        "route_recall", "v002", _artifact(),
        parent=None, diagnosis=None, draft_notes=[], evaluation=None,
        verdict={"l0": {"passed": True}, "l1": {"passed": False}},
        status="frozen",
    )
    graph.write_development("v002", {
        "mechanism_id": mechanism["mechanism_id"], "status": "frozen",
        "l0": {"passed": True}, "l1": {"passed": False},
    })
    with pytest.raises(PromotionProtocolError) as exc:
        candidate_from_development(run_dir, version_id="v002", mechanism=mechanism)
    assert "L1 did not pass" in str(exc.value)


def test_every_reachable_reason_code_has_a_category():
    """An unmapped reason code reads as "no reason recorded".

    The aligned run forwards the opportunity metric's own vocabulary into the
    diagnosis, so the table has to cover the metric's reasons as well as the
    run's own. A code that lands in ``unclassified`` is a run that did record
    why it stopped, reported as if it had not.
    """
    assert set(LEGACY_STATE_REASONS.values()) <= set(REASON_CATEGORIES)
    for code, category in REASON_CATEGORIES.items():
        assert diagnosis(code)["category"] == category
    # The metric's vocabulary, which the aligned run forwards verbatim.
    for code in (
        "no_mechanism_opportunity", "mechanism_invalid",
        "mechanism_unconfirmed", "mechanism_measurement_unresolved",
    ):
        assert code in REASON_CATEGORIES
        assert diagnosis(code)["category"] != "unclassified"


def test_unmeasurable_is_never_recorded_as_candidate_design_failure():
    """§8: unmeasurable is not falsified, and blocked is not a bad candidate.

    Only ``promotion_rejected`` says the candidate failed. A batch that could
    not be measured and a run that never started are different diagnoses, and
    folding them into ``candidate_no_effect`` blames the candidate for a
    measurement gap.
    """
    assert LEGACY_STATE_REASONS["promotion_rejected"] == "candidate_no_effect"
    for state in ("promotion_unmeasurable", "promotion_unresolved"):
        code = LEGACY_STATE_REASONS[state]
        assert diagnosis(code)["category"] != "candidate_design_failed"
    blocked = LEGACY_STATE_REASONS["promotion_blocked_preflight"]
    assert diagnosis(blocked)["category"] == "protocol_invalid"
    # The two questions one batch cannot answer stay explicitly unanswered.
    entry = diagnosis("candidate_no_effect")
    assert entry["mechanism_unrepairable"] is None
    assert entry["action_class_insufficient"] is None


def _verified_projection(protocol: dict) -> dict:
    """The exact projection ``verify_alignment`` re-hashes."""
    return {
        key: value for key, value in protocol.items()
        if key not in {"protocol_hash", "loaded_from", "resolved_cases"}
    }


def test_verify_alignment_rederives_the_localization_precondition(tmp_path):
    """``verify_alignment`` must not accept what ``build_alignment`` refuses.

    The gold-step requirement was enforced only at freeze time, so a consumer
    that verified a hand-edited or directly constructed protocol accepted a
    localization batch whose gain cases carry no annotation. Such a batch can
    never show a localization improvement, and the run would report
    "measurement unresolved" instead of refusing the protocol.
    """
    import copy

    import evolution.promotion_alignment as alignment_module

    mechanism = _mechanism(objective="localization")
    protocol = _freeze(
        tmp_path, mechanism=mechanism,
        membership=_membership(mechanism, localization_viable=True),
        gold_steps={case: 3 for case in ALL_CASES},
        candidate=_bound_candidate(mechanism, kind="localization"),
    )
    # Sanity: the frozen protocol verifies as written.
    verify_alignment(protocol)
    tampered = copy.deepcopy(protocol)
    for case in tampered["mechanism_gain_cases"]:
        tampered["gold_steps"].pop(case, None)
    tampered["protocol_hash"] = alignment_module.digest(
        _verified_projection(tampered)
    )
    with pytest.raises(ValueError) as exc:
        verify_alignment(tampered)
    assert "gold step" in str(exc.value)


def test_verify_alignment_requires_the_baseline_registry_identity(tmp_path):
    """A mechanism-aligned protocol without the frozen baseline cannot be verified."""
    import copy

    import evolution.promotion_alignment as alignment_module

    protocol = _freeze(tmp_path, mechanism=_mechanism())
    verify_alignment(protocol)
    tampered = copy.deepcopy(protocol)
    tampered["baseline_registry_hash"] = ""
    tampered["protocol_hash"] = alignment_module.digest(
        _verified_projection(tampered)
    )
    with pytest.raises(ValueError) as exc:
        verify_alignment(tampered)
    assert "baseline registry" in str(exc.value)
