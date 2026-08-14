"""D2 RouteAddressability invariants and the T2.1 repairability verdicts.

The addressability table is the closed authority for "a replayable
intervention point exists": every evolvable L1 owner maps to a named
detector route, every route named is a real detector route, L2 artifacts
stay out (their surface is the vocabulary registry), and the repairability
authority reads the table — not the capability registry — when deciding
between REPAIRABLE, CAPABILITY_GAP and UNADDRESSABLE_FRAMEWORK_DEFECT.
"""

from __future__ import annotations

from types import SimpleNamespace

from sehd_rise.causal.episode import FailureEdge
from sehd_rise.causal.repairability import RepairabilityStatus, analyze
from sehd_rise.causal.route_addressability import (
    ROUTE_ADDRESSABILITY,
    address_for,
)
from sehd_rise.detection.detector import REQUIRED_ROUTES
from sehd_rise.skills.vocabulary.contracts import MODULE_IDS


def _edge(owner: str) -> FailureEdge:
    return FailureEdge(
        producer_route="",
        output_object="candidate",
        field="anchor_id",
        consumer="proof_court",
        observed="absent",
        required="present",
        owner_module=owner,
        source_artifact_id="",
        blocking_predicate="gold_polarity_mismatch",
    )


def _l1_episode(owner: str) -> SimpleNamespace:
    return SimpleNamespace(
        episode_id="episode_d2",
        skill_layer="l1_provider_rule",
        reason_code="provider_artifact_missing",
        failure_origin="",
        failure_owner=owner,
        terminal_state="verified_clean",
        detection_snapshot={},
        attribution_snapshot={},
        causal_failure_edge_frontier=(_edge(owner),),
    )


def test_every_evolvable_l1_owner_maps_a_real_detector_route() -> None:
    assert set(ROUTE_ADDRESSABILITY) == set(MODULE_IDS)
    for address in ROUTE_ADDRESSABILITY.values():
        assert address.producer_route in REQUIRED_ROUTES
        assert address.replay_scope[0] == address.producer_route
        assert set(address.replay_scope) <= set(REQUIRED_ROUTES)
        assert address.binding_domains
        assert address.observable_postcondition


def test_l2_artifacts_stay_out_of_the_route_table() -> None:
    for artifact in (
        "coverage_validity", "consensus_gate", "boundary_consumption",
        "appeal_mechanism",
    ):
        assert address_for(artifact) is None


def test_route_addressable_failure_without_opcode_is_repairable() -> None:
    """§12.2: a failure with a target route but no deterministic opcode is
    REPAIRABLE, never CAPABILITY_GAP."""
    verdict = analyze(
        _l1_episode("candidate_trigger"),
        allowed_modules=("candidate_trigger",),
        evolution_direction="clean_to_error",
    )
    assert verdict.status is RepairabilityStatus.REPAIRABLE
    assert verdict.evidence["primary_executor"] == "l1_route_guidance"
    assert verdict.evidence["primary_legal_operations"] == []


def test_unrouteable_failure_is_the_true_capability_gap() -> None:
    verdict = analyze(
        _l1_episode("unregistered_owner"),
        allowed_modules=("unregistered_owner",),
        evolution_direction="clean_to_error",
    )
    assert verdict.status is RepairabilityStatus.CAPABILITY_GAP
    assert verdict.evidence["route_addressable"] is False
