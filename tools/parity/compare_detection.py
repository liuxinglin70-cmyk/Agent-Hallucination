"""Old/new detection parity harness (plan §3.3 level 2).

Two verification modes:

1. Warm-cache replay (preferred).  With ``PACA_GLOBAL_CACHE_ROOT`` pointing at
   a populated provider cache, every fixture case is adjudicated through the
   frozen reference engine (``src.sehd_v6``) and through ``sehd_rise``; the
   verdict, decision authority, surviving/quarantined certificate sets and the
   terminal basis must be field-equal, and every new-path provider call must
   be a cache hit (a hit is only possible when the request bytes are
   identical, so hit-ness is itself the byte-equality proof).

2. Offline contract check (degradation path, §5 R2).  When no warm cache is
   available, the deterministic surfaces are compared instead: packet build,
   certificate validators, the full proof-court adjudicator over synthetic
   evidence bundles covering error/clean/unresolved terminals, the L2 policy
   executors over identical adjudication facts, certificate identity, and
   replica aggregation.  The degradation is reported loudly, never skipped.

Run:  python tools/parity/compare_detection.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.parity.fixtures import load_fixture_records  # noqa: E402

from src.sehd_v6.core.paca_tcl import audit_contract as old_audit  # noqa: E402
from src.sehd_v6.core.paca_tcl import policy_evaluator as old_policy  # noqa: E402
from src.sehd_v6.core.paca_tcl import proof_contract as old_court  # noqa: E402
from src.sehd_v6.core.paca_tcl import proof_contract_v3 as old_v3  # noqa: E402
from src.sehd_v6.core.paca_tcl import proof_contract_v3_4 as old_v34  # noqa: E402
from src.sehd_v6.core.bccse import certificate_identity as old_identity  # noqa: E402
from src.sehd_v6.core.bccse import v6_contracts as old_v6  # noqa: E402
from src.sehd_v6.detector import replica_aggregation as old_replicas  # noqa: E402
from src.sehd_v6.utils import ocse_packet as old_packet  # noqa: E402

from sehd_rise.detection import packet as new_packet  # noqa: E402
from sehd_rise.detection import policy as new_policy  # noqa: E402
from sehd_rise.detection import replicas as new_replicas  # noqa: E402
from sehd_rise.detection.court import audit as new_audit  # noqa: E402
from sehd_rise.detection.court import contract as new_court  # noqa: E402
from sehd_rise.detection.court import identity as new_identity  # noqa: E402
from sehd_rise.platform.jsonio import canonical_json  # noqa: E402
from sehd_rise.skills.vocabulary import contracts as new_vocab  # noqa: E402

CHECKS: list[str] = []
FAILURES: list[str] = []


def _jsonable(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def check(name: str, old_value: Any, new_value: Any) -> None:
    old_blob = canonical_json(_jsonable(old_value))
    new_blob = canonical_json(_jsonable(new_value))
    if old_blob == new_blob:
        CHECKS.append(name)
        print(f"  ok   {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL {name}")
        print(f"       old: {old_blob[:600]}")
        print(f"       new: {new_blob[:600]}")


# ============================================================
# Offline surface 1: packet build / validation on fixture records
# ============================================================

def check_packet_parity() -> None:
    print("packet parity (fixtures)")
    records = load_fixture_records()
    assert len(records) >= 8, "fixture set must cover at least 8 cases"
    for case_id, record in sorted(records.items()):
        check(
            f"packet build {case_id}",
            old_packet.build_packet(record),
            new_packet.build_packet(record),
        )


# ============================================================
# Offline surface 2: certificate validators + audit translation
# ============================================================

def _synthetic_packet() -> dict[str, Any]:
    events = []
    for i in range(1, 6):
        events.append({
            "event_id": f"event_{i:03d}",
            "position": i,
            "source_step_id": i,
            "role": "assistant",
            "content": f"step {i}",
            "tool_interactions": [{
                "interaction_id": f"event_{i:03d}_tool_01",
                "tool_name": "search",
                "arguments": {"q": i},
                "observation": f"obs {i}",
            }],
        })
    return {"schema_version": "agenthallu.ocse_packet.v1", "question": "q", "events": events}


def _valid_error_cert(cert_id: str, candidate: str, origin: str, impact: str) -> dict[str, Any]:
    return {
        "certificate_id": cert_id,
        "source_candidate_id": candidate,
        "origin_event_id": origin,
        "claim_kind": "agent_assertion",
        "norm_source": "external_evidence",
        "norm_evidence_refs": [origin],
        "breach_type": "fabrication",
        "violation_evidence_refs": [origin],
        "agent_control_event_id": origin,
        "agent_control_evidence_refs": [origin],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact],
        "impact_evidence_refs": [impact],
        "recovery_state": "none",
        "recovery_evidence_refs": [],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact],
        "rationale": "the fabricated claim reached the final answer",
    }


def _valid_defense_cert(cert_id: str, candidate: str, origin: str,
                        defense_type: str = "insufficient_evidence") -> dict[str, Any]:
    return {
        "certificate_id": cert_id,
        "target_candidate_id": candidate,
        "target_origin_event_id": origin,
        "defense_type": defense_type,
        "evidence_refs": [origin],
        "rationale": "no dominating boundary is provable from the trajectory",
    }


def check_validator_parity() -> None:
    print("validator / audit parity")
    packet = _synthetic_packet()
    origins = {"cand_1": "event_001", "cand_2": "event_002"}
    error_ok = _valid_error_cert("err_1", "cand_1", "event_001", "event_003")
    error_bad = {**error_ok, "impact_event_ids": ["event_404"]}
    defense_ok = _valid_defense_cert("def_1", "cand_1", "event_001")
    defense_bad = {**defense_ok, "evidence_refs": []}

    check("validate_error_certificate pass",
          old_v3.validate_error_certificate(error_ok, packet, origins),
          new_court.validate_error_certificate(error_ok, packet, origins))
    check("validate_error_certificate quarantine",
          old_v3.validate_error_certificate(error_bad, packet, origins),
          new_court.validate_error_certificate(error_bad, packet, origins))
    check("validate_defense_certificate pass",
          old_v34.validate_defense_certificate(defense_ok, packet, origins),
          new_court.validate_defense_certificate(defense_ok, packet, origins))
    check("validate_defense_certificate quarantine",
          old_v34.validate_defense_certificate(defense_bad, packet, origins),
          new_court.validate_defense_certificate(defense_bad, packet, origins))
    check("validate_focused_prosecution chain scores",
          old_court.validate_focused_prosecution(error_ok, packet, origins),
          new_court.validate_focused_prosecution(error_ok, packet, origins))

    # §2.2 ownership ruling: audit tables have exactly one home.
    for name in (
        "ERROR_CHECK_FIELDS", "DEFENSE_CHECK_FIELDS",
        "ERROR_RELATIONAL_INVARIANTS", "DEFENSE_RELATIONAL_INVARIANTS",
        "ERROR_INVARIANT_TARGETS", "DEFENSE_INVARIANT_TARGETS",
    ):
        old_value = getattr(old_audit, name)
        new_value = getattr(new_vocab, name)
        check(f"audit vocabulary {name}",
              {str(k): list(v) for k, v in old_value.items()} if isinstance(old_value, dict) else sorted(map(str, old_value)),
              {str(k): list(v) for k, v in new_value.items()} if isinstance(new_value, dict) else sorted(map(str, new_value)))
        # The court audit module must re-expose the same tables.
        assert getattr(new_audit, name) is new_value or getattr(new_audit, name) == new_value

    check("project_audit_failures",
          old_audit.project_audit_failures(
              row=error_bad, failed_checks=["impact_events", "required_fields"],
              required_fields=sorted(old_v34.ERROR_FIELDS),
              check_fields=old_audit.ERROR_CHECK_FIELDS,
              relational_invariants=old_audit.ERROR_RELATIONAL_INVARIANTS),
          new_audit.project_audit_failures(
              row=error_bad, failed_checks=["impact_events", "required_fields"],
              required_fields=sorted(new_vocab.ERROR_FIELDS) if hasattr(new_vocab, "ERROR_FIELDS") else sorted(old_v34.ERROR_FIELDS),
              check_fields=new_vocab.ERROR_CHECK_FIELDS,
              relational_invariants=new_vocab.ERROR_RELATIONAL_INVARIANTS))


# ============================================================
# Offline surface 3: full proof-court adjudication parity
# ============================================================

def _court_scenarios() -> dict[str, dict[str, Any]]:
    packet = _synthetic_packet()
    required = [
        {"candidate_id": "cand_1", "origin_hypothesis_event_id": "event_001"},
        {"candidate_id": "cand_2", "origin_hypothesis_event_id": "event_002"},
    ]
    err1 = _valid_error_cert("focused_error_001", "cand_1", "event_001", "event_003")
    err2 = _valid_error_cert("focused_error_002", "cand_2", "event_002", "event_004")
    err2_process = {
        **err2,
        "benchmark_causal_status": "process_only",
        "benchmark_causal_evidence_refs": [],
        "impact_kind": "process_debt",
    }
    appeal_err = {
        **_valid_error_cert("appeal_error_001", "cand_2", "event_002", "event_004"),
    }
    defense_weak = _valid_defense_cert("def_1", "cand_1", "event_001")
    defense_strong = _valid_defense_cert(
        "def_2", "cand_2", "event_002", defense_type="supported_or_correct",
    )
    defense_strong["evidence_refs"] = ["event_001", "event_002"]

    prosecution_error = {"coverage_state": "complete", "candidates": [err1]}
    prosecution_two = {"coverage_state": "complete", "candidates": [err1, err2]}
    prosecution_process = {"coverage_state": "complete", "candidates": [err2_process]}
    defense_insufficient = {
        "coverage_state": "complete",
        "candidates": [defense_weak, _valid_defense_cert("def_2", "cand_2", "event_002")],
    }
    defense_dominating = {
        "coverage_state": "complete",
        "candidates": [
            _valid_defense_cert("def_1", "cand_1", "event_001"),
            defense_strong,
        ],
    }
    defense_quarantined = {
        "coverage_state": "complete",
        "candidates": [
            _valid_defense_cert("def_1", "cand_1", "event_001"),
            {**_valid_defense_cert("def_2", "cand_2", "event_002"), "evidence_refs": []},
        ],
    }
    appeal_defense_bundle = {
        "coverage_state": "complete",
        "candidates": [_valid_defense_cert("adef_1", "cand_2", "event_002")],
    }
    rankings = {
        "event_001": {"causal_sufficiency_score": 0.4, "is_root_cause": False},
        "event_002": {"causal_sufficiency_score": 0.9, "is_root_cause": True},
    }
    return {
        "verified_error_single_survivor": {
            "packet": packet, "required_candidates": required,
            "prosecution": prosecution_error, "defense": defense_insufficient,
        },
        "verified_clean_full_coverage": {
            "packet": packet, "required_candidates": required,
            "prosecution": prosecution_process, "defense": defense_dominating,
        },
        "unresolved_quarantined_defense": {
            "packet": packet, "required_candidates": required,
            "prosecution": prosecution_process, "defense": defense_quarantined,
        },
        "unresolved_incomplete_coverage": {
            "packet": packet, "required_candidates": required,
            "prosecution": {"coverage_state": "incomplete", "candidates": [err1]},
            "defense": defense_insufficient,
        },
        "counterfactual_root_selection": {
            "packet": packet, "required_candidates": required,
            "prosecution": prosecution_two, "defense": defense_insufficient,
            "counterfactual_rankings": rankings,
        },
        "appeal_merge_and_consensus_gate": {
            "packet": packet, "required_candidates": required,
            "prosecution": prosecution_error, "defense": defense_insufficient,
            "appeal_defense": appeal_defense_bundle,
            "initial_prosecution": prosecution_error,
            "apply_consensus_gate_flag": True,
        },
    }


def check_court_parity() -> None:
    print("proof-court adjudication parity")
    for name, kwargs in sorted(_court_scenarios().items()):
        check(
            f"court {name}",
            old_court.adjudicate_proof_court(**kwargs),
            new_court.adjudicate_proof_court(**kwargs),
        )


# ============================================================
# Offline surface 4: L2 policy executor parity
# ============================================================

def _policy_battery() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("unresolved_with_complete", "downgrade_coverage",
         {"side": "defense", "to_state": "incomplete"}),
        ("unresolved_with_complete", "invalidate_coverage_claim",
         {"side": "prosecution"}),
        ("unresolved_with_complete", "emit_telemetry_only", {}),
        ("unresolved_with_complete", "require_additional_verification",
         {"verification_scope": "certificate"}),
        ("unresolved_with_complete", "escalate_to_appeal", {}),
        ("unresolved_with_complete", "mark_candidate_ambiguous",
         {"ambiguity_reason": "conflicting_evidence"}),
        ("unresolved_with_complete", "consume_verified_clean_boundary", {}),
        ("unknown_precondition_name", "downgrade_coverage",
         {"side": "defense", "to_state": "incomplete"}),
        ("unresolved_with_complete", "not_a_real_action", {}),
        ("unresolved_with_complete", "downgrade_coverage",
         {"side": "defense"}),
    ]


def check_policy_parity() -> None:
    print("L2 policy executor parity")
    court_result = old_court.adjudicate_proof_court(**_court_scenarios()["unresolved_incomplete_coverage"])
    old_facts = old_v6.build_adjudication_facts(
        court_result, boundary_path_state="verified_clean", boundary_authority_valid=True,
    )
    new_facts = new_policy.build_adjudication_facts(
        court_result, boundary_path_state="verified_clean", boundary_authority_valid=True,
    )
    check("build_adjudication_facts", old_facts.__dict__, new_facts.__dict__)

    items_old = [
        old_v6.PolicyItem(
            item_id=f"item_{i:02d}", precondition=pre, action=act, params=dict(params),
        )
        for i, (pre, act, params) in enumerate(_policy_battery(), start=1)
    ]
    items_new = [
        new_vocab.PolicyItem(
            item_id=f"item_{i:02d}", precondition=pre, action=act, params=dict(params),
        )
        for i, (pre, act, params) in enumerate(_policy_battery(), start=1)
    ]
    old_result = old_policy.evaluate_policies(old_facts, items_old)
    new_result = new_policy.evaluate_policies(new_facts, items_new)
    check("evaluate_policies action sequence", old_result.to_dict(), new_result.to_dict())

    pre_facts = old_policy.PreLedgerPolicyFacts(
        consensus_gate_applied=True,
        blocked_certificate_ids=("appeal_error_001",),
        blocked_certificate_audit_status={"appeal_error_001": "pass"},
        blocked_certificate_causal_status={"appeal_error_001": "causes_incorrect_result"},
        blocked_certificate_impact_kind={"appeal_error_001": "final_answer"},
        blocked_certificate_defense_status={"appeal_error_001": "standing"},
        blocked_certificate_quarantine_status={"appeal_error_001": "clean"},
        terminal_without_restore="unresolved",
    )
    pre_items_old = [
        old_v6.PolicyItem(
            item_id="pre_1", precondition="consensus_blocked_present",
            action="restore_audited_consensus",
            params={"certificate_id": "appeal_error_001"},
        ),
        old_v6.PolicyItem(
            item_id="pre_2", precondition="consensus_blocked_present",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "incomplete"},
        ),
    ]
    pre_items_new = [
        new_vocab.PolicyItem(
            item_id="pre_1", precondition="consensus_blocked_present",
            action="restore_audited_consensus",
            params={"certificate_id": "appeal_error_001"},
        ),
        new_vocab.PolicyItem(
            item_id="pre_2", precondition="consensus_blocked_present",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "incomplete"},
        ),
    ]
    check(
        "evaluate_pre_ledger_policies",
        old_policy.evaluate_pre_ledger_policies(pre_facts, pre_items_old).to_dict(),
        new_policy.evaluate_pre_ledger_policies(pre_facts, pre_items_new).to_dict(),
    )


# ============================================================
# Offline surface 4b: provider request surface byte parity
# ============================================================
# Provider request byte-identity is what warm replay would prove via cache
# hits.  Every prompt builder is a pure string function, so the same
# byte-equality is provable offline: identical builder outputs plus the
# unchanged transport payload recipe imply identical request bodies.

def check_prompt_surface_parity() -> None:
    from src.sehd_v6.core.paca_tcl import basic_runner as old_transport
    from src.sehd_v6.core.paca_tcl import layered_engine as old_layered
    from src.sehd_v6.core.paca_tcl import proof_engine as old_engine
    from src.sehd_v6.core.paca_tcl import proof_engine_v3_4 as old_v34_engine
    from sehd_rise.detection import transport as new_transport
    from sehd_rise.detection.court import engine as new_engine

    print("provider request surface parity")
    packet = _synthetic_packet()
    census = [
        {"candidate_id": "cand_1", "origin_hypothesis_event_id": "event_001",
         "candidate_kind": "fabricated_claim", "claim_or_commitment": "c1"},
        {"candidate_id": "cand_2", "origin_hypothesis_event_id": "event_002",
         "candidate_kind": "fabricated_claim", "claim_or_commitment": "c2"},
    ]
    base = {
        "census": census,
        "qualifications": [{"candidate_id": "cand_1", "violation_state": "verified_violation"}],
        "propagations": [{"candidate_id": "cand_1", "propagation_state": "unrecovered_impact"}],
        "boundary_path": {"state": "unresolved", "responsible_event_id": None,
                          "evidence_refs": [], "rationale": ""},
    }
    proof = {
        "state": "unresolved", "responsible_event_id": None,
        "prosecution_coverage": "complete", "defense_coverage": "complete",
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": [],
        "surviving_error_certificate_ids": [],
        "defeated_error_certificate_ids": [],
        "consensus_gate_blocked_ids": [], "decision_authority": "authoritative",
    }
    prosecution = {"coverage_state": "complete", "candidates": []}
    defense = {"coverage_state": "complete", "candidates": []}
    appeal_candidates = [dict(row) for row in census]
    surviving = [
        _valid_error_cert("focused_error_001", "cand_1", "event_001", "event_003"),
        _valid_error_cert("focused_error_002", "cand_2", "event_002", "event_004"),
    ]

    prompt_pairs = [
        ("SINGLE_LEDGER_SYSTEM", old_transport.system_prompt(), new_engine.SINGLE_LEDGER_SYSTEM),
        ("QUALIFICATION_SYSTEM", old_layered.QUALIFICATION_SYSTEM, new_engine.QUALIFICATION_SYSTEM),
        ("PROPAGATION_SYSTEM", old_layered.PROPAGATION_SYSTEM, new_engine.PROPAGATION_SYSTEM),
        ("BOUNDARY_SYSTEM", old_layered.BOUNDARY_SYSTEM, new_engine.BOUNDARY_SYSTEM),
        ("DEFENSE_SYSTEM", old_v34_engine.DEFENSE_SYSTEM, new_engine.DEFENSE_SYSTEM),
        ("CONSTRUCT_CENSUS_SYSTEM", old_v34_engine.CONSTRUCT_CENSUS_SYSTEM, new_engine.CONSTRUCT_CENSUS_SYSTEM),
        ("FOCUSED_PROSECUTION_SYSTEM", old_engine.FOCUSED_PROSECUTION_SYSTEM, new_engine.FOCUSED_PROSECUTION_SYSTEM),
        ("FOCUSED_PROSECUTION_RETRY_SYSTEM", old_engine.FOCUSED_PROSECUTION_RETRY_SYSTEM, new_engine.FOCUSED_PROSECUTION_RETRY_SYSTEM),
        ("APPEAL_PROSECUTOR_SYSTEM", old_engine.APPEAL_PROSECUTOR_SYSTEM_V35, new_engine.APPEAL_PROSECUTOR_SYSTEM),
        ("APPEAL_PROSECUTOR_RETRY_SYSTEM", old_engine.APPEAL_PROSECUTOR_RETRY_SYSTEM_V35, new_engine.APPEAL_PROSECUTOR_RETRY_SYSTEM),
        ("APPEAL_DEFENDER_SYSTEM", old_engine.APPEAL_DEFENDER_SYSTEM_V35, new_engine.APPEAL_DEFENDER_SYSTEM),
        ("APPEAL_DEFENDER_RETRY_SYSTEM", old_engine.APPEAL_DEFENDER_RETRY_SYSTEM_V35, new_engine.APPEAL_DEFENDER_RETRY_SYSTEM),
        ("COUNTERFACTUAL_LOCALIZATION_SYSTEM", old_engine.COUNTERFACTUAL_LOCALIZATION_SYSTEM, new_engine.COUNTERFACTUAL_LOCALIZATION_SYSTEM),
        ("candidate_system_prompt", old_transport.candidate_system_prompt(), new_transport.candidate_system_prompt()),
    ]
    for name, old_text, new_text in prompt_pairs:
        check(f"system prompt {name}", old_text, new_text)

    user_pairs = [
        ("layered provider user (bare)",
         old_layered._provider_user(packet), new_engine._provider_user(packet)),
        ("layered provider user (census)",
         old_layered._provider_user(packet, census=census),
         new_engine._provider_user(packet, census=census)),
        ("single-ledger user",
         old_transport.provider_user_prompt(packet, census),
         new_engine.single_ledger_user_prompt(packet, census)),
        ("candidate census user",
         old_transport.candidate_provider_user_prompt(packet),
         new_transport.candidate_provider_user_prompt(packet)),
        ("proof user",
         old_v34_engine._proof_user(packet, base), new_engine._proof_user(packet, base)),
        ("focused prosecution user",
         old_engine._focused_prosecution_user(built=packet, candidate=census[0], base=base),
         new_engine._focused_prosecution_user(built=packet, candidate=census[0], base=base)),
        ("focused prosecution retry user",
         old_engine._focused_prosecution_retry_user(
             built=packet, candidate=census[0], base=base, feedback="f",
             previous_response={"coverage_state": "incomplete"}),
         new_engine._focused_prosecution_retry_user(
             built=packet, candidate=census[0], base=base, feedback="f",
             previous_response={"coverage_state": "incomplete"})),
        ("appeal prosecutor user",
         old_engine._appeal_prosecutor_user_v35(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof),
         new_engine._appeal_prosecutor_user(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof)),
        ("appeal prosecutor retry user",
         old_engine._appeal_prosecutor_retry_user_v35(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             failure_category="unreviewed_candidate_ids"),
         new_engine._appeal_prosecutor_retry_user(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             failure_category="unreviewed_candidate_ids")),
        ("appeal defender user",
         old_engine._appeal_defender_user_v35(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             appeal_prosecution=prosecution),
         new_engine._appeal_defender_user(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             appeal_prosecution=prosecution)),
        ("appeal defender retry user",
         old_engine._appeal_defender_retry_user_v35(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             appeal_prosecution=prosecution, failure_category="defense_coverage_mismatch"),
         new_engine._appeal_defender_retry_user(
             built=packet, candidates=appeal_candidates, base=base,
             prosecution=prosecution, defense=defense, proof=proof,
             appeal_prosecution=prosecution, failure_category="defense_coverage_mismatch")),
        ("counterfactual localization user",
         old_engine._counterfactual_localization_user(built=packet, surviving_errors=surviving),
         new_engine._counterfactual_localization_user(built=packet, surviving_errors=surviving)),
    ]
    for name, old_text, new_text in user_pairs:
        check(f"user prompt {name}", old_text, new_text)

    merge_pairs = [
        ("deterministic process census",
         old_v34_engine._deterministic_process_census(packet),
         new_engine._deterministic_process_census(packet)),
        ("union census",
         old_v34_engine._union_census(census, []),
         new_engine._union_census(census, [])),
        ("required candidates",
         old_v34_engine._required_candidates(base),
         new_engine._required_candidates(base)),
        ("merge prosecutions",
         old_v34_engine._merge_prosecutions(prosecution, {"candidates": surviving}, packet, {"cand_1"}),
         new_engine._merge_prosecutions(prosecution, {"candidates": surviving}, packet, {"cand_1"})),
        ("construct authority",
         old_v34_engine._construct_authority({"status": "partial", "coverage_state": "complete"}),
         new_engine._construct_authority({"status": "partial", "coverage_state": "complete"})),
        ("validate provider census",
         old_transport.validate_provider_census(
             {"coverage_state": "complete", "candidates": census}, packet),
         new_transport.validate_provider_census(
             {"coverage_state": "complete", "candidates": census}, packet)),
        ("quarantine feedback",
         old_court.build_quarantine_feedback(
             {"failed_checks": ["ordered_chain"], "missing_chain_stages": ["origin_event"]}),
         new_court.build_quarantine_feedback(
             {"failed_checks": ["ordered_chain"], "missing_chain_stages": ["origin_event"]})),
    ]
    for name, old_value, new_value in merge_pairs:
        check(f"helper {name}", old_value, new_value)


# ============================================================
# Offline surface 5: certificate identity + replica aggregation
# ============================================================

def check_identity_and_replica_parity() -> None:
    print("identity / replica parity")
    packet_result = {
        "proof_adjudication": {
            "surviving_error_certificate_ids": ["err_1"],
        },
        "combined_prosecution": {
            "candidates": [_valid_error_cert("err_1", "cand_1", "event_001", "event_003")],
        },
    }
    check("surviving_carrier_fingerprints",
          sorted(old_identity.surviving_carrier_fingerprints(packet_result) or ()),
          sorted(new_identity.surviving_carrier_fingerprints(packet_result) or ()))
    check("certificate_carrier_movement",
          old_identity.certificate_carrier_movement(packet_result, packet_result),
          new_identity.certificate_carrier_movement(packet_result, packet_result))

    combos = [
        ["verified_error", "verified_error", "verified_clean"],
        ["verified_clean"] * 3,
        ["unresolved", "verified_error", "verified_error"],
        ["verified_error", "verified_clean", "unresolved"],
    ]
    for states in combos:
        for policy_name in ("majority", "consensus"):
            check(f"aggregate_states {states} {policy_name}",
                  old_replicas.aggregate_states(states, policy=policy_name),
                  new_replicas.aggregate_states(states, policy=policy_name))


# ============================================================
# Warm-cache replay (preferred path; loudly degraded when unavailable)
# ============================================================

def warm_cache_available() -> bool:
    root = os.environ.get("PACA_GLOBAL_CACHE_ROOT")
    if not root or root.strip().lower() in {"off", "disabled", "none"}:
        return False
    root_path = Path(root.strip())
    return root_path.is_dir() and any(root_path.rglob("*.json"))


def run_warm_replay() -> None:
    from src.sehd_v6.core.paca_tcl import proof_engine as old_engine
    from sehd_rise.detection.court import engine as new_engine
    from sehd_rise.detection.detector import load_detector_config

    config = load_detector_config(
        REPO_ROOT / "sehd_rise" / "configs" / "detector_v3_5.json",
        require_credentials=False,
    )
    records = load_fixture_records()
    fields = (
        "state", "decision_authority", "occurrence_state",
        "responsible_event_id", "impact_status", "winning_impact_kind",
        "wide_construct_state", "surviving_error_certificate_ids",
        "quarantined_error_certificate_ids", "quarantined_defense_certificate_ids",
        "consensus_gate_blocked_ids", "terminal_basis",
    )
    total_calls = 0
    hit_calls = 0
    with tempfile.TemporaryDirectory(prefix="parity_warm_") as tmp:
        for case_id, record in sorted(records.items()):
            old_result = old_engine.adjudicate_case(
                case_id=case_id, record=record, config=config,
                output_dir=Path(tmp) / "old" / case_id.replace("/", "_"),
            )
            new_result = new_engine.adjudicate_case(
                case_id=case_id, record=record, config=config,
                output_dir=Path(tmp) / "new" / case_id.replace("/", "_"),
            )
            old_projection = {k: old_result.get(k) for k in fields}
            new_projection = {k: new_result.get(k) for k in fields}
            old_projection["terminal_basis"] = (
                (old_result.get("proof_adjudication") or {}).get("terminal_basis")
            )
            new_projection["terminal_basis"] = (
                (new_result.get("proof_adjudication") or {}).get("terminal_basis")
            )
            check(f"warm replay {case_id}", old_projection, new_projection)

            def count_hits(node: Any) -> tuple[int, int]:
                calls = hits = 0
                if isinstance(node, dict):
                    if "cache_hit" in node:
                        calls += 1
                        hits += int(bool(node["cache_hit"]))
                    for value in node.values():
                        c, h = count_hits(value)
                        calls += c
                        hits += h
                elif isinstance(node, list):
                    for value in node:
                        c, h = count_hits(value)
                        calls += c
                        hits += h
                return calls, hits

            calls, hits = count_hits(new_result.get("providers"))
            total_calls += calls
            hit_calls += hits
    print(f"warm-cache replay: {hit_calls}/{total_calls} new-path provider calls were cache hits")
    if total_calls == 0 or hit_calls != total_calls:
        FAILURES.append("warm replay cache-hit coverage")
        print("  FAIL warm replay cache-hit coverage (requests diverged or cache cold)")


def main() -> int:
    print("== compare_detection ==")
    check_packet_parity()
    check_validator_parity()
    check_court_parity()
    check_policy_parity()
    check_prompt_surface_parity()
    check_identity_and_replica_parity()

    if warm_cache_available():
        run_warm_replay()
    else:
        print()
        print("DEGRADED (§5 R2): PACA_GLOBAL_CACHE_ROOT warm cache unavailable;")
        print("provider-level byte-identity replay NOT executed in this run.")
        print("Offline deterministic contract checks above were executed instead.")

    print()
    print(f"{len(CHECKS)} checks passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
