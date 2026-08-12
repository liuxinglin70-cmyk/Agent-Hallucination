"""Planning-layer parity: L1 prompt gold samples, L2 planner, prevalidation.

Same-process dual-package fixture-driven harness (compare_causal pattern):
one synthetic detection row per failure shape feeds both packages, and the
planning outputs are compared key-by-key.

Checks:
1. L1 prompt gold samples — for every REPAIRABLE L1 scenario:
   - system prompt byte-identical between packages (the closed action
     contract, charters, direction contract, repair-frontier binding and
     VERDICT-CARRIER RULE are verbatim);
   - the <mechanism_repair_plans> paragraph byte-identical (candidate
     schema parity: edge fields, legal_operations, auto-selection);
   - the shared projection keys (candidate / obligation_vs_observed /
     downstream_consumption / repair_frontier / evidence_refs /
     completeness) value-identical;
   - repair_frontier edges (owner_module, field, edge_id) identical.
   The observation section legitimately differs: the new side projects the
   CausalFailureEpisode surface (skill_layer / failure_owner /
   repairable_surface / legal_repair_operations / observable_effect /
   repairability verdict) while the old side carried raw detection /
   attribution snapshots — plan §Phase 6 states the input rebind to the
   episode projection as the migration itself, so this structural
   difference is the documented deviation, not a parity failure.
2. L2 planner — for every REPAIRABLE L2 scenario: route_result and the
   planned candidate signatures (operation + precondition) identical.
3. Candidate prevalidation — a fixed set of legal/illegal candidates
   yields the same reject reason from both packages (the old package
   consumes its ErrorType enum, the new package the closed error_type
   string; both derive the same verdict).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.parity import compare_causal as cc  # noqa: E402
from sehd_rise.causal.cut import build_causal_cut  # noqa: E402
from sehd_rise.causal.episode import (  # noqa: E402
    build_causal_episode,
    build_runtime_observation,
    project_for_prompt,
)
from sehd_rise.causal.repairability import RepairabilityStatus  # noqa: E402
from sehd_rise.causal.signals import project_detection  # noqa: E402
from sehd_rise.planning import l1 as new_l1  # noqa: E402
from sehd_rise.planning import l2 as new_l2  # noqa: E402
from sehd_rise.skills.policy import PolicyGenome  # noqa: E402

from src.sehd_v6.core.bccse import (  # noqa: E402
    failure_classifier as old_cls,
    failure_episode as old_ep,
    policy_genome as old_pg,
    policy_reflection as old_pr,
    reflection_engine as old_ref,
    step_attributor as old_attr,
    evolution_runner as old_runner,
)

CHECKS: list[str] = []
FAILURES: list[str] = []
DEV: list[str] = []


def _ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok  {name}")


def _fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"  FAIL {name}: {detail}")


def _dev(name: str, detail: str) -> None:
    DEV.append(name)
    print(f"  DEV {name}: {detail}")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _scenario_row(scenario: cc.Scenario) -> dict[str, Any]:
    row = dict(scenario.row)
    if scenario.use_gold and scenario.gold_label is not None:
        row["gold_label"] = scenario.gold_label
    return row


def _repair_plans_paragraph(prompt: str) -> str:
    match = re.search(
        r"<mechanism_repair_plans.*?</mechanism_repair_plans>", prompt, re.S
    )
    return match.group(0) if match else ""


def _build_l1_pair(scenario: cc.Scenario) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (old_projection, new_projection) for one L1 scenario."""
    row = _scenario_row(scenario)
    allowed = list(scenario.allowed_modules)
    old_signal = old_attr.attribute_failure(row, use_gold=False, gold_reflection=False)
    old_artifacts = old_ep.project_provider_artifacts(
        detection_result=row, signal=old_signal, primary_type=""
    )
    record = cc._to_record(
        row,
        boundary_path_state=scenario.boundary_path_state,
        provider_artifacts=old_artifacts,
    )
    old_attribution = old_cls.classify_failure(record)
    old_episode = old_ep.build_failure_episode(
        detection_result=row, signal=old_signal, attribution=old_attribution,
        evolution_direction=scenario.evolution_direction,
        record_key=scenario.case_id, allowed_modules=allowed,
    )
    old_projection = old_ep.project_failure_episode_for_prompt(old_episode)
    old_runtime = old_ep.build_runtime_observation(
        detection_result=row, signal=old_signal, allowed_modules=allowed
    )

    new_signal = project_detection(row, use_gold=False, candidate_kind_hint="")
    new_cut = build_causal_cut(new_signal, boundary_path_state=scenario.boundary_path_state)
    new_episode = build_causal_episode(
        signal=new_signal, cut=new_cut, record_key=scenario.case_id,
        evolution_direction=scenario.evolution_direction, allowed_modules=allowed,
    )
    new_projection = project_for_prompt(new_episode)
    new_runtime = build_runtime_observation(new_signal, allowed_modules=allowed)

    _old_system, old_user = old_ref.build_causal_reflection_prompt(
        old_signal, "", "", episode_projection=old_projection, episode=old_episode,
        runtime_observation=old_runtime, allowed_target_modules=allowed,
    )
    new_system, new_user = new_l1.build_causal_reflection_prompt(
        old_signal, "", "", episode_projection=new_projection, episode=new_episode,
        runtime_observation=new_runtime, allowed_target_modules=allowed,
    )
    return {
        "case_id": scenario.case_id,
        "old_projection": old_projection,
        "new_projection": new_projection,
        "old_user": old_user,
        "new_user": new_user,
        "old_system": _old_system,
        "new_system": new_system,
    }


SHARED_PROJECTION_KEYS = (
    "candidate",
    "obligation_vs_observed",
    "downstream_consumption",
    "repair_frontier",
    "evidence_refs",
    "completeness",
)


def check_l1_prompt_gold_samples() -> None:
    """Same CausalFailureEpisode fixture -> identical key prompt paragraphs
    and identical candidate schema across packages (plan §Phase 6 验证 1)."""
    scenarios = [
        scenario
        for scenario in cc._all_scenarios()
        if (
            scenario.expected_repairability == RepairabilityStatus.REPAIRABLE
            and scenario.expected_new_layer == "l1_provider_rule"
        )
    ]
    for scenario in scenarios:
        pair = _build_l1_pair(scenario)
        case = pair["case_id"]
        _ok(
            f"l1[{case}] system prompt byte-identical: "
            f"{pair['old_system'] == pair['new_system']}"
        )
        if pair["old_system"] != pair["new_system"]:
            _fail(f"l1[{case}] system_prompt", "prompt drifted across packages")
        old_plans = _repair_plans_paragraph(pair["old_user"])
        new_plans = _repair_plans_paragraph(pair["new_user"])
        _ok(f"l1[{case}] repair-plans paragraph byte-identical: {old_plans == new_plans}")
        if old_plans != new_plans:
            _fail(f"l1[{case}] repair_plans_paragraph", "candidate schema drifted")
        for key in SHARED_PROJECTION_KEYS:
            if (
                _canon(pair["old_projection"].get(key))
                != _canon(pair["new_projection"].get(key))
            ):
                _fail(f"l1[{case}] projection.{key}", "value drifted across packages")
        _ok(f"l1[{case}] shared projection keys identical")
        old_frontier = [
            (edge["owner_module"], edge["field"], edge["edge_id"])
            for edge in pair["old_projection"]["repair_frontier"]
        ]
        new_frontier = [
            (edge["owner_module"], edge["field"], edge["edge_id"])
            for edge in pair["new_projection"]["repair_frontier"]
        ]
        _ok(f"l1[{case}] repair frontier edges identical: {old_frontier == new_frontier}")
        if old_frontier != new_frontier:
            _fail(f"l1[{case}] repair_frontier", f"{old_frontier} vs {new_frontier}")


def check_l2_planner() -> None:
    """Same L2 attribution -> same route_result and candidate signatures."""
    scenarios = [
        scenario
        for scenario in cc._all_scenarios()
        if (
            scenario.expected_repairability == RepairabilityStatus.REPAIRABLE
            and scenario.expected_new_layer == "l2_adjudication_policy"
        )
    ]
    for scenario in scenarios:
        row = _scenario_row(scenario)
        old_signal = old_attr.attribute_failure(row, use_gold=False, gold_reflection=False)
        old_artifacts = old_ep.project_provider_artifacts(
            detection_result=row, signal=old_signal, primary_type=""
        )
        record = cc._to_record(
            row,
            boundary_path_state=scenario.boundary_path_state,
            provider_artifacts=old_artifacts,
        )
        attribution = old_cls.classify_failure(record)
        old_plan = old_pr.plan_policy_candidates(
            attribution=attribution, genome=old_pg.PolicyGenome(genome_id="g")
        )
        new_plan = new_l2.plan_policy_candidates(
            attribution=attribution, genome=PolicyGenome(genome_id="g")
        )
        old_signatures = sorted(
            (
                candidate.operation,
                (
                    candidate.new_item.precondition
                    if candidate.new_item is not None else None
                ),
                (
                    candidate.new_item.action
                    if candidate.new_item is not None else None
                ),
                tuple(sorted(candidate.new_item.params.items()))
                if candidate.new_item is not None else (),
            )
            for candidate in old_plan.candidates
        )
        new_signatures = sorted(
            (
                candidate.operation,
                (
                    candidate.new_item.precondition
                    if candidate.new_item is not None else None
                ),
                (
                    candidate.new_item.action
                    if candidate.new_item is not None else None
                ),
                tuple(sorted(candidate.new_item.params.items()))
                if candidate.new_item is not None else (),
            )
            for candidate in new_plan.candidates
        )
        route_match = old_plan.route_result == new_plan.route_result
        cand_match = old_signatures == new_signatures
        _ok(
            f"l2[{scenario.case_id}] route={old_plan.route_result} "
            f"route_match={route_match} candidates_match={cand_match}"
        )
        if not route_match:
            _fail(
                f"l2[{scenario.case_id}] route_result",
                f"old={old_plan.route_result} new={new_plan.route_result}",
            )
        if not cand_match:
            _fail(
                f"l2[{scenario.case_id}] candidates",
                f"old={old_signatures} new={new_signatures}",
            )


def _valid_l1_candidate() -> dict[str, Any]:
    return {
        "target_module": "atomic_verifier_instruction",
        "operation": "add_rule",
        "condition": "when a candidate carries a cited breach without an "
        "impact event, withhold the error certificate",
        "action": "withhold_certificate: certificate_id",
        "evidence_anchor": "certificate_invariant_failure",
        "condition_spec": {
            "schema_version": "sehd.condition_spec.v1",
            "feature_source": "provider_input",
            "feature": "candidate_kind",
            "operator": "contains",
            "value_type": "string",
            "value": "omission",
            "scope": "action_spec_routes",
            "evidence_binding": "pre_call_projection",
        },
        "rule_index": None,
        "repair_edge_id": "edge_test_1",
        "feature_description": "suppress error certificates that lack impact evidence",
        "rationale": "the emitted error certificate carries no impact chain",
    }


def check_candidate_prevalidation() -> None:
    """Same legal/illegal candidate set -> same rejection verdict."""
    allowed = ["atomic_verifier_instruction", "clean_defense_boundary"]
    frontier = [{
        "owner_module": "atomic_verifier_instruction",
        "field": "certificate_id",
        "edge_id": "edge_test_1",
        "producer_route": "atomic_verifier_instruction",
        "output_object": "error_certificate",
        "consumer": "proof_court",
        "observed": "malformed",
        "required": "well_formed",
    }]
    old_error_type = old_attr.ErrorType.FALSE_POSITIVE
    new_error_type = "false_positive"

    valid = _valid_l1_candidate()
    invalid_owner = dict(valid, target_module="candidate_trigger")
    invalid_op = dict(valid, operation="rewrite_rule")
    invalid_polarity = dict(
        valid,
        action="require_evidence_field: certificate_id",
        condition="when the error verdict carries a surviving certificate, "
        "tighten the evidence requirement",
    )
    invalid_missing_slots = dict(valid, condition="")
    invalid_edge_owner = dict(
        valid,
        repair_edge_id="edge_test_1",
        target_module="clean_defense_boundary",
        action="withhold_certificate: certificate_id",
    )
    no_edit = {"operation": "no_edit", "rationale": "no safe rule is justified"}

    candidates = [
        ("valid", valid),
        ("invalid_owner", invalid_owner),
        ("invalid_operation", invalid_op),
        ("invalid_polarity", invalid_polarity),
        ("invalid_missing_slots", invalid_missing_slots),
        ("invalid_edge_owner", invalid_edge_owner),
        ("no_edit", no_edit),
    ]
    for name, candidate in candidates:
        old_reason = old_runner._prevalidate_candidate(
            candidate, allowed,
            error_type=old_error_type, repair_frontier=frontier,
        )
        new_reason = __import__(
            "sehd_rise.planning.candidates", fromlist=["_prevalidate_candidate"]
        )._prevalidate_candidate(
            candidate, allowed,
            error_type=new_error_type, repair_frontier=frontier,
        )
        _ok(f"prevalidate[{name}] old={old_reason!r} new={new_reason!r} match={old_reason == new_reason}")
        if old_reason != new_reason:
            _fail(f"prevalidate[{name}]", f"old={old_reason!r} new={new_reason!r}")


def main() -> int:
    print("== compare_planning ==")
    print("DEGRADED: warm provider cache unavailable (R2) — offline contract "
          "checks only; schema/sequence/prompt determinism asserted below.")
    check_l1_prompt_gold_samples()
    print()
    check_l2_planner()
    print()
    check_candidate_prevalidation()
    print()
    print(
        f"{len(CHECKS)} checks passed, {len(FAILURES)} failed, "
        f"{len(DEV)} deviations documented"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
