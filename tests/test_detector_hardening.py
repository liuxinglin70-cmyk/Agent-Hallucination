from __future__ import annotations

from pathlib import Path
from types import MethodType

from src.sehd_v6.core.bccse.step_attributor import ErrorType
from src.sehd_v6.core.bccse.types import FailureEpisode
from src.sehd_v6.core.paca_tcl import proof_contract
from src.sehd_v6.core.paca_tcl import proof_contract_v3_4
from src.sehd_v6.core.paca_tcl import proof_engine
from src.sehd_v6.core.paca_tcl.proof_evaluator import (
    PacaTclV35EvolutionEvaluator,
    _prediction_state,
    experience_record_from_row,
    metric_snapshot,
)
from src.sehd_v6.core.paca_tcl.proof_engine_v3_4 import _occurrence_first_rows


def _packet() -> dict:
    return {
        "events": [
            {"event_id": "event_001", "tool_interactions": []},
            {"event_id": "event_002", "tool_interactions": []},
        ]
    }


def _candidate() -> dict:
    return {
        "candidate_id": "candidate_001",
        "origin_hypothesis_event_id": "event_001",
    }


def _error_certificate(*, ordered: bool = True) -> dict:
    impact_event = "event_002" if ordered else "event_001"
    control_event = "event_001" if ordered else "event_002"
    return {
        "certificate_id": "error_001",
        "source_candidate_id": "candidate_001",
        "origin_event_id": "event_001",
        "claim_kind": "agent_assertion",
        "norm_source": "prior_trajectory_evidence",
        "norm_evidence_refs": ["event_001"],
        "breach_type": "contradiction",
        "violation_evidence_refs": ["event_001"],
        "agent_control_event_id": control_event,
        "agent_control_evidence_refs": [control_event],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact_event],
        "impact_evidence_refs": [impact_event],
        "recovery_state": "post_impact_residual",
        "recovery_evidence_refs": ["event_002"],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact_event],
        "rationale": (
            "The agent assertion contradicts prior evidence, caused an incorrect "
            "final answer, and was only repaired after the impact occurred."
        ),
    }


def _defense(defense_type: str = "supported_or_correct") -> dict:
    return {
        "certificate_id": "defense_001",
        "target_candidate_id": "candidate_001",
        "target_origin_event_id": "event_001",
        "defense_type": defense_type,
        "evidence_refs": ["event_001"],
        "rationale": "The shared event is offered as a correctness defense.",
    }


def _metric_row(*, proof: dict, prosecution: dict) -> dict:
    return {
        "gold_label": False,
        "gold_steps": [],
        "runtime_valid": True,
        "state": str(proof.get("state") or "unresolved"),
        "responsible_step": None,
        "candidate_path": {"state": "unresolved"},
        "boundary_path": {"state": "unresolved"},
        "census": [],
        "prosecution": prosecution,
        "combined_prosecution": None,
        "proof_adjudication": proof,
        "evidence_health": {},
    }


def test_construct_exception_and_empty_base_cannot_be_clean_valid(
    monkeypatch, tmp_path: Path,
) -> None:
    construct_route = {"name": "construct"}
    defense_route = {"name": "defense"}

    def base_case(**_kwargs):
        return {
            "runtime_valid": True,
            "state": "verified_clean",
            "census": [],
            "qualifications": [],
            "propagations": [],
            "boundary_path": {
                "state": "verified_clean",
                "responsible_event_id": None,
                "evidence_refs": [],
                "rationale": "No base candidate.",
            },
            "adjudication": {"state": "verified_clean"},
            "providers": {},
        }

    def provider(*, route, **_kwargs):
        if route is construct_route:
            raise RuntimeError("construct unavailable")
        assert route is defense_route
        return {"coverage_state": "complete", "candidates": []}, {}

    monkeypatch.setattr(proof_engine.v2, "adjudicate_case", base_case)
    monkeypatch.setattr(proof_engine, "provider_call", provider)

    result = proof_engine.adjudicate_case(
        case_id="case",
        record={
            "question": "test",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
        },
        config={
            "construct_census_route": construct_route,
            "defense_route": defense_route,
        },
        output_dir=tmp_path,
        census_root=tmp_path,
    )

    assert result["state"] == "unresolved"
    assert result["runtime_valid"] is False
    assert result["proof_adjudication"]["decision_authority"] == "invalid"
    reason_codes = {
        item["code"]
        for item in result["proof_adjudication"]["authority_failure_reasons"]
    }
    assert "construct_not_complete" in reason_codes


def test_supported_or_correct_defense_prevents_occurrence_append() -> None:
    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    for module in (proof_contract_v3_4, proof_contract):
        kwargs = (
            {"apply_consensus_gate_flag": False}
            if module is proof_contract else {}
        )
        proof = module.adjudicate_proof_court(
            required_candidates=[_candidate()],
            prosecution=prosecution,
            defense={"coverage_state": "complete", "candidates": [_defense()]},
            packet=_packet(),
            **kwargs,
        )

        assert proof["state"] == "verified_clean"
        assert proof["incident_occurred"] is False
        assert proof["occurrence_state"] == "verified_clean"
        assert proof["occurrence_error_certificate_ids"] == []


def test_post_impact_recovery_does_not_erase_adjudicated_occurrence() -> None:
    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    for module in (proof_contract_v3_4, proof_contract):
        kwargs = (
            {"apply_consensus_gate_flag": False}
            if module is proof_contract else {}
        )
        proof = module.adjudicate_proof_court(
            required_candidates=[_candidate()],
            prosecution=prosecution,
            defense={
                "coverage_state": "complete",
                "candidates": [_defense("insufficient_evidence")],
            },
            packet=_packet(),
            **kwargs,
        )

        assert proof["state"] == "verified_error"
        assert proof["incident_occurred"] is True
        assert proof["occurrence_state"] == "verified_error"
        assert proof["occurrence_error_certificate_ids"] == ["error_001"]
        assert proof["occurrence_impact_status"] == "recovered_after_impact"
        assert proof["occurrence_recovery_state"] == "post_impact_residual"


def test_causal_metric_rejects_malformed_chain_and_is_undefined_without_predictions() -> None:
    malformed_prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate(ordered=False)],
    }
    malformed_proof = proof_contract.adjudicate_proof_court(
        required_candidates=[_candidate()],
        prosecution=malformed_prosecution,
        defense={"coverage_state": "complete", "candidates": [_defense()]},
        packet=_packet(),
        apply_consensus_gate_flag=False,
    )
    malformed_metrics = proof_engine.evaluate([
        _metric_row(proof=malformed_proof, prosecution=malformed_prosecution)
    ])

    assert malformed_metrics["predicted_causal_error_certificate_count"] == 1
    assert malformed_metrics["valid_causal_error_certificate_count"] == 0
    assert malformed_metrics["causal_chain_validity"] == 0.0

    empty_prosecution = {"coverage_state": "complete", "candidates": []}
    no_prediction_metrics = proof_engine.evaluate([
        _metric_row(
            proof={
                "state": "verified_clean",
                "occurrence_state": "verified_clean",
                "error_audits": [],
            },
            prosecution=empty_prosecution,
        )
    ])
    assert no_prediction_metrics["causal_chain_validity"] is None
    assert no_prediction_metrics["causal_chain_validity_defined"] is False


def test_evaluator_uses_occurrence_before_conflicting_legacy_clean_state() -> None:
    row = _metric_row(
        proof={
            # Lower-priority nested and compatibility fields intentionally
            # conflict with the authoritative top-level occurrence state.
            "state": "verified_clean",
            "occurrence_state": "verified_clean",
            "error_audits": [],
        },
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    row.update({
        "case_ref": "case_ref",
        "gold_label": True,
        "state": "verified_clean",
        "incident_state": "verified_non_hallucination",
        "occurrence_state": "verified_error",
        "responsible_step": 99,
        "occurrence_responsible_step": 2,
        "gold_steps": [2],
    })

    assert _prediction_state(row) is True
    experience = experience_record_from_row(row, include_gold=True)
    assert experience.predicted_positive is True
    assert experience.predicted_responsible_ref == "2"
    assert experience.unresolved is False
    assert experience.metadata["proof_state"] == "verified_error"
    assert experience.metadata["prediction_state_authority"] == "occurrence"

    snapshot = metric_snapshot([row])
    assert snapshot.hallucination_recall == 1.0
    assert snapshot.precision == 1.0
    assert snapshot.responsible_step_accuracy == 1.0
    assert snapshot.unresolved_rate == 0.0


def test_evaluator_occurrence_authority_fallback_order() -> None:
    assert _prediction_state({
        "proof_adjudication": {"occurrence_state": "verified_error"},
        "incident_state": "verified_non_hallucination",
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "proof_adjudication": {
            "occurrence_ledger": {"occurrence_state": "verified_error"},
        },
        "incident_state": "verified_non_hallucination",
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "occurrence_ledger": {"occurrence_state": "verified_error"},
        "incident_state": {"occurrence_state": "verified_clean"},
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "incident_state": {"occurrence_state": "verified_error"},
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "incident_state": "verified_hallucination",
        "state": "verified_clean",
    }) is True


def test_invalid_authority_abstains_without_erasing_occurrence_ledger() -> None:
    occurrence_ledger = {
        "state": "verified_error",
        "incident_occurred": True,
        "certificate_ids": ["error_001"],
    }
    proof = {
        "state": "unresolved",
        "occurrence_state": "verified_error",
        "incident_occurred": True,
        "occurrence_ledger": occurrence_ledger,
        "decision_authority": "invalid",
        "error_audits": [],
    }
    row = _metric_row(
        proof=proof,
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    row.update({
        "case_ref": "invalid_case",
        "gold_label": False,
        "runtime_valid": False,
        "state": "verified_clean",
        "occurrence_state": "verified_error",
        "decision_authority": "invalid",
        "occurrence_ledger": occurrence_ledger,
    })

    assert _prediction_state(row) is None
    experience = experience_record_from_row(row)
    assert experience.predicted_positive is None
    assert experience.unresolved is True
    assert experience.certificate_valid is False
    assert experience.metadata["occurrence_state"] == "verified_error"
    assert experience.metadata["prediction_state_authority"] == "invalid"

    projected = _occurrence_first_rows([row])[0]
    assert projected["state"] == "unresolved"
    assert projected["occurrence_ledger"]["state"] == "verified_error"
    assert row["occurrence_ledger"]["state"] == "verified_error"

    detector_metrics = proof_engine.evaluate([row])
    assert detector_metrics["tri_state_counts"] == {"unresolved": 1}
    snapshot = metric_snapshot([row])
    assert snapshot.unresolved_rate == 1.0
    assert snapshot.macro_f1 == 0.0
    assert snapshot.valid_output_rate == 0.0
    assert snapshot.invalid_rate == 1.0

    authority_only = dict(row)
    authority_only["runtime_valid"] = True
    assert _prediction_state(authority_only) is None
    assert _occurrence_first_rows([authority_only])[0]["state"] == "unresolved"

    runtime_only = dict(row)
    runtime_only["decision_authority"] = "authoritative"
    runtime_only["proof_adjudication"] = {
        **proof,
        "decision_authority": "authoritative",
    }
    assert _prediction_state(runtime_only) is None
    assert _occurrence_first_rows([runtime_only])[0]["state"] == "unresolved"


def test_invalid_occurrence_cannot_count_as_replay_correction() -> None:
    parent_proof = {
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "error_audits": [],
    }
    parent = _metric_row(
        proof=parent_proof,
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    parent.update({
        "case_ref": "source",
        "state": "verified_error",
        "occurrence_state": "verified_error",
    })
    child = {
        **parent,
        "runtime_valid": False,
        "decision_authority": "invalid",
        "proof_adjudication": {
            **parent_proof,
            "state": "unresolved",
            "decision_authority": "invalid",
            "occurrence_ledger": {"state": "verified_error"},
        },
    }

    evaluator = object.__new__(PacaTclV35EvolutionEvaluator)
    evaluator._get_rows = MethodType(
        lambda _self, genome, _keys: (parent,) if genome == "parent" else (child,),
        evaluator,
    )
    evaluator._anonymous_case_map = MethodType(
        lambda _self, rows: {str(row["case_ref"]): row for row in rows},
        evaluator,
    )
    replay = evaluator.paired_replay(
        parent="parent",
        child="child",
        case_keys=("source",),
        episodes=(FailureEpisode(
            record_key="source",
            error_type=ErrorType.FALSE_POSITIVE,
            support_refs=("source",),
        ),),
    )

    assert replay.targeted_error_total == 1
    assert replay.targeted_error_corrected == 0
    assert replay.valid_outputs == 0
