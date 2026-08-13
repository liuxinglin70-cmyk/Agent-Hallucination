"""Phase 2B mechanical acceptance for constructive recall closure."""

from __future__ import annotations

import copy
import re

import pytest

from sehd_rise.detection import continuation
from sehd_rise.detection.court import engine as court_engine
from sehd_rise.detection.continuation import (
    CONSTRUCTED_CANDIDATE_DUPLICATE,
    CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE,
    CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
    CONSTRUCTION_COMPLETED,
    execute_constructive_rules,
    resume_court_from_candidate,
)
from sehd_rise.skills.action import compile_action_spec
from sehd_rise.skills.anchors import project_claim_anchors
from sehd_rise.skills.binding import BINDING_BOUND, match_condition_bindings


def _record() -> dict:
    return {
        "history": [
            {"role": "user", "content": "Check the release date."},
            {"role": "assistant", "content": "The package was released on March 3."},
            {"role": "assistant", "content": "I have completed the check."},
        ],
    }


def _baseline() -> dict:
    return {
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "state": "verified_clean",
        "census": [],
        "qualifications": [],
        "propagations": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "combined_prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "complete", "candidates": []},
        "providers": {},
        "proof_adjudication": {
            "state": "verified_clean",
            "decision_authority": "authoritative",
            "responsible_event_id": None,
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": [],
            "error_audits": [],
        },
    }


def _condition() -> dict:
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "anchor_has_number",
        "operator": "equals",
        "value_type": "boolean",
        "value": True,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _rule() -> tuple[str, str, dict, dict, str]:
    action = compile_action_spec(
        "candidate_trigger", "construct_candidate: anchor_id",
    )
    return "candidate_trigger", "rule_construct", _condition(), action.to_dict(), ""


def _provider(*, purpose: str, **_kwargs):
    if purpose == "detector:qualification":
        return ({
            "coverage_state": "complete",
            "candidates": [{
                "candidate_id": "skill_candidate_placeholder",
                "violation_state": "verified_violation",
                "actor_state": "agent_caused",
                "boundary_state": "cleared",
                "positive_evidence_refs": ["event_002"],
                "actor_evidence_refs": ["event_002"],
                "boundary_evidence_refs": [],
                "rationale": "The assistant authored the factual release claim.",
            }],
        }, {"purpose": purpose})
    raise AssertionError(purpose)


def _provider_for_candidate(
    candidate_id: str, *, emit_error: bool = True, origin_event_id: str = "event_002",
):
    calls: list[str] = []

    def provider(*, purpose: str, **_kwargs):
        calls.append(purpose)
        origin_index = int(origin_event_id.rsplit("_", 1)[1])
        impact_event_id = f"event_{origin_index + 1:03d}"
        if purpose == "detector:qualification":
            value = {
                "coverage_state": "complete",
                "candidates": [{
                    "candidate_id": candidate_id,
                    "violation_state": "verified_violation",
                    "actor_state": "agent_caused",
                    "boundary_state": "cleared",
                    "positive_evidence_refs": [origin_event_id],
                    "actor_evidence_refs": [origin_event_id],
                    "boundary_evidence_refs": [],
                    "rationale": "The assistant authored the unsupported release claim.",
                }],
            }
        elif purpose == "detector:propagation":
            value = {
                "coverage_state": "complete",
                "candidates": [{
                    "candidate_id": candidate_id,
                    "propagation_state": "unrecovered_impact",
                    "residual_impact": True,
                    "consumption_event_ids": [origin_event_id],
                    "consumption_evidence_refs": [origin_event_id],
                    "impact_event_ids": [impact_event_id],
                    "impact_evidence_refs": [impact_event_id],
                    "recovery_event_id": None,
                    "recovery_evidence_refs": [],
                    "impact_surfaces": ["A"],
                    "rationale": "The unsupported claim remained in the produced result.",
                }],
            }
        elif purpose == "detector:focused_prosecution":
            certificates = []
            if emit_error:
                # The schema example id is a literal, so providers echo
                # the identical id for every candidate.
                certificates = [{
                    "certificate_id": "focused_error_001",
                    "source_candidate_id": candidate_id,
                    "origin_event_id": origin_event_id,
                    "claim_kind": "agent_assertion",
                    "norm_source": "prior_trajectory_evidence",
                    "norm_evidence_refs": ["event_001"],
                    "breach_type": "fabrication",
                    "violation_evidence_refs": [origin_event_id],
                    "agent_control_event_id": origin_event_id,
                    "agent_control_evidence_refs": [origin_event_id],
                    "impact_kind": "final_answer",
                    "impact_event_ids": [impact_event_id],
                    "impact_evidence_refs": [impact_event_id],
                    "recovery_state": "none",
                    "recovery_evidence_refs": [],
                    "benchmark_causal_status": "causes_incorrect_result",
                    "benchmark_causal_evidence_refs": [impact_event_id],
                    "rationale": (
                        "NORM: The release date requires support from the supplied task. "
                        "ORIGIN: The assistant introduced March 3 at event two. "
                        "CONTROL: The assistant directly authored the unsupported assertion. "
                        "PROPAGATION: The assertion remained through the subsequent response. "
                        "IMPACT: The produced answer contains the unsupported release date. "
                        "COUNTERFACTUAL: Removing that assertion prevents the incorrect result."
                    ),
                }]
            value = {"coverage_state": "complete", "candidates": certificates}
        elif purpose == "detector:defense":
            value = {
                "coverage_state": "complete",
                "candidates": [{
                    "certificate_id": "focused_defense_001",
                    "target_candidate_id": candidate_id,
                    "target_origin_event_id": origin_event_id,
                    "defense_type": "insufficient_evidence",
                    "evidence_refs": [origin_event_id],
                    "rationale": "No addressable evidence proves a clean boundary.",
                }],
            }
        else:
            raise AssertionError(purpose)
        return value, {"purpose": purpose}

    return provider, calls


_CANDIDATE_ID_PATTERN = re.compile(r'"candidate_id"\s*:\s*"([^"]+)"')
_ORIGIN_PATTERN = re.compile(r'"origin_hypothesis_event_id"\s*:\s*"([^"]+)"')


def _provider_for_candidates(
    candidate_ids: list[str], *, explode_on: str = "",
) -> tuple[callable, list[str]]:
    """Serve every candidate with the same literal provider ids, routing by
    the candidate id and origin embedded in the call's user text."""
    calls: list[str] = []

    def provider(*, purpose: str, user: str = "", **_kwargs):
        match = _CANDIDATE_ID_PATTERN.search(user or "")
        active = match.group(1) if match else candidate_ids[0]
        origin_match = _ORIGIN_PATTERN.search(user or "")
        origin = origin_match.group(1) if origin_match else "event_002"
        calls.append(f"{purpose}:{active}")
        if active == explode_on and purpose == "detector:focused_prosecution":
            raise RuntimeError("provider failure for one candidate")
        return _provider_for_candidate(
            active, origin_event_id=origin,
        )[0](purpose=purpose)

    return provider, calls


def test_claim_anchor_projection_is_deterministic() -> None:
    first = project_claim_anchors(_record())
    second = project_claim_anchors(copy.deepcopy(_record()))
    assert [anchor.to_dict() for anchor in first] == [anchor.to_dict() for anchor in second]
    assert first[0].anchor_id.startswith("anchor_")
    assert first[0].span_text == "The package was released on March 3."


def test_claim_anchor_gold_firewall() -> None:
    contaminated = {**_record(), "gold_label": True, "hallucination_step": 2}
    with pytest.raises(ValueError, match="label-free"):
        project_claim_anchors(contaminated)
    nested = {**_record(), "metadata": {"gold_steps": [2]}}
    with pytest.raises(ValueError, match="label-free"):
        project_claim_anchors(nested)
    payload = project_claim_anchors(_record())[0].to_dict()
    assert "gold_label" not in str(payload)
    assert "hallucination_step" not in str(payload)


def test_missing_candidate_condition_binds_exactly_one_claim_anchor() -> None:
    match = match_condition_bindings(
        _condition(), _baseline(), domain="claim_anchor",
        cardinality="exactly_one", record=_record(),
    )
    assert match.status == BINDING_BOUND
    assert match.bindings[0].identifiers["source_event_id"] == "event_002"


def test_constructive_continuation_materializes_and_verifies(monkeypatch) -> None:
    anchor = project_claim_anchors(_record())[0]
    # The constructor hashes canonical JSON strings, so obtain the actual id
    # from a first rules call by installing a provider keyed by the bound id.
    expected = continuation._candidate_from_anchor(anchor)
    provider, calls = _provider_for_candidate(expected["candidate_id"])
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    result, execution = execute_constructive_rules(
        _baseline(), [_rule()], record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    proof = execution["constructive_execution_proofs"][0]
    assert proof["status"] == CONSTRUCTION_COMPLETED
    assert proof["candidate_anchor_id"] == proof["source_anchor_id"]
    assert proof["constructed_candidate_id"] == expected["candidate_id"]
    assert proof["certificate_ids_created"] == ["constructed_error_001"]
    assert proof["terminal_before"] == "verified_clean"
    assert proof["terminal_after"] == "verified_error"
    assert result["state"] == "verified_error"
    assert calls == [
        "detector:qualification", "detector:propagation",
        "detector:focused_prosecution", "detector:defense",
    ]


def test_verified_clean_construction_does_not_fabricate_certificate(monkeypatch) -> None:
    anchor = project_claim_anchors(_record())[0]
    candidate = continuation._candidate_from_anchor(anchor)
    provider, _calls = _provider_for_candidate(candidate["candidate_id"], emit_error=False)
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    result, proof = resume_court_from_candidate(
        _baseline(), candidate=candidate, record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    assert proof.status == CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE
    assert proof.verifier_result == "verified_non_error"
    assert proof.certificate_ids_created == ()
    assert result["state"] == "verified_clean"


def test_local_continuation_preserves_unrelated_candidate_census(monkeypatch) -> None:
    anchor = project_claim_anchors(_record())[0]
    candidate = continuation._candidate_from_anchor(anchor)
    provider, calls = _provider_for_candidate(
        candidate["candidate_id"], emit_error=False,
    )
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    baseline = _baseline()
    unrelated = {
        "candidate_id": "candidate_unrelated_001",
        "origin_hypothesis_event_id": "event_003",
        "claim_or_commitment": "I have completed the check.",
    }
    baseline["census"] = [unrelated]

    result, proof = resume_court_from_candidate(
        baseline, candidate=candidate, record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
    )

    assert proof.status == CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE
    assert result["census"][0] == unrelated
    assert result["census"][1]["candidate_id"] == candidate["candidate_id"]
    assert calls == [
        "detector:qualification", "detector:propagation",
        "detector:focused_prosecution",
    ]


def test_construct_candidate_is_idempotent(monkeypatch) -> None:
    anchor = project_claim_anchors(_record())[0]
    candidate = continuation._candidate_from_anchor(anchor)
    provider, _calls = _provider_for_candidate(candidate["candidate_id"])
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    first, proof = resume_court_from_candidate(
        _baseline(), candidate=candidate, record=_record(), config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
    )
    assert proof.status == CONSTRUCTION_COMPLETED
    second, duplicate = resume_court_from_candidate(
        first, candidate=candidate, record=_record(), config={},
    )
    assert duplicate.status == CONSTRUCTED_CANDIDATE_DUPLICATE
    assert len(second["skill_constructed_candidates"]) == 1


def _numeric_record() -> dict:
    return {
        "history": [
            {"role": "user", "content": "List some figures."},
            {"role": "assistant", "content": "The rate is 17.4% in 2022."},
            {"role": "assistant", "content": "It launched on August 8."},
            {"role": "assistant", "content": "Revenue grew 3 times."},
        ],
    }


def test_construct_candidate_generalizes_over_many_matching_anchors(monkeypatch) -> None:
    """A one_or_many construct_candidate builds every matched anchor.

    The generalization predicate (``anchor_has_number``) matches every numeric
    claim, so the executor must construct each anchor instead of raising
    BINDING_AMBIGUOUS or silently taking only the first match.
    """
    record = _numeric_record()
    anchors = project_claim_anchors(record)
    numeric = [a for a in anchors if a.observable_features["anchor_has_number"]]
    assert len(numeric) >= 2

    match = match_condition_bindings(
        _condition(), _baseline(), domain="claim_anchor",
        cardinality="one_or_many", record=record,
    )
    assert match.status == BINDING_BOUND
    assert len(match.bindings) == len(numeric)

    constructed: list[str] = []

    def fake_resume(result, *, candidate, record, config, provider_usage_ledger=None):
        constructed.append(candidate["claim_anchor_id"])
        merged = copy.deepcopy(dict(result))
        merged["census"] = [*merged.get("census", []), dict(candidate)]
        return merged, continuation._proof(
            anchor_id=candidate["claim_anchor_id"],
            before=str(merged.get("state") or ""),
            candidate=candidate,
            status=CONSTRUCTION_COMPLETED,
        )

    monkeypatch.setattr(continuation, "resume_court_from_candidate", fake_resume)
    result, execution = execute_constructive_rules(
        _baseline(), [_rule()], record=record, config={},
    )
    assert len(constructed) == len(numeric)
    assert set(constructed) == {anchor.anchor_id for anchor in numeric}
    assert {row["entity_id"] for row in execution["fired"]} == {
        anchor.anchor_id for anchor in numeric
    }
    assert len(execution["constructive_execution_proofs"]) == len(numeric)


def test_construct_candidate_is_bounded_by_max_matches(monkeypatch) -> None:
    record = _numeric_record()
    numeric = [a for a in project_claim_anchors(record) if a.observable_features["anchor_has_number"]]
    constructed: list[str] = []

    def fake_resume(result, *, candidate, record, config, provider_usage_ledger=None):
        constructed.append(candidate["claim_anchor_id"])
        merged = copy.deepcopy(dict(result))
        merged["census"] = [*merged.get("census", []), dict(candidate)]
        return merged, continuation._proof(
            anchor_id=candidate["claim_anchor_id"],
            before=str(merged.get("state") or ""),
            candidate=candidate,
            status=CONSTRUCTION_COMPLETED,
        )

    monkeypatch.setattr(continuation, "resume_court_from_candidate", fake_resume)
    _result, execution = execute_constructive_rules(
        _baseline(), [_rule()], record=record, config={},
        max_matches_per_rule=1,
    )
    assert len(constructed) == 1
    assert len(execution["fired"]) == 1
    assert constructed[0] in {anchor.anchor_id for anchor in numeric}


def test_one_or_many_continuation_renumbers_provider_literal_ids(monkeypatch) -> None:
    """Two constructed anchors both pass: the same provider-literal
    certificate id is renumbered into the continuation namespace instead of
    colliding in the merged court document."""
    record = _numeric_record()
    anchors = [
        a for a in project_claim_anchors(record)
        if a.observable_features["anchor_has_number"]
    ]
    assert len(anchors) >= 3
    candidate_ids = [
        continuation._candidate_from_anchor(anchor)["candidate_id"]
        for anchor in anchors
    ]
    provider, calls = _provider_for_candidates(candidate_ids)
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    result, execution = execute_constructive_rules(
        _baseline(), [_rule()], record=record, config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
        max_matches_per_rule=2,
    )
    statuses = [proof["status"] for proof in execution["constructive_execution_proofs"]]
    assert statuses == [CONSTRUCTION_COMPLETED, CONSTRUCTION_COMPLETED]
    assert execution["terminal_state"] == "verified_error"
    assert any(call.startswith("detector:focused_prosecution") for call in calls)
    certificate_ids = [
        str(row["certificate_id"])
        for row in result["combined_prosecution"]["candidates"]
    ]
    assert certificate_ids == ["constructed_error_001", "constructed_error_002"]
    defense_ids = [
        str(row["certificate_id"]) for row in result["defense"]["candidates"]
    ]
    assert len(defense_ids) == len(set(defense_ids)) == 2


def test_candidate_level_exception_skips_binding_without_failing_case(monkeypatch) -> None:
    """A candidate-level adjudication failure is recorded per binding and
    never aborts the remaining constructive run."""
    record = _numeric_record()
    anchors = [
        a for a in project_claim_anchors(record)
        if a.observable_features["anchor_has_number"]
    ]
    candidates = [continuation._candidate_from_anchor(anchor) for anchor in anchors]
    provider, _calls = _provider_for_candidates(
        [candidate["candidate_id"] for candidate in candidates],
    )
    monkeypatch.setattr(continuation, "detection_call", provider)
    monkeypatch.setattr(court_engine, "detection_call", provider)
    real_resume = continuation.resume_court_from_candidate
    failing_anchor = anchors[1].anchor_id

    def flaky_resume(result, *, candidate, record, config, provider_usage_ledger=None):
        if candidate["claim_anchor_id"] == failing_anchor:
            raise ValueError("certificate ids must be present and unique")
        return real_resume(
            result, candidate=candidate, record=record, config=config,
            provider_usage_ledger=provider_usage_ledger,
        )

    monkeypatch.setattr(continuation, "resume_court_from_candidate", flaky_resume)
    result, execution = execute_constructive_rules(
        _baseline(), [_rule()], record=record, config={
            "qualification_route": {}, "propagation_route": {},
            "focused_prosecution_route": {}, "defense_route": {},
            "focused_prosecution_retries": 0,
        },
        max_matches_per_rule=2,
    )
    statuses = [proof["status"] for proof in execution["constructive_execution_proofs"]]
    assert statuses == [CONSTRUCTION_COMPLETED, CONSTRUCTED_CANDIDATE_NOT_VERIFIED]
    failed_row = execution["fired"][1]
    assert failed_row["entity_id"] == failing_anchor
    assert failed_row["construction_status"] == CONSTRUCTED_CANDIDATE_NOT_VERIFIED
    assert "certificate ids must be present and unique" in (
        failed_row["constructive_execution_proof"]["verifier_result"]
    )
    assert len(result["combined_prosecution"]["candidates"]) == 1
    assert result["state"] == "verified_error"
