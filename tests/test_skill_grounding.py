"""Phase 2A mechanical acceptance for entity-level Skill grounding."""

from __future__ import annotations

from sehd_rise.skills.action import compile_action_spec
from sehd_rise.skills.binding import (
    BINDING_AMBIGUOUS,
    BINDING_BOUND,
    BINDING_DOMAIN_MISMATCH,
    BINDING_NOT_FOUND,
    Binding,
    match_condition_bindings,
    project_binding_universe,
)
from sehd_rise.skills.l1_execution import (
    evaluate_l1_rule,
    execute_l1_intervention,
)
from sehd_rise.planning.candidates import _prevalidate_candidate


def _atom(feature: str, value, *, value_type: str = "string") -> dict:
    operator = "equals" if value_type == "boolean" else "contains"
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": feature,
        "operator": operator,
        "value_type": value_type,
        "value": value,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _combine(kind: str, *children: dict) -> dict:
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "",
        "operator": "",
        "value_type": "string",
        "value": "",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": kind,
        "children": list(children),
        "binding": "entity_scoped",
    }


def _result(*, same_entity: bool = False, two_matching_certificates: bool = False) -> dict:
    census = [
        {
            "candidate_id": "C1",
            "candidate_kind": "unsupported_claim",
            "module_tag": "tool_use" if not same_entity else "reasoning",
            "origin_hypothesis_event_id": "E1",
            "axes": ["F"],
        },
        {
            "candidate_id": "C2",
            "candidate_kind": "unsupported_claim" if same_entity else "contradiction",
            "module_tag": "reasoning" if not same_entity else "tool_use",
            "origin_hypothesis_event_id": "E2",
            "axes": ["F"],
        },
    ]
    certificates = [
        {
            "certificate_id": "C7",
            "source_candidate_id": "C1",
            "origin_event_id": "E1",
            "evidence_refs": ["E1"],
        },
    ]
    audits = [{
        "certificate_id": "C7",
        "decision": "pass",
        "missing_fields": [],
        "failed_invariants": ["norm_evidence"],
    }]
    surviving = ["C7"]
    if two_matching_certificates:
        certificates.append({
            "certificate_id": "C8",
            "source_candidate_id": "C2",
            "origin_event_id": "E2",
        })
        audits.append({
            "certificate_id": "C8",
            "decision": "pass",
            "missing_fields": [],
            "failed_invariants": ["norm_evidence"],
        })
        surviving.append("C8")
    return {
        "state": "verified_error",
        "census": census,
        "prosecution": {"candidates": certificates},
        "combined_prosecution": {"candidates": certificates},
        "proof_adjudication": {
            "state": "verified_error",
            "responsible_event_id": "E1",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": surviving,
            "error_audits": audits,
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }


def test_all_of_joins_only_the_same_candidate() -> None:
    condition = _combine(
        "all_of",
        _atom("candidate_kind", "unsupported_claim"),
        _atom("module_tag", "reasoning"),
    )
    split = match_condition_bindings(
        condition, _result(same_entity=False),
        domain="candidate", cardinality="one_or_many",
    )
    assert split.status == BINDING_NOT_FOUND

    same = match_condition_bindings(
        condition, _result(same_entity=True),
        domain="candidate", cardinality="one_or_many",
    )
    assert same.status == BINDING_BOUND
    assert [binding.entity_id for binding in same.bindings] == ["C1"]


def test_not_is_an_entity_domain_anti_join() -> None:
    # C1 owns surviving certificate C7; C2 has no verified evidence.
    condition = _combine(
        "all_of",
        _atom("candidate_kind", "unsupported_claim"),
        _combine("not", _atom("evidence_verified", True, value_type="boolean")),
    )
    match = match_condition_bindings(
        condition, _result(same_entity=True),
        domain="candidate", cardinality="one_or_many",
    )
    assert match.status == BINDING_BOUND
    assert [binding.entity_id for binding in match.bindings] == ["C2"]


def test_capability_rejects_wrong_binding_domain_before_mutation() -> None:
    result = _result()
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    wrong = Binding(
        domain="candidate", entity_id="C1", identifiers={"candidate_id": "C1"},
    )
    transformed, report = execute_l1_intervention(
        result, spec=action, bindings=(wrong,),
    )
    assert report["binding_status"] == BINDING_DOMAIN_MISMATCH
    assert report["fired"] is False
    assert transformed["proof_adjudication"]["surviving_error_certificate_ids"] == ["C7"]


def test_source_certificate_binding_executes_and_clears_edge() -> None:
    result = _result()
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    transformed, report = evaluate_l1_rule(
        condition_spec=_atom("failed_invariant", "norm_evidence"),
        action_spec=action.to_dict(),
        result=result,
    )
    assert report["binding_status"] == BINDING_BOUND
    assert report["binding_proof"]["bindings"][0]["entity_id"] == "C7"
    assert report["fired"] is True
    assert transformed["proof_adjudication"]["surviving_error_certificate_ids"] == []
    assert transformed["proof_adjudication"]["state"] == "verified_clean"


def test_missing_target_is_binding_not_found_and_never_executes() -> None:
    result = _result()
    result["prosecution"] = {"candidates": []}
    result["combined_prosecution"] = {"candidates": []}
    result["proof_adjudication"]["error_audits"] = []
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    transformed, report = evaluate_l1_rule(
        condition_spec=_atom("candidate_kind", "unsupported_claim"),
        action_spec=action.to_dict(),
        result=result,
    )
    assert report["binding_status"] == BINDING_NOT_FOUND
    assert report["fired"] is False
    assert transformed == result


def test_exactly_one_contract_reports_ambiguous_binding() -> None:
    action = compile_action_spec(
        "atomic_verifier_instruction", "withhold_certificate: certificate_id",
    )
    _transformed, report = evaluate_l1_rule(
        condition_spec=_atom("failed_invariant", "norm_evidence"),
        action_spec=action.to_dict(),
        result=_result(two_matching_certificates=True),
    )
    assert report["binding_status"] == BINDING_AMBIGUOUS
    assert report["fired"] is False


def test_source_prevalidation_persists_binding_proof() -> None:
    candidate = {
        "target_module": "atomic_verifier_instruction",
        "operation": "add_rule",
        "condition": "a certificate fails the norm evidence invariant",
        "action": "withhold_certificate: certificate_id",
        "evidence_anchor": "unsupported certificate caused the verdict",
        "condition_spec": _atom("failed_invariant", "norm_evidence"),
    }
    reason = _prevalidate_candidate(
        candidate,
        ("atomic_verifier_instruction",),
        source_result=_result(),
    )
    assert reason is None
    assert candidate["binding_proof"]["status"] == BINDING_BOUND
    assert candidate["binding_proof"]["bindings"][0]["entity_id"] == "C7"


def test_audit_features_key_by_certificate_id_across_focused_and_combined_sets() -> None:
    """Appeal renumbering (initial_error_NNN / appeal_error_NNN) must not
    shift audits onto the wrong certificate: the audit map is keyed by each
    audit's own certificate id, not by a positional zip against the focused
    prosecution subset."""
    census = [
        {
            "candidate_id": "C1", "candidate_kind": "unsupported_claim",
            "module_tag": "reasoning", "origin_hypothesis_event_id": "E1",
            "axes": ["F"],
        },
        {
            "candidate_id": "C2", "candidate_kind": "contradiction",
            "module_tag": "tool_use", "origin_hypothesis_event_id": "E2",
            "axes": ["F"],
        },
    ]
    focused = [
        {
            "certificate_id": "focused_error_001", "source_candidate_id": "C1",
            "origin_event_id": "E1",
        },
    ]
    combined = [
        {
            "certificate_id": "appeal_error_001", "source_candidate_id": "C2",
            "origin_event_id": "E2",
        },
        {
            "certificate_id": "initial_error_001", "source_candidate_id": "C1",
            "origin_event_id": "E1",
        },
    ]
    result = {
        "state": "verified_error",
        "census": census,
        "prosecution": {"candidates": focused},
        "combined_prosecution": {"candidates": combined},
        "proof_adjudication": {
            "state": "verified_error",
            "responsible_event_id": "E1",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": ["initial_error_001"],
            "error_audits": [
                {
                    "certificate_id": "appeal_error_001",
                    "decision": "quarantine",
                    "missing_fields": ["norm_evidence_refs"],
                    "failed_invariants": [],
                },
                {
                    "certificate_id": "initial_error_001",
                    "decision": "pass",
                    "missing_fields": [],
                    "failed_invariants": ["norm_evidence"],
                },
            ],
            "terminal_basis": {"decisive_facts": {
                "coverage_complete": True,
                "candidate_terminal": True,
            }},
        },
    }
    bindings = {
        binding.entity_id: binding
        for binding in project_binding_universe(result, domain="certificate")
    }
    initial = bindings["initial_error_001"]
    assert "norm_evidence" in initial.features["failed_invariant"]
    assert initial.features["missing_field"] == []
    assert initial.features["evidence_verified"] is True
    appeal = bindings["appeal_error_001"]
    assert appeal.features["missing_field"] == ["norm_evidence_refs"]
    assert appeal.features["failed_invariant"] == []
    assert appeal.features["evidence_verified"] is False
    candidate_bindings = {
        binding.entity_id: binding
        for binding in project_binding_universe(result, domain="candidate")
    }
    assert candidate_bindings["C1"].features["evidence_verified"] is True
    assert candidate_bindings["C2"].features["evidence_verified"] is False
