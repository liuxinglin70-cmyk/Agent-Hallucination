"""B8 P2 misc acceptance: boundary key alignment, court candidate partition
ids, leakage rejection feedback mapping, and unreachable-template removal."""

from __future__ import annotations

from sehd_rise.detection.court.contract import adjudicate_proof_court
from sehd_rise.evaluation.promotion import (
    HARD_GOLD_OR_IDENTITY_LEAKAGE,
    classify_reason_codes,
)
from sehd_rise.loop.ledger import GateEvent, _fuzzy_codes_for
from sehd_rise.meta.review import FEEDBACK_UNSAFE_OR_UNREPRESENTABLE
from sehd_rise.planning.l2 import plan_policy_candidates
from sehd_rise.skills.binding import project_binding_universe
from sehd_rise.skills.policy import PolicyGenome
from sehd_rise.skills.vocabulary.contracts import LAYER_L2


def _packet() -> dict:
    return {
        "schema_version": "agenthallu.ocse_packet.v1",
        "question": "q",
        "events": [{"event_id": "event_001", "position": 1, "tool_interactions": []}],
    }


def _error_cert(cert_id: str) -> dict:
    return {
        "certificate_id": cert_id,
        "source_candidate_id": "c1",
        "origin_event_id": "event_001",
        "claim_kind": "agent_assertion",
        "norm_source": "tool_contract",
        "norm_evidence_refs": ["event_001"],
        "breach_type": "invalid_action",
        "violation_evidence_refs": ["event_001"],
        "agent_control_event_id": "event_001",
        "agent_control_evidence_refs": ["event_001"],
        "impact_kind": "final_answer",
        "impact_event_ids": ["event_001"],
        "impact_evidence_refs": ["event_001"],
        "recovery_state": "none",
        "recovery_evidence_refs": [],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": ["event_001"],
        "rationale": (
            "NORM: ... | ORIGIN: ... | CONTROL: ... | PROPAGATION: ... | "
            "IMPACT: ... | COUNTERFACTUAL: ..."
        ),
    }


def test_boundary_domain_binds_the_court_boundary_path() -> None:
    result = {
        "boundary_path": {
            "state": "verified_clean",
            "responsible_event_id": "event_009",
            "evidence_refs": ["event_009"],
            "rationale": "no incident occurred",
            "contract_validation": {"decision": "pass", "failed_checks": []},
        },
    }
    bindings = project_binding_universe(result, domain="boundary")
    assert len(bindings) == 1
    assert bindings[0].identifiers["boundary_id"] == "event_009"
    assert bindings[0].features["state"] == "verified_clean"
    # A result carrying only the retired clean_boundary key binds nothing.
    retired_key = {"clean_boundary": {"state": "verified_clean"}}
    assert project_binding_universe(retired_key, domain="boundary") == ()


def test_court_produces_candidate_partition_ids() -> None:
    result = adjudicate_proof_court(
        required_candidates=[
            {"candidate_id": "c1", "origin_hypothesis_event_id": "event_001"},
            {"candidate_id": "c2", "origin_hypothesis_event_id": "event_001"},
        ],
        prosecution={
            "coverage_state": "complete",
            "candidates": [_error_cert("e1")],
        },
        defense={
            "coverage_state": "complete",
            "candidates": [{
                "certificate_id": "d1",
                "target_candidate_id": "c2",
                "target_origin_event_id": "event_001",
                "defense_type": "explicitly_authorized",
                "evidence_refs": ["event_001"],
                "rationale": "the action was explicitly authorized",
            }],
        },
        packet=_packet(),
    )
    assert result["state"] == "verified_error"
    assert result["positive_candidate_ids"] == ["c1"]
    assert result["clean_covered_candidate_ids"] == ["c2"]


def test_leakage_rejection_maps_to_hard_code_and_feedback() -> None:
    hard, quality = classify_reason_codes(
        ["label_or_case_leakage: gold_label"],
    )
    assert hard == (HARD_GOLD_OR_IDENTITY_LEAKAGE,)
    assert quality == ()
    event = GateEvent(
        schema_version="sehd.gate_event.v1",
        event_id="event_leak",
        subject_id="subject_1",
        episode_id="episode_1",
        attempt_id="",
        layer="l1_provider_rule",
        artifact="candidate_trigger",
        operation="add_rule",
        stage="structural_gate",
        gate_decision="reject",
        evidence_grade="weak",
        activation_state="",
        hard_reject_codes=hard,
    )
    assert FEEDBACK_UNSAFE_OR_UNREPRESENTABLE in _fuzzy_codes_for(event)


def test_unreachable_quarantine_template_is_gone() -> None:
    """The all_candidates_quarantined planning template had no producing cut;
    the planner must resolve that reason through the fail-closed route."""

    class Attribution:
        reason_code = "all_candidates_quarantined"
        layer = LAYER_L2
        artifact = "coverage_validity_policy"
        evidence = {"terminal_state": "unresolved"}

        def to_dict(self):
            return {
                "layer": self.layer,
                "artifact": self.artifact,
                "reason_code": self.reason_code,
                "evidence": dict(self.evidence),
            }

    plan = plan_policy_candidates(
        attribution=Attribution(),
        genome=PolicyGenome(genome_id="g"),
    )
    assert plan.candidates == ()
    assert plan.route_result != ""
