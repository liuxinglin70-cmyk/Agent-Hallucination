"""Promotion bridge: candidate object, frozen protocol and gate reuse.

Every test here is offline: the promotion batch, the candidate and the arms are
deterministic. The point is to lock the invariants the optimization plan states
in prose, so that a later change cannot quietly weaken them:

- a research PASS never admits on its own;
- a batch overlapping the development cases is refused;
- an unmeasurable objective ends ``promotion_unmeasurable`` with deployment
  unchanged, not as a rejection;
- the registry is written only for a ``promoted`` result.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolution.promotion import (
    PromotionProtocolError,
    PromotionRun,
    build_candidate,
    candidate_content_hash,
    candidate_from_registry_patch,
    candidate_from_research,
    freeze_protocol,
    load_protocol,
    promote,
)
from evolution.contracts import make_intervention


def _candidate(
    intervention_id: str = "IV-P-001",
    *,
    target_kind: str = "detection",
    directive: str = "Prefer the annotated responsible step.",
    failure_cases: list[str] | None = None,
) -> dict:
    entry = make_intervention(
        intervention_id=intervention_id,
        intervention_type="text_boundary",
        hook="judge.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": directive,
            "not_when": "Never apply when the delivered result already matches.",
        },
        expected_behavior="the judge convicts the deviating delivery",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-dev",
        failure_cases=failure_cases or ["DEV/001.json", "DEV/002.json"],
    )
    if target_kind != "detection":
        entry["claim"]["target_kind"] = target_kind
        # The declaration is part of the executable identity, so the hash has
        # to be recomputed rather than left over from before the mutation.
        from detector.interventions import intervention_content_hash

        entry["content_hash"] = intervention_content_hash(entry)
    return build_candidate(
        candidate_id="research:run:v001",
        change_kind="draft",
        base_revision="deadbeef",
        artifacts=[entry],
        development={"source": "research", "screening_passed": True},
        target_kind=target_kind,
    )


def _protocol(tmp_path: Path, *, roles: dict[str, str], gold_steps: dict | None = None,
              development: list[str] | None = None,
              gold: dict[str, bool] | None = None) -> dict:
    """Freeze a protocol without touching the real dataset or detector config.

    ``gold`` defaults to a mixed batch: benefit cases are gold-positive (that
    is what a repair opportunity needs) and protection cases are gold-negative
    except where a test overrides it, so the HARMLESSNESS gate has both a
    baseline-correct TP and TN to protect.
    """
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(sorted(roles)), encoding="utf-8")
    detector_config = tmp_path / "detector.json"
    detector_config.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    resolved_gold = dict(gold) if gold else {
        case: (role == "benefit") for case, role in roles.items()
    }
    protocol = freeze_protocol(
        tmp_path / "protocol.json",
        batch_path=batch,
        roles=roles,
        gold=resolved_gold,
        gold_steps=gold_steps or {},
        development_cases=development or [],
        detector_config=detector_config,
        workers=2,
        timeout_seconds=600,
        reason="test",
    )
    # load_protocol re-derives labels from the dataset; the offline harness
    # supplies them directly so the batch never touches the raw corpus.
    protocol["resolved_cases"] = sorted(roles)
    protocol["gold"] = resolved_gold
    protocol["gold_steps"] = dict(gold_steps or {})
    protocol["loaded_from"] = str(tmp_path / "protocol.json")
    return protocol


class _ScriptedBackend:
    """Deterministic stand-in: the candidate convicts the benefit positives."""

    name = "scripted"

    def __init__(self, *, wrong_steps: dict[str, int] | None = None,
                 fixed_steps: dict[str, int] | None = None,
                 control_prediction: dict[str, bool] | None = None,
                 detection_delta: bool = True) -> None:
        self.wrong_steps = dict(wrong_steps or {})
        self.fixed_steps = dict(fixed_steps or {})
        self.control_prediction = dict(control_prediction or {})
        self.detection_delta = detection_delta

    def evaluate_fresh(self, cases, *, dataset_root, registry=None, gold_map=None,
                       gold_step_map=None, workers=1, deadline=None):
        from evolution.backends import fingerprint

        entries = list((registry or {}).get("interventions") or ())
        active = bool(entries)
        rows = []
        for case in cases:
            gold = bool((gold_map or {}).get(case))
            if active:
                if self.detection_delta:
                    prediction = gold
                else:
                    prediction = self.control_prediction.get(case, gold)
                step = self.fixed_steps.get(case, self.wrong_steps.get(case))
                if prediction is True and step is None:
                    step = (gold_step_map or {}).get(case)
            else:
                prediction = self.control_prediction.get(case, gold)
                step = self.wrong_steps.get(case)
                if prediction is True and step is None:
                    step = (gold_step_map or {}).get(case)
            rows.append({
                "case": case,
                "gold": gold,
                "gold_step": (gold_step_map or {}).get(case),
                "prediction": prediction,
                "state": (
                    "verified_error" if prediction
                    else ("verified_clean" if prediction is False else "unresolved")
                ),
                "authority": "authoritative" if prediction is not None else "invalid",
                "responsible_step": step,
                "result": {
                    "case_id": case,
                    "state": "verified_error" if prediction else "verified_clean",
                    "decision_authority": (
                        "authoritative" if prediction is not None else "invalid"
                    ),
                    "runtime_valid": True,
                    "responsible_step": step,
                    "proof": {"deployment": {"fired": [
                        {"intervention_id": str(e.get("intervention_id")),
                         "event": "text_injected"}
                        for e in entries
                    ]}},
                    "stage_status": {},
                },
                "backend": self.name,
                "registry_hash": fingerprint(registry),
            })
        return rows


def _run(tmp_path: Path, *, candidate: dict, protocol: dict, backend) -> dict:
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    return run.run()


class _FakeRegistry:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.writes: list[list[dict]] = []
        self.state = {"cycle": 0}

    def check_limits(self, entries, *, library_cfg=None):
        return []

    def write(self, entries, *, cycle, reason, library_cfg=None):
        from evolution.contracts import make_registry

        self.entries = [dict(entry) for entry in entries]
        self.writes.append(self.entries)
        return make_registry(self.entries)


class _FakeLedger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event, payload):
        self.events.append((str(event), dict(payload)))
        return {"event": event, "payload": payload}


# ---------------------------------------------------------------------------
def test_candidate_object_is_content_identified():
    first = _candidate()
    second = _candidate()
    assert first["content_hash"] == second["content_hash"]
    # A lifecycle transition must not change the identity a version is
    # attributed to.
    second["status"] = "active"
    assert candidate_content_hash(second) == candidate_content_hash(first)
    changed = _candidate(directive="A materially different instruction.")
    assert changed["content_hash"] != first["content_hash"]


def test_candidate_rejects_case_id_leak_free_but_requires_artifacts(tmp_path):
    with pytest.raises(ValueError):
        build_candidate(
            candidate_id="x", change_kind="draft", base_revision="r",
            artifacts=[],
        )


BENEFIT = ["A/001.json", "B/001.json"]
# The HARMLESSNESS gate protects baseline-correct cases of both polarities, so
# a promotion batch that could ever admit needs both in the protection set.
PROTECTION_POS = ["C/001.json", "D/001.json"]
PROTECTION_NEG = ["E/001.json", "F/001.json"]


def _standard_roles() -> dict[str, str]:
    return {
        **{case: "benefit" for case in BENEFIT},
        **{case: "protection" for case in PROTECTION_POS + PROTECTION_NEG},
    }


def _standard_protocol(tmp_path: Path, *, gold_steps: dict | None = None) -> dict:
    roles = _standard_roles()
    gold = {
        **{case: True for case in BENEFIT + PROTECTION_POS},
        **{case: False for case in PROTECTION_NEG},
    }
    return _protocol(tmp_path, roles=roles, gold_steps=gold_steps, gold=gold)


def test_freeze_protocol_refuses_development_overlap(tmp_path):
    roles = {
        "DEV/001.json": "benefit", "DEV/002.json": "benefit",
        "CLEAN/001.json": "protection", "CLEAN/002.json": "protection",
        "CLEAN/003.json": "protection", "CLEAN/004.json": "protection",
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _protocol(
            tmp_path, roles=roles, development=["DEV/001.json"],
            gold={
                "DEV/001.json": True, "DEV/002.json": True,
                "CLEAN/001.json": True, "CLEAN/002.json": True,
                "CLEAN/003.json": False, "CLEAN/004.json": False,
            },
        )
    assert "candidate-unused" in str(exc.value)


def test_freeze_protocol_refuses_rewrite(tmp_path):
    roles = {
        "A/001.json": "benefit", "B/001.json": "benefit",
        "C/001.json": "protection", "D/001.json": "protection",
        "E/001.json": "protection", "F/001.json": "protection",
    }
    gold = {
        "A/001.json": True, "B/001.json": True,
        "C/001.json": True, "D/001.json": True,
        "E/001.json": False, "F/001.json": False,
    }
    _protocol(tmp_path, roles=roles, gold=gold)
    with pytest.raises(PromotionProtocolError) as exc:
        _protocol(tmp_path, roles=roles, gold=gold)
    assert "already frozen" in str(exc.value)


def test_freeze_protocol_needs_a_benefit_case(tmp_path):
    with pytest.raises(PromotionProtocolError):
        _protocol(tmp_path, roles={"A/001.json": "protection"})


def test_freeze_protocol_rejects_unknown_role(tmp_path):
    with pytest.raises(PromotionProtocolError):
        _protocol(tmp_path, roles={"A/001.json": "training"})


def test_freeze_protocol_refuses_a_protection_set_of_one_polarity(tmp_path):
    """A batch that provably cannot satisfy the coverage floor is refused early.

    The first real promotion run hit exactly this: its protection cases were
    all gold-negative, so protected_tp could never reach the floor and the
    whole batch was unevaluable by construction. Spending provider budget to
    discover that is what this freeze-time check prevents.
    """
    roles = {
        "A/001.json": "benefit", "B/001.json": "benefit",
        "C/001.json": "protection", "D/001.json": "protection",
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _protocol(
            tmp_path, roles=roles,
            gold={
                "A/001.json": True, "B/001.json": True,
                "C/001.json": False, "D/001.json": False,
            },
        )
    assert "coverage floor" in str(exc.value)
    assert "gold-true" in str(exc.value)


def test_freeze_protocol_refuses_a_protection_set_without_negatives(tmp_path):
    roles = {
        "A/001.json": "benefit", "B/001.json": "benefit",
        "C/001.json": "protection", "D/001.json": "protection",
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _protocol(
            tmp_path, roles=roles,
            gold={case: True for case in roles},
        )
    assert "coverage floor" in str(exc.value)


def test_freeze_protocol_refuses_missing_gold(tmp_path):
    roles = {
        "A/001.json": "benefit", "B/001.json": "benefit",
        "C/001.json": "protection", "D/001.json": "protection",
    }
    with pytest.raises(PromotionProtocolError) as exc:
        _protocol(
            tmp_path, roles=roles,
            gold={
                "A/001.json": True, "B/001.json": True,
                "C/001.json": True, "D/001.json": None,
            },
        )
    assert "boolean gold" in str(exc.value)


def test_load_protocol_rejects_a_changed_batch(tmp_path, monkeypatch):
    roles = {
        "A/001.json": "benefit", "B/001.json": "benefit",
        "C/001.json": "protection", "D/001.json": "protection",
        "E/001.json": "protection", "F/001.json": "protection",
    }
    _protocol(
        tmp_path, roles=roles,
        gold={
            "A/001.json": True, "B/001.json": True,
            "C/001.json": True, "D/001.json": True,
            "E/001.json": False, "F/001.json": False,
        },
    )
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps([
        "A/001.json", "B/001.json", "C/001.json", "D/001.json",
        "E/001.json", "F/001.json", "G/001.json",
    ]), encoding="utf-8")
    monkeypatch.setattr(
        "evolution.promotion.source_digest", lambda: _frozen_source(tmp_path),
    )
    protocol = json.loads((tmp_path / "protocol.json").read_text(encoding="utf-8-sig"))
    with pytest.raises(PromotionProtocolError) as exc:
        load_protocol(
            tmp_path / "protocol.json",
            dataset_root=tmp_path,
            stages=("structural",),
        )
    assert "changed after it was frozen" in str(exc.value) or "roles" in str(exc.value)


def _frozen_source(tmp_path: Path) -> str:
    protocol = json.loads((tmp_path / "protocol.json").read_text(encoding="utf-8-sig"))
    return str(protocol.get("code_baseline") or "")


def test_structural_rejects_a_candidate_developed_on_the_batch(tmp_path):
    """A batch overlapping the development cases must not even be measured."""
    protocol = _protocol(
        tmp_path,
        roles={
            "DEV/001.json": "benefit", "DEV/002.json": "benefit",
            "C/001.json": "protection", "D/001.json": "protection",
            "E/001.json": "protection", "F/001.json": "protection",
        },
        gold={
            "DEV/001.json": True, "DEV/002.json": True,
            "C/001.json": True, "D/001.json": True,
            "E/001.json": False, "F/001.json": False,
        },
    )
    candidate = _candidate()
    report = _run(
        tmp_path, candidate=candidate, protocol=protocol,
        backend=_ScriptedBackend(),
    )
    assert report["state"] == "promotion_rejected"
    assert "candidate-unused" in report["terminal_reason"]
    assert "m" not in report["gates"] or report["gates"]["m"].get("passed") is not True


def test_development_failure_cannot_be_promoted(tmp_path):
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()
    candidate["development_results"] = {
        "source": "research", "screening_passed": False,
    }
    report = _run(
        tmp_path, candidate=candidate, protocol=protocol,
        backend=_ScriptedBackend(),
    )
    assert report["state"] == "promotion_rejected"
    assert "screening verdict did not pass" in report["terminal_reason"]


def test_promotion_needs_a_measurement_before_admission(tmp_path):
    """Control already correct everywhere: nothing to repair, so unmeasurable.

    This is the property that keeps a research PASS from admitting by itself.
    """
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()
    backend = _ScriptedBackend(
        control_prediction={"A/001.json": True, "B/001.json": True,
                            "C/001.json": True, "D/001.json": True},
        detection_delta=False,
    )
    report = _run(
        tmp_path, candidate=candidate, protocol=protocol, backend=backend,
    )
    assert report["state"] == "promotion_unmeasurable"
    assert report["admitted"] is False
    assert "new_errors" not in report["terminal_reason"]


def test_detection_candidate_promotes_and_writes_once(tmp_path):
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()
    control = {"A/001.json": False, "B/001.json": False}
    backend = _ScriptedBackend(control_prediction=control)
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] == "promoted", report.get("terminal_reason")
    assert run.registry.writes, "an admitted candidate must be committed"
    assert len(run.registry.writes) == 1
    assert report["version"]["artifacts"] == ["IV-P-001"]
    events = [event for event, _ in run.ledger.events]
    assert "candidate_promoted" in events
    assert "promotion_attempt" in events


def test_detection_candidate_rejected_leaves_registry_untouched(tmp_path):
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()
    # The candidate fixes nothing on the benefit set: no marginal gain.
    backend = _ScriptedBackend(
        control_prediction={"A/001.json": False, "B/001.json": False},
        detection_delta=False,
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] in {"promotion_rejected", "promotion_unmeasurable"}
    assert run.registry.writes == []


def test_localization_candidate_promotes_on_joint_delta(tmp_path):
    """A localization-only candidate is scored by the joint rule, not M."""
    protocol = _standard_protocol(tmp_path, gold_steps={"A/001.json": 6, "B/001.json": 14})
    candidate = _candidate(target_kind="localization")
    # Control convicts both positives but names the wrong step; the candidate
    # convicts the same two and names the annotated step. Detection unchanged.
    backend = _ScriptedBackend(
        wrong_steps={"A/001.json": 3, "B/001.json": 9},
        fixed_steps={"A/001.json": 6, "B/001.json": 14},
        detection_delta=False,
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] == "promoted", report.get("terminal_reason")
    assert report["target_kind"] == "localization"
    assert report["benefit"]["localization"]["joint_delta"] == 2
    assert run.registry.writes and len(run.registry.writes) == 1


def test_localization_candidate_that_only_adds_detections_is_rejected(tmp_path):
    """A binary-only gain must not satisfy a localization claim.

    The batch does carry localization opportunity (the control convicts both
    positives but names the wrong step). The candidate adds no localization:
    it keeps the wrong steps, so the joint delta stays flat and the claim is
    falsified on this batch rather than left unmeasured.
    """
    protocol = _standard_protocol(
        tmp_path, gold_steps={"A/001.json": 6, "B/001.json": 14},
    )
    candidate = _candidate(target_kind="localization")
    backend = _ScriptedBackend(
        wrong_steps={"A/001.json": 3, "B/001.json": 9},
        detection_delta=False,
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] == "promotion_rejected"
    assert report["opportunity"]["localization"]["improvable"] == 2
    assert report["benefit"]["localization"]["joint_delta"] == 0
    assert run.registry.writes == []


def test_localization_candidate_without_opportunity_is_unmeasurable(tmp_path):
    """No repairable localization in the batch: unmeasured, not falsified."""
    protocol = _standard_protocol(
        tmp_path, gold_steps={"A/001.json": 6, "B/001.json": 14},
    )
    candidate = _candidate(target_kind="localization")
    # The control already localizes both positives correctly: nothing to fix.
    backend = _ScriptedBackend(detection_delta=False)
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] == "promotion_unmeasurable"
    assert report["opportunity"]["localization"]["improvable"] == 0
    assert run.registry.writes == []


def test_localization_preservation_is_charged(tmp_path):
    """Breaking a correct localization must not be netted out by new gains."""
    protocol = _standard_protocol(tmp_path, gold_steps={"A/001.json": 6, "B/001.json": 14})
    candidate = _candidate(target_kind="localization")

    class _Break(_ScriptedBackend):
        def evaluate_fresh(self, cases, **kwargs):
            rows = super().evaluate_fresh(cases, **kwargs)
            entries = list((kwargs.get("registry") or {}).get("interventions") or ())
            if entries:
                for row in rows:
                    if row["case"] == "A/001.json":
                        row["responsible_step"] = 99
                        row["result"]["responsible_step"] = 99
            return rows

    backend = _Break(
        fixed_steps={"A/001.json": 6, "B/001.json": 14},
        detection_delta=False,
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    report = run.run()
    assert report["state"] in {"promotion_rejected", "promotion_unmeasurable"}
    assert report["benefit"]["localization"]["joint_broken"] == 1
    assert run.registry.writes == []


def test_promote_entry_point_records_a_protocol_error(tmp_path):
    with pytest.raises(PromotionProtocolError):
        promote(
            candidate=_candidate(),
            protocol_path=tmp_path / "missing.json",
            output_dir=tmp_path / "out",
            config={"dataset_root": str(tmp_path)},
        )


def test_candidate_from_registry_patch(tmp_path):
    entry = _candidate()["artifacts"][0]
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    candidate = candidate_from_registry_patch(path)
    assert candidate["artifacts"][0]["intervention_id"] == "IV-P-001"
    assert candidate["target_kind"] == "detection"


def test_candidate_from_research_reads_the_selected_version(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "v002").mkdir(parents=True)
    entry = _candidate()["artifacts"][0]
    (run_dir / "v002" / "candidate.json").write_text(json.dumps({
        "version_id": "v002", "route": "C", "kind": "revision",
        "candidate": entry, "candidate_hash": "abc",
        "verdict": {"passed": True, "reasons": [], "training": {}, "neighbors": {}},
    }), encoding="utf-8")
    (run_dir / "selection.json").write_text(json.dumps({
        "routes": {"C": {"versions": ["v002"], "selected": "v002",
                         "all_passing": ["v002"]}},
    }), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps({
        "code_baseline": "cafe1234",
    }), encoding="utf-8")
    candidate = candidate_from_research(run_dir)
    assert candidate["candidate_id"] == "research:run:v002"
    assert candidate["base_revision"] == "cafe1234"
    assert candidate["development_results"]["screening_passed"] is True
    assert candidate["change_kind"] == "revision"


def test_candidate_from_research_refuses_an_unselected_run(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "selection.json").write_text(json.dumps({
        "routes": {"C": {"versions": ["v001"], "selected": None}},
    }), encoding="utf-8")
    with pytest.raises(PromotionProtocolError):
        candidate_from_research(run_dir)


def test_completed_arm_is_resumed_not_redrawn(tmp_path):
    """An interrupted run must not re-spend budget on an arm already measured."""
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()

    class _Counting(_ScriptedBackend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        def evaluate_fresh(self, cases, **kwargs):
            self.calls += 1
            return super().evaluate_fresh(cases, **kwargs)

    backend = _Counting(
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    first = run.run()
    assert first["state"] == "promoted"
    calls_after_first = backend.calls

    # A second attempt at the same identity reuses both stored arms.
    resume_backend = _Counting(
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    resumed = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=resume_backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    second = resumed.run()
    assert second["state"] == "promoted"
    assert resume_backend.calls == 0, "a completed arm must not be re-measured"
    assert sorted(second["resumed_arms"]) == ["candidate", "control"]
    assert calls_after_first > 0


def test_changed_case_set_invalidates_a_stored_arm(tmp_path):
    """Resume must not reuse rows measured on a different input."""
    protocol = _standard_protocol(tmp_path)
    candidate = _candidate()
    forward = _ScriptedBackend(
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=forward,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    run.run()

    class _Counting(_ScriptedBackend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        def evaluate_fresh(self, cases, **kwargs):
            self.calls += 1
            return super().evaluate_fresh(cases, **kwargs)

    altered = dict(protocol)
    altered["gold_steps"] = {**protocol["gold_steps"], "A/001.json": 99}
    backend = _Counting(
        control_prediction={"A/001.json": False, "B/001.json": False},
    )
    resumed = PromotionRun(
        candidate=candidate,
        protocol=altered,
        config={"dataset_root": str(tmp_path), "gates": {}, "library": {}},
        output_dir=tmp_path / "out",
        backend=backend,
        active_registry=_FakeRegistry(),
        ledger=_FakeLedger(),
        stages=("structural", "harmlessness", "behavior", "m"),
    )
    resumed.run()
    assert backend.calls > 0, "changed gold steps must force a fresh measurement"


def test_target_kind_defaults_to_detection_on_old_entries():
    """An entry written before target_kind existed is a detection claim."""
    from evolution.gates import candidate_target_kind

    entry = _candidate()["artifacts"][0]
    assert "target_kind" not in entry["claim"]
    assert candidate_target_kind(entry) == "detection"
