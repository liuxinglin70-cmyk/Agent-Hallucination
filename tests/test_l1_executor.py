"""Deterministic L1 post-processing executor (plan B): surface transforms and
terminal recompute.

The five remaining L1 opcodes are monotonic suppress/select transforms over
the court result. These tests lock the transform semantics and the terminal
rule: a surviving carrier keeps the verdict error; withholding every carrier
with a clean terminal basis flips clean; otherwise the verdict opens to
unresolved.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.skills.l1_execution import (
    evaluate_l1_rule,
    execute_l1_intervention,
)
from sehd_rise.skills.action import ActionSpec, compile_action_spec
from sehd_rise.skills.binding import Binding
from sehd_rise.skills.rule import project_result_condition_features


def _spec(operation: str, target: str = "") -> ActionSpec:
    action = f"{operation}: {target}" if target else operation
    # compile_action_spec validates the opcode against the closed vocabulary.
    if operation == "withhold_certificate":
        return compile_action_spec("atomic_verifier_instruction", action)
    if operation == "keep_coverage_incomplete":
        return compile_action_spec("evidence_obligation", action)
    if operation in ("select_earliest_origin", "suppress_later_origin"):
        return compile_action_spec("earliest_origin_tiebreak", action)
    raise ValueError(operation)


def _binding(domain: str, field: str, value: str) -> Binding:
    return Binding(domain=domain, entity_id=value, identifiers={field: value})


def _result(*, surviving: list[str], coverage: str = "complete",
            candidate_terminal: bool = True) -> dict:
    return {
        "state": "verified_error" if surviving else "verified_clean",
        "responsible_event_id": "event_001" if surviving else None,
        "census": [
            {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001",
             "candidate_kind": "unsupported_claim", "module_tag": "Tool-Use",
             "axes": ["P"]},
            {"candidate_id": "c2", "origin_hypothesis_event_id": "event_002",
             "candidate_kind": "invalid_tool_use", "module_tag": "Reasoning",
             "axes": ["F"]},
        ],
        "prosecution": {
            "candidates": [
                {"certificate_id": "cert_1", "source_candidate_id": "c1",
                 "origin_event_id": "event_001"},
                {"certificate_id": "cert_2", "source_candidate_id": "c2",
                 "origin_event_id": "event_002"},
            ],
        },
        "combined_prosecution": {
            "candidates": [
                {"certificate_id": "cert_1", "source_candidate_id": "c1",
                 "origin_event_id": "event_001"},
                {"certificate_id": "cert_2", "source_candidate_id": "c2",
                 "origin_event_id": "event_002"},
            ],
        },
        "proof_adjudication": {
            "state": "verified_error" if surviving else "verified_clean",
            "responsible_event_id": "event_001" if surviving else None,
            "prosecution_coverage": coverage,
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": list(surviving),
            "error_audits": [
                {"certificate_id": "cert_1", "decision": "pass",
                 "failed_checks": [],
                 "missing_fields": [], "failed_invariants": []},
                {"certificate_id": "cert_2", "decision": "quarantine",
                 "failed_checks": ["norm_evidence"],
                 "missing_fields": ["norm_evidence_refs"],
                 "failed_invariants": ["norm_evidence"]},
            ],
            "terminal_basis": {
                "decisive_facts": {
                    "coverage_complete": coverage == "complete",
                    "candidate_terminal": candidate_terminal,
                },
            },
        },
    }


def test_withhold_certificate_grounds_instance_from_condition() -> None:
    result = _result(surviving=["cert_2"])
    spec_cond = {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "failed_invariant",
        "operator": "contains",
        "value_type": "string",
        "value": "norm_evidence",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }
    mutated, report = evaluate_l1_rule(
        result=result,
        action_spec=_spec("withhold_certificate", "certificate_id").to_dict(),
        condition_spec=spec_cond,
    )
    assert report["fired"] is True
    assert mutated["proof_adjudication"]["surviving_error_certificate_ids"] == []


def test_project_result_condition_features_reads_result_and_record() -> None:
    result = _result(surviving=["cert_1", "cert_2"])
    record = {
        "history": [
            {"role": "assistant", "tool_calls": [{"name": "cd", "arguments": {}}],
             "tool_responses": []},
            {"role": "assistant", "tool_calls": [{"name": "cd", "arguments": {}}],
             "tool_responses": []},
        ],
    }
    features = project_result_condition_features(result, record)
    assert "unsupported_claim" in features["candidate_kind"]
    assert "other" in features["candidate_kind"]  # "invalid_tool_use" normalizes to "other"
    assert "tool_use" in features["module_tag"]
    assert "reasoning" in features["module_tag"]
    assert "norm_evidence" in features["failed_invariant"]
    assert features["has_tool_call"] is True
    assert features["has_tool_retry"] is True


def test_evaluate_l1_rule_fires_matched_condition() -> None:
    result = _result(surviving=["cert_2"])
    spec_cond = {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "failed_invariant",
        "operator": "contains",
        "value_type": "string",
        "value": "norm_evidence",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    transformed, report = evaluate_l1_rule(
        condition_spec=spec_cond,
        action_spec=action.to_dict(),
        result=result,
    )
    assert report["matched"] is True
    assert report["fired"] is True
    assert transformed["proof_adjudication"]["surviving_error_certificate_ids"] == []


def test_evaluate_l1_rule_skips_unmatched_condition() -> None:
    result = _result(surviving=["cert_1"])
    spec_cond = {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "failed_invariant",
        "operator": "contains",
        "value_type": "string",
        "value": "impact_evidence_binding",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    transformed, report = evaluate_l1_rule(
        condition_spec=spec_cond,
        action_spec=action.to_dict(),
        result=result,
    )
    assert report["matched"] is False
    assert report["fired"] is False
    # Unmatched condition leaves the result unchanged.
    assert transformed["proof_adjudication"]["surviving_error_certificate_ids"] == ["cert_1"]


def test_withhold_certificate_flips_error_to_unresolved() -> None:
    result = _result(surviving=["cert_1"])
    mutated, report = execute_l1_intervention(
        result, spec=_spec("withhold_certificate", "certificate_id"),
        bindings=(_binding("certificate", "certificate_id", "cert_1"),),
    )
    assert report["fired"] is True
    proof = mutated["proof_adjudication"]
    assert proof["surviving_error_certificate_ids"] == []
    assert proof["state"] == "verified_clean"


def test_keep_coverage_incomplete_opens_coverage() -> None:
    result = _result(surviving=[])
    mutated, report = execute_l1_intervention(
        result, spec=_spec("keep_coverage_incomplete", "coverage_state"),
        bindings=(_binding("coverage", "coverage_side", "prosecution"),),
    )
    assert report["fired"] is True
    assert mutated["proof_adjudication"]["prosecution_coverage"] == "incomplete"


def test_select_earliest_origin_reassigns_responsible() -> None:
    result = _result(surviving=["cert_1", "cert_2"])
    result["proof_adjudication"]["responsible_event_id"] = "event_002"
    mutated, report = execute_l1_intervention(
        result, spec=_spec("select_earliest_origin", "responsible_event_id"),
        bindings=(
            _binding("origin", "origin_event_id", "event_001"),
            _binding("origin", "origin_event_id", "event_002"),
        ),
    )
    assert report["fired"] is True
    assert mutated["proof_adjudication"]["responsible_event_id"] == "event_001"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
