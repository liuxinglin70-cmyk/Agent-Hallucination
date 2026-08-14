"""T2.4 route-guidance injection: the compile-time firewall, the owner->route
mapping single source, and the target-route-only injection surface.

A route-guidance skill has no deterministic backend, so its intervention is
a firewall-checked guidance block compiled into the owner's producer route.
These tests pin: (1) the firewall rejects verdict/evidence/certificate
authority text at compile time; (2) guidance compiles only for the target
route and never leaks to a non-target route; (3) the skills owner->route
table and the causal RouteAddressability table never drift.
"""

from __future__ import annotations

from sehd_rise.causal.route_addressability import ROUTE_ADDRESSABILITY
from sehd_rise.detection.court.engine import _guidance_suffix
from sehd_rise.detection.injection import compile_route_guidance
from sehd_rise.skills.capabilities import OWNER_PRODUCER_ROUTE
from sehd_rise.skills.genome import (
    MODULE_IDS,
    SkillEdit,
    SkillGenome,
    SkillModule,
    create_baseline_genome,
    validate_edit,
)
from sehd_rise.skills.rule import sanitize_route_guidance, validate_route_guidance


def _condition_spec() -> dict:
    # A certificate-domain predicate: ``atomic_verifier_instruction`` binds in
    # the ``certificate`` domain, so the guidance fires only when a certificate
    # entity is not yet evidence-verified.
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "evidence_verified",
        "operator": "not_equals",
        "value_type": "boolean",
        "value": True,
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _genome_with_guidance(owner: str, guidance: str) -> SkillGenome:
    baseline = create_baseline_genome()
    modules = {
        mid: SkillModule.from_dict(baseline.modules[mid].to_dict())
        for mid in MODULE_IDS
    }
    genome = SkillGenome(genome_id="g_guidance", modules=modules)
    module = genome.modules[owner]
    module.rules.append("When <guidance> applies, follow the route guidance.")
    module.set_rule_meta(
        len(module.rules) - 1,
        {"route_guidance": guidance, "condition_spec": _condition_spec()},
    )
    return genome


def test_route_guidance_firewall_rejects_authority_text() -> None:
    assert sanitize_route_guidance("Create a certificate for this candidate.") == ""
    assert sanitize_route_guidance("Mark this example hallucinated.") == ""
    assert sanitize_route_guidance("return unresolved") == ""
    assert sanitize_route_guidance("Use case_id BFCL_000.") == ""


def test_route_guidance_firewall_keeps_verification_principles() -> None:
    clean = "Verify each cited event independently before accepting a claim."
    ok, reason = validate_route_guidance(clean)
    assert ok, reason
    assert sanitize_route_guidance(clean) == clean


def test_compile_route_guidance_drops_poisoned_blocks() -> None:
    genome = _genome_with_guidance(
        "atomic_verifier_instruction",
        "Verify cited events independently.",
    )
    # A second module whose guidance would violate authority: dropped.
    genome.modules["evidence_obligation"].rules.append("When X, do Y.")
    genome.modules["evidence_obligation"].set_rule_meta(
        len(genome.modules["evidence_obligation"].rules) - 1,
        {"route_guidance": "Create a certificate for this candidate."},
    )
    compiled = compile_route_guidance(genome)
    assert "atomic_verifier_instruction" in compiled
    assert "evidence_obligation" not in compiled
    assert compiled["atomic_verifier_instruction"][0].text == (
        "Verify cited events independently."
    )


def test_compile_route_guidance_drops_conditionless_entries() -> None:
    """A guidance with no applicability predicate is unconditional — the D1
    generalization hazard — and must never compile."""
    genome = _genome_with_guidance(
        "atomic_verifier_instruction", "Verify cited events.",
    )
    # Strip the condition from the stored meta.
    module = genome.modules["atomic_verifier_instruction"]
    meta = module.get_rule_meta(len(module.rules) - 1)
    meta.pop("condition_spec", None)
    module.set_rule_meta(len(module.rules) - 1, meta)
    assert compile_route_guidance(genome) == {}


def test_select_route_guidance_gates_on_condition() -> None:
    from sehd_rise.detection.injection import select_route_guidance
    genome = _genome_with_guidance(
        "atomic_verifier_instruction", "Verify cited events.",
    )
    compiled = compile_route_guidance(genome)
    # A court result where evidence_verified is False: the condition holds.
    matching = {
        "proof_adjudication": {
            "state": "verified_clean",
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": [],
            "error_audits": [{
                "certificate_id": "c1",
                "decision": "fail",
                "missing_fields": [],
                "failed_invariants": [],
            }],
        },
        "census": [{
            "candidate_id": "c1",
            "candidate_kind": "unsupported_claim",
            "module_tag": "tool_use",
            "origin_hypothesis_event_id": "e1",
            "axes": ["F"],
        }],
        "combined_prosecution": {"candidates": [{
            "certificate_id": "c1", "source_candidate_id": "c1",
            "origin_event_id": "e1",
        }]},
    }
    selected = select_route_guidance(compiled, result=matching)
    assert selected == {
        "atomic_verifier_instruction": ("Verify cited events.",),
    }
    # A result whose projected features do not satisfy the condition: nothing
    # is injected.
    non_matching = {
        **matching,
        "proof_adjudication": {
            **matching["proof_adjudication"],
            "state": "verified_error",
            "prosecution_coverage": "incomplete",
            "surviving_error_certificate_ids": ["c1"],
        },
    }
    assert select_route_guidance(compiled, result=non_matching) == {}


def test_owner_route_mapping_never_drifts_from_addressability() -> None:
    for owner, address in ROUTE_ADDRESSABILITY.items():
        assert OWNER_PRODUCER_ROUTE[owner] == address.producer_route, owner
    assert set(OWNER_PRODUCER_ROUTE) == set(MODULE_IDS)


def test_guidance_suffix_targets_only_the_requested_route() -> None:
    blocks = {"focused_prosecution_route": ("Verify cited events.",)}
    injected = _guidance_suffix("focused_prosecution_route", blocks)
    assert "Verify cited events." in injected
    assert _guidance_suffix("defense_route", blocks) == ""
    assert _guidance_suffix("focused_prosecution_route", None) == ""
    assert _guidance_suffix("focused_prosecution_route", {}) == ""


def _route_guidance_edit(guidance: str) -> SkillEdit:
    return SkillEdit(
        edit_id="edit_guidance",
        target_module="atomic_verifier_instruction",
        operation="add_rule",
        new_content="When verifying, follow the route guidance.",
        condition_spec={
            "schema_version": "sehd.condition_spec.v3",
            "feature_source": "court_result",
            "feature": "evidence_verified",
            "operator": "equals",
            "value_type": "boolean",
            "value": False,
            "scope": "l1_court_transform",
            "evidence_binding": "post_court_projection",
            "combinator": "atom",
            "binding": "entity_scoped",
        },
        route_guidance=guidance,
    )


def test_route_guidance_edit_admits_only_firewall_clean_text() -> None:
    genome = create_baseline_genome()
    ok, _ = validate_edit(
        _route_guidance_edit("Verify cited events independently."), genome,
    )
    assert ok is True
    ok, reason = validate_edit(
        _route_guidance_edit("Create a certificate for this candidate."),
        genome,
    )
    assert ok is False
    assert reason == "route_guidance_rejected_by_firewall"
    # An empty route_guidance field is not a route-guidance edit: it falls
    # through to the deterministic rule contract, which demands an action
    # spec (and rejects it accordingly).
    ok, reason = validate_edit(
        _route_guidance_edit(""), genome,
    )
    assert ok is False
    assert reason == "source_binding_proof_missing"
