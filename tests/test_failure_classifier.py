"""Tests for the deterministic failure classifier (v6)."""

from src.sehd_v6.core.bccse import v6_contracts as contracts
from src.sehd_v6.core.bccse.failure_classifier import (
    ARTIFACT_OWNER,
    classify_failure,
)


def _court(**overrides) -> dict:
    """A well-formed, internally consistent proof-court result."""
    base = {
        "state": "unresolved",
        "prosecution_coverage": "incomplete",
        "defense_coverage": "incomplete",
        "valid_error_certificate_count": 1,
        "valid_defense_certificate_count": 0,
        "surviving_error_certificate_ids": [],
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": [],
        "consensus_gate_applied": False,
        "consensus_gate_blocked_ids": [],
        "required_candidate_count": 1,
        "error_audits": [],
        "defense_audits": [],
    }
    base.update(overrides)
    return base


def _record(**overrides) -> dict:
    base = {
        "adjudication": _court(),
        "boundary_path_state": "",
        "provider_artifacts": [],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# Rule 1: provider artifact missing / malformed -> L1
# ------------------------------------------------------------------

def test_rule1_missing_artifact_maps_to_owner_module():
    record = _record(provider_artifacts=[
        {
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": False,
            "missing_fields": ["causal_chain"],
        },
    ])
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L1
    assert result.artifact == "evidence_obligation"
    assert result.reason_code == "provider_artifact_missing"
    assert result.evidence["kind"] == "error_certificate"
    assert result.evidence["missing_fields"] == ["causal_chain"]
    assert result.actionable


def test_rule1_malformed_artifact_reason_is_malformed():
    record = _record(provider_artifacts=[
        {
            "kind": "candidate",
            "module_hint": "",
            "malformed": True,
            "missing_fields": [],
        },
    ])
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L1
    assert result.artifact == "candidate_trigger"
    assert result.reason_code == "provider_artifact_malformed"


def test_rule1_registered_module_hint_wins_over_default_owner():
    record = _record(provider_artifacts=[
        {
            "kind": "defense_certificate",
            "module_hint": "abstention_rule",
            "malformed": False,
            "missing_fields": ["boundary"],
        },
    ])
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L1
    assert result.artifact == "abstention_rule"


def test_rule1_unregistered_hint_falls_back_to_owner_table():
    record = _record(provider_artifacts=[
        {
            "kind": "defense_certificate",
            "module_hint": "not_a_module",
            "malformed": False,
            "missing_fields": ["boundary"],
        },
    ])
    assert classify_failure(record).artifact == "clean_defense_boundary"


def test_rule1_malformed_error_cert_is_verifier_quality_gap():
    """A certificate that was emitted but failed field validation is a
    certificate-quality gap owned by atomic_verifier_instruction; a missing
    certificate is an evidence-obligation gap owned by evidence_obligation."""
    record = _record(provider_artifacts=[
        {
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": True,
            "missing_fields": ["norm_evidence_refs"],
        },
    ])
    result = classify_failure(record)
    assert result.artifact == "atomic_verifier_instruction"
    assert result.reason_code == "provider_artifact_malformed"
    assert result.evidence["missing_fields"] == ["norm_evidence_refs"]


def test_artifact_owner_table_covers_all_kinds():
    assert set(ARTIFACT_OWNER) == {
        "candidate", "error_certificate", "defense_certificate",
    }
    assert all(v in contracts.L1_ARTIFACTS for v in ARTIFACT_OWNER.values())


# ------------------------------------------------------------------
# Rule 2: consensus gate blocked -> L2 consensus_gate_policy
# ------------------------------------------------------------------

def test_rule2_consensus_gate_blocked():
    record = _record(adjudication=_court(
        consensus_gate_applied=True,
        consensus_gate_blocked_ids=["cand-1"],
    ))
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_CONSENSUS_GATE
    assert result.reason_code == "consensus_gate_blocked"
    assert result.evidence["consensus_gate_blocked_count"] == 1
    assert result.actionable


def test_rule2_blocked_ids_without_gate_applied_do_not_fire():
    # Registered predicate semantics: gate must be applied AND blocked
    # nonempty; classifier must not re-derive a looser version.
    record = _record(adjudication=_court(
        consensus_gate_applied=False,
        consensus_gate_blocked_ids=["cand-1"],
        valid_error_certificate_count=1,
    ))
    result = classify_failure(record)
    assert result.layer != contracts.LAYER_L2
    assert result.artifact != contracts.L2_ARTIFACT_CONSENSUS_GATE


# ------------------------------------------------------------------
# Rule 3: hollow complete coverage -> L2 coverage_validity_policy
# ------------------------------------------------------------------

def test_rule3_hollow_complete_defense():
    record = _record(adjudication=_court(
        defense_coverage="complete",
        valid_defense_certificate_count=0,
    ))
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_COVERAGE_VALIDITY
    assert result.reason_code == "hollow_complete_defense"


def test_rule3_hollow_complete_prosecution():
    record = _record(adjudication=_court(
        prosecution_coverage="complete",
        valid_error_certificate_count=0,
    ))
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_COVERAGE_VALIDITY
    assert result.reason_code == "hollow_complete_prosecution"


# ------------------------------------------------------------------
# Rule 4: boundary verified_clean + unresolved -> L2 boundary policy
# ------------------------------------------------------------------

def test_rule4_boundary_terminal_conflict():
    record = _record(
        boundary_path_state="verified_clean",
        adjudication=_court(state="unresolved"),
    )
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_BOUNDARY_CONSUMPTION
    assert result.reason_code == "boundary_terminal_conflict"
    assert result.evidence["boundary_path_state"] == "verified_clean"


# ------------------------------------------------------------------
# Rule 5: fall-through
# ------------------------------------------------------------------

def test_rule5_plain_unresolved_is_no_actionable():
    result = classify_failure(_record(adjudication=_court(state="unresolved")))
    assert result.layer == contracts.LAYER_NONE
    assert not result.actionable


def test_rule5_quarantined_all_but_verdict_given_is_detector_out_of_scope():
    record = _record(adjudication=_court(
        state="verified_error",
        required_candidate_count=1,
        quarantined_error_certificate_ids=["e1"],
        valid_error_certificate_count=0,
        valid_defense_certificate_count=0,
    ))
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_DETECTOR
    assert result.reason_code == "internal_inconsistency"
    assert not result.actionable


def test_rule5_surviving_errors_with_clean_verdict_is_detector_out_of_scope():
    record = _record(adjudication=_court(
        state="verified_clean",
        surviving_error_certificate_ids=["e1"],
        valid_error_certificate_count=1,
    ))
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_DETECTOR
    assert result.reason_code == "internal_inconsistency"


def test_consistent_clean_verdict_is_no_actionable():
    record = _record(adjudication=_court(
        state="verified_clean",
        defense_coverage="complete",
        valid_defense_certificate_count=1,
        valid_error_certificate_count=0,
    ))
    assert classify_failure(record).layer == contracts.LAYER_NONE


# ------------------------------------------------------------------
# Priority order
# ------------------------------------------------------------------

def test_priority_malformed_artifact_beats_consensus_blocked():
    # Both rule 1 and rule 2 fire: most-upstream (L1 artifact) wins.
    record = _record(
        provider_artifacts=[
            {"kind": "candidate", "module_hint": "", "malformed": True,
             "missing_fields": []},
        ],
        adjudication=_court(
            consensus_gate_applied=True,
            consensus_gate_blocked_ids=["cand-1"],
        ),
    )
    result = classify_failure(record)
    assert result.layer == contracts.LAYER_L1
    assert result.reason_code == "provider_artifact_malformed"


def test_priority_l2_rule_beats_fall_through():
    record = _record(adjudication=_court(
        state="unresolved",
        consensus_gate_applied=True,
        consensus_gate_blocked_ids=["cand-1"],
    ))
    assert classify_failure(record).layer == contracts.LAYER_L2


# ------------------------------------------------------------------
# Input contract robustness
# ------------------------------------------------------------------

def test_missing_optional_fields_do_not_crash():
    result = classify_failure({})
    assert result.layer in contracts.FAILURE_LAYERS
    assert classify_failure({"adjudication": None}).layer in contracts.FAILURE_LAYERS
    assert classify_failure(
        {"adjudication": {}, "boundary_path_state": None,
         "provider_artifacts": None}
    ).layer in contracts.FAILURE_LAYERS


def test_unknown_artifact_kind_is_ignored_not_crashed():
    record = _record(provider_artifacts=[
        {"kind": "mystery", "module_hint": "", "malformed": True,
         "missing_fields": ["x"]},
    ])
    assert classify_failure(record).layer == contracts.LAYER_NONE


def test_result_serializable_via_to_dict():
    record = _record(provider_artifacts=[
        {"kind": "candidate", "module_hint": "", "malformed": True,
         "missing_fields": []},
    ])
    payload = classify_failure(record).to_dict()
    assert set(payload) == {"layer", "artifact", "reason_code", "evidence"}
    assert payload["layer"] == contracts.LAYER_L1
    assert isinstance(payload["evidence"], dict)


# ------------------------------------------------------------------
# P0 frozen fixtures: attribution positive/negative reproducibility
# ------------------------------------------------------------------

from tests.fixtures import meta_skill_evolution as frozen


def test_fixture_consensus_blocked_unresolved_attributes_l2_consensus():
    result = classify_failure(
        frozen.classifier_record(frozen.consensus_blocked_unresolved())
    )
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_CONSENSUS_GATE
    assert result.reason_code == "consensus_gate_blocked"


def test_fixture_consensus_blocked_with_verdict_never_attributes_l2():
    """A gate that filtered candidates yet still produced a verdict is the
    detector working as designed, not an addressable L2 failure."""
    result = classify_failure(
        frozen.classifier_record(frozen.consensus_blocked_terminal_verdict())
    )
    assert result.layer == contracts.LAYER_NONE
    assert result.reason_code == "no_failure_shape"


def test_fixture_boundary_verified_clean_unresolved_attributes_l2_boundary():
    result = classify_failure(
        frozen.classifier_record(frozen.boundary_verified_clean_unresolved())
    )
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_BOUNDARY_CONSUMPTION
    assert result.reason_code == "boundary_terminal_conflict"


def test_fixture_boundary_conflict_with_surviving_error_attributes_l2():
    """Attribution still routes to the boundary-consumption policy; the
    surviving error is what the post-ledger runtime must fail closed on."""
    result = classify_failure(
        frozen.classifier_record(frozen.boundary_conflict_with_surviving_error())
    )
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_BOUNDARY_CONSUMPTION


def test_fixture_hollow_prosecution_attributes_l2_coverage():
    result = classify_failure(
        frozen.classifier_record(frozen.hollow_prosecution())
    )
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_COVERAGE_VALIDITY
    assert result.reason_code == "hollow_complete_prosecution"


def test_fixture_hollow_defense_attributes_l2_coverage():
    result = classify_failure(
        frozen.classifier_record(frozen.hollow_defense())
    )
    assert result.layer == contracts.LAYER_L2
    assert result.artifact == contracts.L2_ARTIFACT_COVERAGE_VALIDITY
    assert result.reason_code == "hollow_complete_defense"


def test_fixture_l1_malformed_candidate_attributes_l1():
    result = classify_failure(
        frozen.classifier_record(frozen.l1_malformed_candidate())
    )
    assert result.layer == contracts.LAYER_L1
    assert result.artifact == "candidate_trigger"
    assert result.reason_code == "provider_artifact_malformed"


def test_fixture_attributions_are_byte_identical_across_rebuilds():
    import json

    builders = (
        frozen.consensus_blocked_unresolved,
        frozen.consensus_blocked_terminal_verdict,
        frozen.boundary_verified_clean_unresolved,
        frozen.boundary_conflict_with_surviving_error,
        frozen.hollow_prosecution,
        frozen.hollow_defense,
        frozen.l1_malformed_candidate,
    )
    for build in builders:
        first = classify_failure(frozen.classifier_record(build())).to_dict()
        second = classify_failure(frozen.classifier_record(build())).to_dict()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
