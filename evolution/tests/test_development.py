"""Candidate development: lineage, cheap checks, and what freeze must refuse.

The deployment side used to be one-shot: propose, gate, reject, propose
something else. The pilot's working candidates came from the research path,
which always revised the same parent. This locks the deployment-side loop:

* a candidate that fails L0 is never measured, and its failure is machine
  readable;
* L1 requires a target to move and the neighbours to hold -- and a candidate
  that improves a target while breaking a neighbour must not be frozen, which
  the plan calls out as the first negative integration test (§15.4);
* parent-child lineage survives on disk, so a version's ancestry is
  reconstructible after the run;
* the development budget is its own, so a long loop cannot consume what the
  formal promotion protocol needs.
"""

from __future__ import annotations

import json

import pytest

from evolution.candidate_versions import CandidateVersionGraph
from evolution.contracts import make_intervention
from evolution.development import (
    CandidateDeveloper,
    DevelopmentBudget,
    check_l0,
    check_l1,
    development_report,
    plan_reaches,
)

def mechanism(
    *,
    stage: str = "recall",
    failure_mode: str = "not_reached",
    obligation: str = "required_action",
    deviation: str = "omission",
    operation: str = "route_recall",
    mechanism_id: str = "M-dev",
) -> dict:
    return {
        "schema_version": "evolution.failure_mechanism.v1",
        "mechanism_id": mechanism_id,
        "family_id": "FAM-fn-x",
        "objective": "detection",
        "task_obligation": {"kind": obligation},
        "observed_deviation": {"kind": deviation},
        "detector_failure": {"stage": stage, "failure_mode": failure_mode},
        "repair_hypothesis": {
            "operation": operation,
            "required_inputs": [],
            "required_capabilities": [],
            "forbidden_shortcuts": ["case_id"],
        },
        "member_cases": ["A/001.json", "B/002.json"],
        "status": "confirmed",
        "confidence_basis": "multi_case",
    }


def candidate(
    *,
    intervention_id: str = "IV-1-001",
    hook: str = "scanner.system",
    directive: str = "report actions required by the question that never ran",
    intervention_type: str = "text_boundary",
    body: dict | None = None,
) -> dict:
    """A valid candidate whose hook is legal for its type."""
    if body is None:
        if intervention_type == "recall_trigger":
            body = {
                "predicate": "evo_needs_action",
                "runner": "deterministic",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
                "trigger_verdicts": ["violated"],
            }
        else:
            body = {
                "directive": directive,
                "not_when": "never when every required action ran",
            }
    return make_intervention(
        intervention_id=intervention_id,
        intervention_type=intervention_type,
        hook=hook,
        packet_features=["has_tool_calls"],
        body=body,
        expected_behavior="recall gains candidates",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-x",
        failure_cases=["A/001.json", "B/002.json"],
    )


def broken_candidate() -> dict:
    """A candidate shaped wrongly enough to fail L0, but valid enough to build.

    ``make_intervention`` validates on construction, so a schema failure cannot
    be produced through it. This dict is built by hand: it is exactly the input
    a proposer could emit, which is what L0 exists to catch.
    """
    entry = candidate()
    entry.pop("content_hash", None)
    entry["hook"] = "no.such.hook"
    return entry


def fired(*intervention_ids: str) -> dict:
    """A proof fragment recording that these interventions reached the runtime."""
    return {"deployment": {"fired": [
        {"intervention_id": identifier, "event": "text_injected"}
        for identifier in intervention_ids
    ]}}


def row(
    case: str,
    *,
    arm: str,
    gold: bool,
    prediction: bool | None,
    authority: str = "authoritative",
    step: int | None = None,
    stage_states: dict | None = None,
    proof: dict | None = None,
) -> dict:
    return {
        "case": case,
        "arm": arm,
        "gold": gold,
        "prediction": prediction,
        "authority": authority,
        "responsible_step": step,
        "result": {
            "state": (
                "verified_error" if prediction else
                "verified_clean" if prediction is False else "unresolved"
            ),
            "decision_authority": authority,
            "responsible_step": step,
            "stage_status": stage_states or {},
            "proof": proof or {},
        },
    }


# --------------------------------------------------------------------------
# L0: deterministic, and it must not let a bad candidate be measured
# --------------------------------------------------------------------------
def test_l0_passes_a_candidate_aimed_at_the_failing_stage():
    result = check_l0(
        candidate(), mechanism=mechanism(stage="recall_scanner"),
    )
    assert result["passed"] is True
    assert result["failed"] == []
    assert {item["check"] for item in result["checks"]} >= {
        "schema", "no_case_id", "structured_identity", "targets_mechanism",
    }


def test_l0_rejects_a_candidate_that_cannot_reach_the_failing_stage():
    """A candidate aimed at the wrong stage is not measured at all.

    The mechanism failed in the tracer, so the repair has to reach it. A guard
    only decides; it runs after the tracer has already named its step, so it
    cannot change which step gets named.
    """
    guard = make_intervention(
        intervention_id="IV-1-003",
        intervention_type="decision_guard",
        hook="adjudicate.guard",
        packet_features=["has_tool_calls"],
        body={
            "action": "withhold_conviction",
            "action_not_when": "never when the contradiction is confirmed",
        },
        expected_behavior="withhold a conviction the step evidence cannot support",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-x",
        failure_cases=["A/001.json", "B/002.json"],
    )
    result = check_l0(
        guard,
        mechanism=mechanism(stage="step_tracer", failure_mode="unhostable_step"),
    )
    assert result["passed"] is False
    failed = {item["check"] for item in result["failed"]}
    assert "targets_mechanism" in failed
    detail = next(
        item["detail"] for item in result["failed"]
        if item["check"] == "targets_mechanism"
    )
    assert "cannot reach" in detail
    # A tracer directive does reach it.
    assert check_l0(
        candidate(intervention_id="IV-1-002", hook="tracer.system",
                  directive="name the step where the content deviated"),
        mechanism=mechanism(stage="step_tracer", failure_mode="unhostable_step"),
    )["passed"] is True


def test_l0_rejects_a_case_id_leak():
    leaked = candidate(directive="fix A/001.json specifically")
    result = check_l0(leaked, mechanism=mechanism(stage="recall_scanner"))
    assert result["passed"] is False
    assert any(item["check"] == "no_case_id" for item in result["failed"])


def test_l0_rejects_a_redundant_executable_identity():
    """The same executable thing is already deployed: nothing new to measure.

    Prose types (``text_boundary``) have no structured identity by design, so
    the check is exercised with an ``evidence_check``, whose identity is its
    executable content.
    """
    def evidence(intervention_id: str) -> dict:
        return make_intervention(
            intervention_id=intervention_id,
            intervention_type="evidence_check",
            hook="judge.post",
            packet_features=["has_tool_calls"],
            body={
                "predicate": "required_action_present",
                "runner": "deterministic",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
            },
            expected_behavior="mechanically observe the unmet action",
            target_polarity="recall",
            cycle=1,
            failure_family="FAM-fn-x",
            failure_cases=["A/001.json", "B/002.json"],
        )

    result = check_l0(
        evidence("IV-1-002"),
        mechanism=mechanism(stage="judge"),
        active_entries=[evidence("IV-1-001")],
    )
    assert result["passed"] is False
    assert any(
        item["check"] == "structured_identity" for item in result["failed"]
    )
    # A prose candidate is not rejected for lacking a key: the check reports it
    # as not applicable rather than as a conflict.
    prose = check_l0(candidate(), mechanism=mechanism(stage="recall_scanner"))
    assert all(
        item["passed"] for item in prose["checks"]
        if item["check"] == "structured_identity"
    )


def test_l0_rejects_an_invalid_candidate():
    broken = candidate()
    broken["hook"] = "no.such.hook"
    result = check_l0(broken, mechanism=mechanism(stage="recall_scanner"))
    assert result["passed"] is False
    assert any(item["check"] == "schema" for item in result["failed"])


def test_l0_reports_when_a_predicate_fires_on_a_normal_neighbour():
    """A rule that is already wrong cannot be repaired by measuring it more."""
    def runner(entry, neighbor):
        return "violated" if neighbor.get("case") == "N/003.json" else "satisfied"

    result = check_l0(
        candidate(), mechanism=mechanism(stage="recall_scanner"),
        normal_neighbors=[{"case": "N/003.json"}],
        predicate_runner=runner,
    )
    assert result["passed"] is False
    assert any(
        item["check"] == "normal_neighbors_clean" for item in result["failed"]
    )


def test_plan_reaches_uses_the_dependency_map():
    assert plan_reaches("recall.trigger", "recall_scanner") is True
    assert plan_reaches("tracer.system", "step_tracer") is True
    assert plan_reaches("tracer.system", "judge") is False
    assert plan_reaches("no.such.hook", "judge") is False


# --------------------------------------------------------------------------
# L1: the target must move and the neighbours must hold
# --------------------------------------------------------------------------
def test_l1_passes_when_the_target_improves_and_neighbours_hold():
    baseline = [
        row("A/001.json", arm="baseline", gold=True, prediction=False),
        row("B/002.json", arm="baseline", gold=True, prediction=False),
        row("N/003.json", arm="baseline", gold=False, prediction=False),
    ]
    active = [
        row("A/001.json", arm="candidate", gold=True, prediction=True),
        row("B/002.json", arm="candidate", gold=True, prediction=True),
        row("N/003.json", arm="candidate", gold=False, prediction=False),
    ]
    result = check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json", "B/002.json"],
        neighbor_cases=["N/003.json"],
    )
    assert result["passed"] is True
    assert result["target_improved"] == ["A/001.json", "B/002.json"]
    assert result["neighbor_regressed"] == []


def test_l1_does_not_freeze_on_a_target_gain_that_breaks_a_neighbour():
    """§15.4's first negative test: improvement does not excuse a regression.

    The candidate repairs one training failure and simultaneously convicts a
    case the deployed version had right. That is not a partially good
    candidate; it is one that must not reach a frozen protocol.
    """
    baseline = [
        row("A/001.json", arm="baseline", gold=True, prediction=False),
        row("N/003.json", arm="baseline", gold=False, prediction=False),
    ]
    active = [
        row("A/001.json", arm="candidate", gold=True, prediction=True),
        row("N/003.json", arm="candidate", gold=False, prediction=True),
    ]
    result = check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json"], neighbor_cases=["N/003.json"],
    )
    assert result["passed"] is False
    assert result["target_improved"] == ["A/001.json"]
    assert result["neighbor_regressed"] == ["N/003.json"]
    assert any("neighbour" in reason for reason in result["reasons"])
    assert result["delta"]["new_errors"] == 1


def test_l1_fails_when_no_target_case_moved():
    baseline = [row("A/001.json", arm="baseline", gold=True, prediction=False)]
    active = [row("A/001.json", arm="candidate", gold=True, prediction=False)]
    result = check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json"], neighbor_cases=[],
    )
    assert result["passed"] is False
    assert result["target_improved"] == []
    assert any("no target case improved" in r for r in result["reasons"])


def test_l1_requires_the_candidate_to_have_actually_fired():
    """A candidate whose hook never ran measured nothing about itself.

    Plan §7.4 items 3-4: the hook fired and the target behaviour changed
    observably. Without this the loop would read "the target did not improve"
    as evidence about the mechanism when in fact the candidate never reached
    the runtime.
    """
    baseline = [row("A/001.json", arm="baseline", gold=True, prediction=False)]
    active = [row("A/001.json", arm="candidate", gold=True, prediction=True)]
    never_fired = check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json"], neighbor_cases=[],
        intervention_id="IV-1-001",
    )
    assert never_fired["passed"] is False
    assert never_fired["fired_cases"] == []
    assert any("never reached the runtime" in r for r in never_fired["reasons"])

    fired = check_l1(
        baseline_rows=baseline,
        candidate_rows=[
            {**active[0], "result": {
                **active[0]["result"],
                "proof": {"deployment": {"fired": [
                    {"intervention_id": "IV-1-001", "event": "text_injected"},
                ]}},
            }},
        ],
        target_cases=["A/001.json"], neighbor_cases=[],
        intervention_id="IV-1-001",
    )
    assert fired["passed"] is True
    assert fired["fired_cases"] == ["A/001.json"]
    # A bookkeeping event that records a non-match is not a firing.
    not_matched = check_l1(
        baseline_rows=baseline,
        candidate_rows=[
            {**active[0], "result": {
                **active[0]["result"],
                "proof": {"deployment": {"fired": [
                    {"intervention_id": "IV-1-001",
                     "event": "recall_trigger_not_matched"},
                ]}},
            }},
        ],
        target_cases=["A/001.json"], neighbor_cases=[],
        intervention_id="IV-1-001",
    )
    assert not_matched["passed"] is False
    assert not_matched["fired_cases"] == []
    # Without a declared intervention id the firing check is not applied: a
    # caller measuring something that does report a pair still works.
    assert check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json"], neighbor_cases=[],
    )["passed"] is True


def test_l1_ignores_a_non_authoritative_candidate_row():
    baseline = [row("A/001.json", arm="baseline", gold=True, prediction=False)]
    active = [
        row("A/001.json", arm="candidate", gold=True, prediction=True,
            authority="degraded_judge_only"),
    ]
    result = check_l1(
        baseline_rows=baseline, candidate_rows=active,
        target_cases=["A/001.json"], neighbor_cases=[],
    )
    assert result["passed"] is False


def test_l1_localization_reads_the_step_not_only_the_conviction():
    baseline = [
        row("L/001.json", arm="baseline", gold=True, prediction=True, step=1),
    ]
    # The conviction is unchanged and the step moved onto the annotated step:
    # a localization repair. The binary decision is identical in both rows, so
    # a detection reading would have called this "no improvement".
    improved = check_l1(
        baseline_rows=baseline,
        candidate_rows=[
            row("L/001.json", arm="candidate", gold=True, prediction=True, step=3),
        ],
        target_cases=["L/001.json"], neighbor_cases=[], localization=True,
        gold_steps={"L/001.json": 3},
    )
    assert improved["passed"] is True
    unchanged = check_l1(
        baseline_rows=baseline,
        candidate_rows=[
            row("L/001.json", arm="candidate", gold=True, prediction=True, step=1),
        ],
        target_cases=["L/001.json"], neighbor_cases=[], localization=True,
        gold_steps={"L/001.json": 3},
    )
    assert unchanged["passed"] is False


def test_l1_localization_refuses_a_differently_wrong_step():
    """A step that moves is not a step that improves.

    Without the annotation "the step changed" accepts a candidate that
    redirects the conviction from one wrong step to another, which repairs
    nothing.
    """
    baseline = [
        row("L/001.json", arm="baseline", gold=True, prediction=True, step=1),
    ]
    wrongly_moved = check_l1(
        baseline_rows=baseline,
        candidate_rows=[
            row("L/001.json", arm="candidate", gold=True, prediction=True, step=9),
        ],
        target_cases=["L/001.json"], neighbor_cases=[], localization=True,
        gold_steps={"L/001.json": 3},
    )
    assert wrongly_moved["passed"] is False
    assert wrongly_moved["target_unchanged"] == ["L/001.json"]


def test_l1_localization_needs_the_annotation_to_claim_an_improvement():
    """No annotation, no improvement claim: the step is not scored blindly."""
    result = check_l1(
        baseline_rows=[
            row("L/001.json", arm="baseline", gold=True, prediction=True, step=1),
        ],
        candidate_rows=[
            row("L/001.json", arm="candidate", gold=True, prediction=True, step=3),
        ],
        target_cases=["L/001.json"], neighbor_cases=[], localization=True,
    )
    assert result["passed"] is False


# --------------------------------------------------------------------------
# The loop: lineage, budget, and freeze
# --------------------------------------------------------------------------
def _developer(tmp_path, *, budget: DevelopmentBudget | None = None,
              l1_runner=None, **overrides) -> CandidateDeveloper:
    kwargs = dict(
        output_dir=tmp_path,
        budget=budget or DevelopmentBudget(),
        mechanism=mechanism(stage="recall_scanner"),
        capability_plan={"sufficient_routes": ["recall_trigger"]},
        target_cases=["A/001.json"],
        neighbor_cases=["N/003.json"],
        l1_runner=l1_runner,
    )
    kwargs.update(overrides)
    return CandidateDeveloper(**kwargs)


def test_loop_records_lineage_and_freezes_the_passing_version(tmp_path):
    """The seed fails L0, is revised, and the revision passes L1 and freezes."""
    def l1_runner(cases, *, candidate, plan):
        # The recall net was never reached on the baseline and runs on the
        # candidate arm: the stage the mechanism failed in actually moved.
        return [
            row("A/001.json", arm="baseline", gold=True, prediction=False,
                stage_states={"recall_scanner": {"state": "not_needed"}}),
            row("N/003.json", arm="baseline", gold=False, prediction=False,
                stage_states={"recall_scanner": {"state": "not_needed"}}),
            row("A/001.json", arm="candidate", gold=True, prediction=True,
                stage_states={"recall_scanner": {"state": "ok"}},
                proof=fired("IV-1-001")),
            row("N/003.json", arm="candidate", gold=False, prediction=False,
                stage_states={"recall_scanner": {"state": "not_needed"}},
                proof=fired("IV-1-001")),
        ]

    good = candidate()
    bad = broken_candidate()  # unknown hook: fails schema at L0
    drafts = [bad, good]
    seen: list[str] = []

    def propose(mechanism_, plan_, parent, diagnosis, cycle):
        seen.append(str(parent.get("intervention_id")) if parent else "")
        return [drafts.pop(0)] if drafts else []

    developer = _developer(tmp_path, l1_runner=l1_runner)
    result = developer.develop(propose, cycle=1)
    assert result["selected_version_id"] == "v002"
    assert [item["version_id"] for item in result["versions"]] == ["v001", "v002"]
    assert result["versions"][0]["status"] == "failed_l0"
    assert result["versions"][1]["status"] == "ready_for_freeze"
    # Lineage is on disk: v002's parent is the measured version, and the draft
    # that never ran is not its parent.
    assert result["lineage"] == ["v002"]
    assert result["status"] == "ready_for_freeze"
    # The revision saw the failure diagnosis of the version before it.
    frozen = developer.freeze("v002")
    assert frozen["version_id"] == "v002"
    assert frozen["mechanism_id"] == "M-dev"
    assert frozen["route"] == "recall_directive"
    assert result["planned_route"] == "recall_trigger"
    assert result["versions"][1]["route_replanned"] is True
    assert frozen["candidate"]["intervention_id"] == "IV-1-001"


def test_loop_stops_at_the_version_cap(tmp_path):
    """The budget is a cap, not a suggestion."""
    def propose(mechanism_, plan_, parent, diagnosis, cycle):
        return [broken_candidate()]  # always fails L0, so never freezes

    budget = DevelopmentBudget(max_versions_per_mechanism=2)
    developer = _developer(tmp_path, budget=budget)
    result = developer.develop(propose, cycle=1)
    assert len(result["versions"]) == 2
    assert result["stopped"] == "max_versions_per_mechanism"
    assert result["selected_version_id"] == ""
    assert result["status"] == "discarded"


def test_loop_stops_when_no_candidate_is_drafted(tmp_path):
    developer = _developer(tmp_path)
    result = developer.develop(lambda *a, **k: [], cycle=1)
    assert result["versions"] == []
    assert result["stopped"] == "no_candidates"
    assert result["selected_version_id"] == ""


def test_loop_stops_before_exceeding_the_l1_case_budget(tmp_path):
    def l1_runner(cases, *, candidate, plan):
        return []

    budget = DevelopmentBudget(max_l1_cases=1)
    developer = _developer(tmp_path, budget=budget, l1_runner=l1_runner)
    result = developer.develop(
        lambda *a, **k: [candidate()], cycle=1,
    )
    assert result["stopped"] == "max_l1_cases"
    assert result["versions"] == []


def test_freeze_refuses_a_version_that_did_not_pass_l1(tmp_path):
    """The only exit from development is a passing version.

    A candidate that broke a neighbour is recorded as failed_l1, and freeze
    must refuse it -- otherwise the formal gates would be asked to re-run the
    development loop's own job.
    """
    def l1_runner(cases, *, candidate, plan):
        return [
            row("A/001.json", arm="baseline", gold=True, prediction=False),
            row("N/003.json", arm="baseline", gold=False, prediction=False),
            row("A/001.json", arm="candidate", gold=True, prediction=True,
                proof=fired("IV-1-001")),
            row("N/003.json", arm="candidate", gold=False, prediction=True,
                proof=fired("IV-1-001")),
        ]

    developer = _developer(tmp_path, l1_runner=l1_runner)
    result = developer.develop(lambda *a, **k: [candidate()], cycle=1)
    assert result["versions"][0]["status"] == "failed_l1"
    with pytest.raises(ValueError, match="not ready_for_freeze"):
        developer.freeze(result["versions"][0]["version_id"])


def test_freeze_refuses_an_unknown_version(tmp_path):
    developer = _developer(tmp_path)
    with pytest.raises(ValueError, match="unknown version"):
        developer.freeze("v999")


def test_development_status_is_a_sidecar_not_a_change_to_the_version_record(
    tmp_path,
):
    """The research format stays parseable: status goes in its own file."""
    def propose(mechanism_, plan_, parent, diagnosis, cycle):
        return [broken_candidate()]

    budget = DevelopmentBudget(max_versions_per_mechanism=1)
    developer = _developer(tmp_path, budget=budget)
    developer.develop(propose, cycle=1)
    record = json.loads(
        (tmp_path / "v001" / "candidate.json").read_text(encoding="utf-8")
    )
    # Exactly the fields the research format already had.
    assert set(record) == {
        "version_id", "route", "kind", "candidate", "candidate_hash",
        "parent_hash", "notes", "evaluation", "verdict",
    }
    sidecar = json.loads(
        (tmp_path / "v001" / "development.json").read_text(encoding="utf-8")
    )
    assert sidecar["status"] == "failed_l0"
    assert sidecar["schema_version"] == "evolution.candidate_development.v1"


def test_development_log_is_machine_readable(tmp_path):
    def propose(mechanism_, plan_, parent, diagnosis, cycle):
        return [broken_candidate()]

    budget = DevelopmentBudget(max_versions_per_mechanism=1)
    developer = _developer(tmp_path, budget=budget)
    developer.develop(propose, cycle=1)
    log = developer.graph.read_log()
    assert len(log) == 1
    assert log[0]["status"] == "failed_l0"
    assert log[0]["mechanism_id"] == "M-dev"
    assert log[0]["l0_passed"] is False


def test_development_report_is_compact_and_auditable(tmp_path):
    def propose(mechanism_, plan_, parent, diagnosis, cycle):
        return [broken_candidate()]

    budget = DevelopmentBudget(max_versions_per_mechanism=1)
    developer = _developer(tmp_path, budget=budget)
    result = developer.develop(propose, cycle=1)
    report = development_report(result)
    assert report["mechanism_id"] == "M-dev"
    assert report["versions"][0]["l0_passed"] is False
    assert report["versions"][0]["l1_passed"] is False
    assert report["budget"]["caps"]["max_versions_per_mechanism"] == 1
    json.dumps(report, ensure_ascii=False)


def test_the_loop_never_drafts_against_a_route_the_planner_rejected(tmp_path):
    """The route comes from the capability plan, not from the family list."""
    developer = _developer(
        tmp_path,
        capability_plan={
            "sufficient_routes": ["recall_trigger"],
            "insufficient_routes": [
                {"route": "tracer_directive", "reasons": ["wrong capability"]},
            ],
        },
    )
    result = developer.develop(lambda *a, **k: [], cycle=1)
    assert result["route"] == "recall_trigger"
    # With no sufficient route there is nothing to develop against.
    empty = _developer(tmp_path / "other", capability_plan={})
    assert empty.develop(lambda *a, **k: [], cycle=1)["route"] == ""


# --------------------------------------------------------------------------
# The version store is shared with the research path
# --------------------------------------------------------------------------
def test_version_graph_numbering_continues_across_runs(tmp_path):
    first = CandidateVersionGraph(tmp_path)
    assert first.new_version_id() == "v001"
    (tmp_path / "v001").mkdir()
    (tmp_path / "v001" / "candidate.json").write_text(
        json.dumps({"version_id": "v001", "route": "A", "candidate_hash": "x"}),
        encoding="utf-8",
    )
    second = CandidateVersionGraph(tmp_path)
    second.load_existing()
    assert second.new_version_id() == "v002"
    assert second.route_versions() == {"A": ["v001"]}


def test_version_graph_recovers_lineage_from_disk(tmp_path):
    graph = CandidateVersionGraph(tmp_path)
    parent = candidate()
    child = candidate(intervention_id="IV-1-002")
    graph.record("A", "v001", parent, parent=None, diagnosis=None,
                 draft_notes=[], evaluation=None, verdict=None)
    graph.record("A", "v002", child, parent=parent, diagnosis={"reasons": ["x"]},
                 draft_notes=[], evaluation=None, verdict=None)
    reloaded = CandidateVersionGraph(tmp_path)
    reloaded.load_existing()
    assert reloaded.parent_of("v002") == "v001"
    assert reloaded.lineage("v002") == ["v001", "v002"]
    assert reloaded.lineage("v001") == ["v001"]


def test_l0_reports_a_neighbour_runner_that_raises(tmp_path):
    """A runner that raises is a failure, not a clean neighbour.

    Skipping the exception would report ``normal_neighbors_clean`` as passed
    for a check that never ran -- exactly the "looks green because nothing was
    measured" failure the deterministic layer exists to prevent.
    """
    def exploding_runner(candidate_, neighbor):
        raise RuntimeError("runner is broken")

    verdict = check_l0(
        candidate(),
        mechanism=mechanism(),
        normal_neighbors=[{"case": "N/001.json", "gold": False}],
        predicate_runner=exploding_runner,
    )
    assert verdict["passed"] is False
    failed = {item["check"]: item for item in verdict["failed"]}
    assert "normal_neighbors_clean" in failed
    assert "RuntimeError" in failed["normal_neighbors_clean"]["detail"]


def test_l0_records_an_unexpected_validator_error_instead_of_raising():
    """A malformed candidate fails L0; it does not abort the loop."""
    verdict = check_l0(
        {"intervention_id": "IV-x", "type": "text_boundary", "hook": 42},
        mechanism=mechanism(),
    )
    assert verdict["passed"] is False
    assert verdict["failed"]
    assert any(item["check"] == "schema" for item in verdict["failed"])


def test_l0_refuses_a_localization_candidate_without_an_annotation():
    """No annotated step means no localization improvement can be shown."""
    verdict = check_l0(
        candidate(),
        mechanism=mechanism(),
        target_kind="localization",
        target_cases=["L/001.json"],
        gold_steps={},
    )
    assert verdict["passed"] is False
    assert any(
        item["check"] == "localization_annotation" for item in verdict["failed"]
    )
    # With the annotation the same candidate passes that check.
    ok = check_l0(
        candidate(),
        mechanism=mechanism(),
        target_kind="localization",
        target_cases=["L/001.json"],
        gold_steps={"L/001.json": 3},
    )
    assert all(item["check"] != "localization_annotation" for item in ok["failed"])


def test_l1_case_budget_charges_both_arms(tmp_path):
    """A replay of N cases costs 2N evaluations, one per arm each.

    Charging N let the loop measure twice the configured budget, which made the
    development cap a number the run reported but did not enforce.
    """
    def l1_runner(cases, *, candidate, plan):
        return []

    budget = DevelopmentBudget(max_l1_cases=3)
    developer = _developer(
        tmp_path, budget=budget, l1_runner=l1_runner,
        target_cases=["A/001.json"], neighbor_cases=["N/003.json"],
    )
    result = developer.develop(lambda *a, **k: [candidate()], cycle=1)
    # Two cases, two arms: 4 evaluations needed against a cap of 3.
    assert result["stopped"] == "max_l1_cases"
    assert result["versions"] == []


def test_l1_provider_budget_stops_the_loop(tmp_path):
    """The draft charge is real: the provider cap is enforced, not decorative."""
    def l1_runner(cases, *, candidate, plan):
        return []

    budget = DevelopmentBudget(max_provider_calls=2, max_l1_cases=100)
    developer = _developer(tmp_path, budget=budget, l1_runner=l1_runner)
    result = developer.develop(lambda *a, **k: [candidate()], cycle=1)
    assert result["budget"]["used"]["provider_calls"] <= 2
    assert len(result["versions"]) <= 2


def test_development_never_measures_on_formal_gain_cases(tmp_path):
    """The development loop may not spend a sentinel or gain case."""
    from evolution.config import load_config
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit

    cases = [f"A/{index:03d}.json" for index in range(12)]
    for index, case in enumerate(cases):
        path = tmp_path / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"task {index}",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
        }), encoding="utf-8")
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:8],
        patrol_pool=cases[8:], mini=True,
    )
    config = load_config()
    config["dataset_root"] = str(tmp_path)
    engine = EvolutionEngine(config, split=split, mini=True)
    with pytest.raises(ValueError, match="formal evaluation cases"):
        engine.develop_rows(
            [cases[4]], candidate=candidate(), split=split,
        )
