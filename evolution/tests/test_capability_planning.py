"""Capability planning: "a route exists" is not "a route can repair".

The plan separates three questions that were previously one:

    Generatable  ->  Triggerable  ->  Repair-capable

``worksheet.classify_family_actionability`` answers the first by enumerating
treatment routes. This module answers the third by checking the mechanism's
required repair against what the runtime can actually read and do, and by
consuming the route-falsification memory. The pilot's tracer directive is the
recorded case: the route existed, the hook fired, and the step never moved.

These tests pin each way the third answer can come out, so a mechanism is never
labelled actionable on the strength of an enumeration.
"""

from __future__ import annotations

from detector.predicate_context import HOOK_CONTEXT_FIELDS
from detector.predicate_dsl import FIELD_TYPES

from evolution.capability_planning import (
    CAPABILITY_KINDS,
    CAPABILITY_SCHEMA,
    ROUTE_CAPABILITY,
    analyze_capability_requirement,
    capability_gap_record,
    capability_surface_report,
    plan_capabilities,
)
from evolution.mechanisms import infer_mechanism, repair_requirements
from evolution.route_failures import record_route_failures
from evolution.tests.route_observation_fixture import measured_fixture


def mechanism(
    *,
    obligation: str = "required_action",
    deviation: str = "omission",
    operation: str = "observe_deviation",
    stage: str = "recall",
    failure_mode: str = "not_reached",
    objective: str = "detection",
    mechanism_id: str = "M-test",
) -> dict:
    return {
        "schema_version": "evolution.failure_mechanism.v1",
        "mechanism_id": mechanism_id,
        "family_id": "FAM-fn-x",
        "objective": objective,
        "task_obligation": {"kind": obligation},
        "observed_deviation": {"kind": deviation},
        "detector_failure": {"stage": stage, "failure_mode": failure_mode},
        "repair_hypothesis": {
            "operation": operation,
            "required_inputs": list(
                repair_requirements({
                    "repair_hypothesis": {"operation": operation},
                    "task_obligation": {"kind": obligation},
                })["required_inputs"]
            ),
            "required_capabilities": [],
            "forbidden_shortcuts": ["case_id"],
        },
        "member_cases": ["A/001.json", "B/002.json"],
        "status": "confirmed",
        "confidence_basis": "multi_case",
    }


def observation(mid: str, route: str) -> dict:
    return {
        "mechanism_id": mid,
        "family_id": "FAM-fn-x",
        "observation": {
            "route": route,
            "status": "insufficient_for_mechanism",
            "measurement": measured_fixture()[-1],
            "evidence": {"hook_fired": 0.9, "output_changed": 7,
                         "target_outcome_changed": 0},
        },
    }


# --------------------------------------------------------------------------
# The surface report must describe the real runtime
# --------------------------------------------------------------------------
def test_surface_report_matches_the_runtime_vocabulary():
    """The planner's view of the runtime is derived from the runtime itself."""
    surface = capability_surface_report()
    assert surface["schema_version"] == CAPABILITY_SCHEMA
    assert surface["typed_field_count"] == len(FIELD_TYPES)
    assert surface["guard_actions"]
    assert surface["hooks"]
    # Every route the planner knows maps to a capability the surface names.
    for route, (capability, hooks) in ROUTE_CAPABILITY.items():
        assert capability in surface["capabilities"], route
        for hook in hooks:
            assert hook in surface["hooks"], (route, hook)
    # The context-field readings are the runtime's own, not a second copy.
    for hook, fields in surface["hook_reads_context_fields"].items():
        assert fields == sorted(HOOK_CONTEXT_FIELDS.get(hook, frozenset()))


def test_every_repair_operation_declares_only_real_typed_fields():
    """A required input that is not a runtime field is a bug, not a limit."""
    for operation in (
        "route_recall", "observe_deviation", "repair_causal_upgrade",
        "repair_defense_boundary", "withhold_conviction", "tighten_predicate",
        "constrain_localization", "repair_localization_source", "unavailable",
    ):
        for obligation in ("required_action", "verbatim_content", "completion_claim"):
            requirement = repair_requirements({
                "repair_hypothesis": {"operation": operation},
                "task_obligation": {"kind": obligation},
            })
            for field in requirement["required_inputs"]:
                assert field in FIELD_TYPES, (operation, obligation, field)


# --------------------------------------------------------------------------
# actionability
# --------------------------------------------------------------------------
def test_mechanically_decidable_repair_is_actionable():
    """An omitted tool action can be observed with the typed vocabulary."""
    result = analyze_capability_requirement(
        mechanism(obligation="required_action", operation="observe_deviation"),
        routes=["evolved_predicate"],
    )
    assert result["classification"] == "actionable"
    assert result["sufficient_routes"] == ["evolved_predicate"]
    assert result["required_capability"] == ""
    assert result["insufficient_routes"] == []


def test_semantic_repair_is_a_recorded_capability_gap():
    """A value comparison the deterministic operators cannot settle.

    ``detector.predicates`` deliberately reports ``insufficient`` rather than
    convicting on a value mismatch, and the typed operator set can test literal
    containment but not "these two values mean the same thing". So an
    ``exact_value`` mechanism is not repairable with an observation, however
    the program is attached.
    """
    result = analyze_capability_requirement(
        mechanism(obligation="exact_value", operation="observe_deviation"),
        routes=["evolved_predicate"],
    )
    assert result["classification"] == "capability_gap"
    assert result["required_capability"] == "semantic_judgment_missing"
    assert result["sufficient_routes"] == []
    assert result["insufficient_routes"][0]["route"] == "evolved_predicate"
    assert any(
        "semantic judgment" in item for item in result["evidence"]
    )


def test_prompt_only_route_is_named_rather_than_called_a_capability():
    """A prose route is reachable but compares nothing, and says so."""
    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=["tracer_directive"],
    )
    assert result["classification"] == "actionable"
    assert result["required_capability"] == "prompt_only"
    assert result["sufficient_routes"] == ["tracer_directive"]


def test_localization_gap_names_the_missing_operator():
    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=[],
    )
    assert result["classification"] == "capability_gap"
    assert result["required_capability"] == "localization_attribution_missing"


def test_guard_route_serves_a_false_conviction():
    result = analyze_capability_requirement(
        mechanism(
            obligation="exact_value", deviation="none",
            operation="withhold_conviction",
            stage="adjudication", failure_mode="false_conviction",
        ),
        routes=["withhold_guard"],
    )
    assert result["classification"] == "actionable"
    assert result["sufficient_routes"] == ["withhold_guard"]


def test_restore_guard_serves_a_refuted_defence():
    result = analyze_capability_requirement(
        mechanism(
            obligation="verbatim_content", deviation="mismatch",
            operation="repair_defense_boundary",
            stage="defense", failure_mode="refuted_judge",
        ),
        routes=["restore_guard"],
    )
    assert result["classification"] == "actionable"
    assert result["sufficient_routes"] == ["restore_guard"]


# --------------------------------------------------------------------------
# route falsification is consumed
# --------------------------------------------------------------------------
def test_a_falsified_route_is_no_longer_sufficient(tmp_path):
    """The tracer directive that fired and moved nothing is not a fix."""
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(
            path, cycle=cycle,
            observations=[observation("M-test", "tracer_directive")],
        )
    from evolution.route_failures import load_route_failures

    records = load_route_failures(path)
    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=["tracer_directive"],
        route_failure_records=records,
        runtime_identity=measured_fixture()[1],
    )
    assert result["sufficient_routes"] == []
    assert result["classification"] == "research_only"
    assert result["required_capability"] == "already_supported_but_insufficient"
    assert any(
        "fired and changed nothing" in item for item in result["evidence"]
    )


def test_a_single_observation_does_not_close_a_route(tmp_path):
    """One round is not a falsification; the route stays available."""
    path = tmp_path / "route_failures.jsonl"
    record_route_failures(
        path, cycle=1,
        observations=[observation("M-test", "tracer_directive")],
    )
    from evolution.route_failures import load_route_failures

    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=["tracer_directive"],
        route_failure_records=load_route_failures(path),
        runtime_identity=measured_fixture()[1],
    )
    assert result["sufficient_routes"] == ["tracer_directive"]


def test_falsification_on_another_mechanism_does_not_close_the_route(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(
            path, cycle=cycle,
            observations=[observation("M-other", "tracer_directive")],
        )
    from evolution.route_failures import load_route_failures

    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=["tracer_directive"],
        route_failure_records=load_route_failures(path),
        runtime_identity=measured_fixture()[1],
    )
    assert result["sufficient_routes"] == ["tracer_directive"]


def test_one_falsified_route_leaves_the_others_available(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(
            path, cycle=cycle,
            observations=[observation("M-test", "tracer_directive")],
        )
    from evolution.route_failures import load_route_failures

    result = analyze_capability_requirement(
        mechanism(
            obligation="unspecified", deviation="mislocation",
            operation="constrain_localization",
            stage="step_tracer", failure_mode="unhostable_step",
            objective="localization",
        ),
        routes=["tracer_directive", "upgrade_directive"],
        route_failure_records=load_route_failures(path),
        runtime_identity=measured_fixture()[1],
    )
    assert result["sufficient_routes"] == ["upgrade_directive"]
    assert [item["route"] for item in result["insufficient_routes"]] == [
        "tracer_directive"
    ]


# --------------------------------------------------------------------------
# planning and gap records
# --------------------------------------------------------------------------
def test_plan_capabilities_separates_actionable_from_gaps():
    families = [
        {
            "failure_family": "FAM-fn-a",
            "count": 3,
            "sources": ["BFCL"],
            "mechanism": mechanism(mechanism_id="M-a"),
            "actionability": {"routes": ["evolved_predicate"]},
        },
        {
            "failure_family": "FAM-fn-b",
            "count": 2,
            "sources": ["Camel"],
            "mechanism": mechanism(
                mechanism_id="M-b", obligation="exact_value",
            ),
            "actionability": {"routes": ["evolved_predicate"]},
        },
    ]
    plan = plan_capabilities(families)
    assert set(plan["actionable_mechanisms"]) == {"M-a"}
    assert len(plan["gaps"]) == 1
    gap = plan["gaps"][0]
    assert gap["mechanism_id"] == "M-b"
    assert gap["classification"] == "semantic_judgment_missing"
    assert gap["failure_family"] == "FAM-fn-b"
    # The gap reason vocabulary is the planner's, not a single catch-all.
    assert gap["classification"] in CAPABILITY_KINDS
    assert "capability_surface" in plan


def test_gap_record_is_shaped_for_the_backlog_store():
    result = analyze_capability_requirement(
        mechanism(obligation="exact_value", operation="observe_deviation"),
        routes=["evolved_predicate"],
    )
    record = capability_gap_record(
        result, failure_family="FAM-fn-b", count=2, sources=["Camel"],
    )
    for key in (
        "failure_family", "classification", "reason", "count", "sources",
        "routes",
    ):
        assert key in record, key
    assert record["reason"]
    # The backlog identity keys on (family, classification); the mechanism id
    # travels with the record so the gap is traceable to a reading.
    assert record["classification"] == "semantic_judgment_missing"


def test_plan_ignores_families_without_a_mechanism():
    plan = plan_capabilities([
        {"failure_family": "FAM-x", "count": 2, "actionability": {}},
    ])
    assert plan["requirements"] == []
    assert plan["gaps"] == []


def test_inference_and_planning_agree_on_required_inputs():
    """The mechanism's declared inputs are the runtime's own field names."""
    inferred = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates-abc",
        members=[
            {
                "member": "member_001",
                "contracts": ["required_action"],
                "stages": {},
                "judge_positive": False,
                "recall_path": "not_needed",
                "upgrade_positive": False,
                "defense_verdict": "none",
                "predicates": {"required_action_present": "violated"},
                "typed_samples": {},
            },
            {
                "member": "member_002",
                "contracts": ["required_action"],
                "stages": {},
                "judge_positive": False,
                "recall_path": "not_needed",
                "upgrade_positive": False,
                "defense_verdict": "none",
                "predicates": {"required_action_present": "violated"},
                "typed_samples": {},
            },
        ],
        member_cases=["A/001.json", "B/002.json"],
    )
    for field in inferred["repair_hypothesis"]["required_inputs"]:
        assert field in FIELD_TYPES, field
    # The real route list for an FN family whose recall never ran.
    result = analyze_capability_requirement(
        inferred,
        routes=[
            "recall_trigger", "recall_directive", "upgrade_directive",
            "evolved_predicate",
        ],
    )
    assert result["classification"] == "actionable"
    assert result["sufficient_routes"] == ["recall_trigger"]


def test_an_observation_cannot_start_a_recall_net_that_never_ran():
    """Reading the right fields is not the same as being able to act on them.

    The mechanism is "recall never reached", whose repair is to route recall.
    An ``evidence_check`` observes a case at ``judge.post``; it does not make
    the recall scanner run. So it cannot repair this mechanism however well its
    inputs line up -- and before capability fit was checked, it was reported as
    sufficient on exactly that basis.
    """
    recall_mechanism = mechanism(
        obligation="required_action", deviation="omission",
        operation="route_recall", stage="recall_scanner", failure_mode="not_reached",
    )
    only_observation = analyze_capability_requirement(
        recall_mechanism, routes=["evolved_predicate", "mechanical_conviction"],
    )
    assert only_observation["sufficient_routes"] == []
    assert only_observation["classification"] == "capability_gap"
    assert only_observation["required_capability"] == "control_flow_missing"
    assert all(
        item["capability_fits"] is False
        for item in only_observation["insufficient_routes"]
    )
    # The route that can act on it is the one that opens the net.
    with_trigger = analyze_capability_requirement(
        recall_mechanism, routes=["evolved_predicate", "recall_trigger"],
    )
    assert with_trigger["sufficient_routes"] == ["recall_trigger"]
    # A widening directive is not an opener, and is reported as unfit here.
    assert analyze_capability_requirement(
        recall_mechanism, routes=["recall_directive"],
    )["sufficient_routes"] == []


def test_localization_needs_a_localization_operator_not_an_observation():
    """A conviction guard cannot constrain which step an LLM names."""
    localization = mechanism(
        obligation="unspecified", deviation="mislocation",
        operation="constrain_localization", stage="step_tracer",
        failure_mode="unhostable_step", objective="localization",
    )
    result = analyze_capability_requirement(
        localization,
        routes=["evolved_predicate", "mechanical_conviction", "tracer_directive"],
    )
    assert result["sufficient_routes"] == ["tracer_directive"]
    assert result["required_capability"] == "prompt_only"
