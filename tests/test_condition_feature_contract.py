"""The condition-feature contract is single-sourced.

skills.vocabulary.contracts.CONDITION_FEATURES owns the predicate
vocabulary; skills.rule derives validation and prompt rendering from it and
skills.binding.project_binding_universe emits exactly the per-domain sets
derived from it, so extending the projection never requires a second edit
in the rule layer and pollution vocabulary never enters the conditionable
surface.
"""

from __future__ import annotations

from sehd_rise.skills.binding import project_binding_universe
from sehd_rise.skills.rule import (
    evaluate_condition_spec,
    render_condition_spec_contract,
    validate_condition_spec,
    validate_condition_spec_for_domain,
)
from sehd_rise.skills.vocabulary.contracts import (
    CONDITION_FEATURES,
    DOMAIN_CONDITION_FEATURES,
)


def _atom(feature: str, operator: str, value, value_type: str) -> dict:
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


def _full_result() -> tuple[dict, dict]:
    result = {
        "state": "verified_error",
        "census": [{
            "candidate_id": "C1",
            "candidate_kind": "unsupported_claim",
            "module_tag": "tool_use",
            "origin_hypothesis_event_id": "event_2",
            "axes": ["F"],
        }],
        "combined_prosecution": {"candidates": [{
            "certificate_id": "CERT1",
            "source_candidate_id": "C1",
            "origin_event_id": "event_2",
            "evidence_refs": ["event_2"],
        }]},
        "proof_adjudication": {
            "prosecution_coverage": "complete",
            "defense_coverage": "incomplete",
            "surviving_error_certificate_ids": ["CERT1"],
            "error_audits": [{
                "certificate_id": "CERT1",
                "decision": "pass",
                "missing_fields": ["anchor_id"],
                "failed_invariants": ["norm_evidence"],
            }],
        },
        "boundary_path": {
            "state": "verified_error",
            "responsible_event_id": "event_2",
        },
    }
    record = {
        "history": [
            {"role": "user", "content": "Check the rate."},
            {"role": "assistant", "tool_calls": [{"name": "search"}]},
            {"role": "tool", "tool_responses": ["error: timeout"]},
            {"role": "assistant", "content": "The rate is 17.4% [1]."},
        ],
    }
    return result, record


def test_every_declared_domain_feature_validates_and_renders() -> None:
    """Extending the contracts table needs no rule-layer edit: each declared
    feature validates on its domain and appears in the rendered contract."""
    for domain, features in DOMAIN_CONDITION_FEATURES.items():
        rendered = render_condition_spec_contract(binding_domains=(domain,))
        for feature in features:
            spec = CONDITION_FEATURES[feature]
            value = sorted(spec.values, key=str)[0]
            atom = _atom(feature, sorted(spec.operators)[0], value, spec.value_type)
            ok, reason = validate_condition_spec_for_domain(atom, domain)
            assert ok, f"{domain}/{feature}: {reason}"
            assert f"- {feature} /" in rendered


def test_projector_emits_exactly_the_declared_sets() -> None:
    result, record = _full_result()
    for domain, declared in DOMAIN_CONDITION_FEATURES.items():
        bindings = project_binding_universe(result, domain=domain, record=record)
        assert bindings, f"{domain}: projection unexpectedly empty"
        for binding in bindings:
            assert set(binding.features) <= declared, (
                f"{domain} emitted undeclared features: "
                f"{sorted(set(binding.features) - declared)}"
            )


def test_evaluator_vocabulary_never_enters_the_feature_surface() -> None:
    result, record = _full_result()
    poisoned_record = {
        **record,
        "case_id": "BFCL_000",
        "is_hallucination": True,
        "gold_label": "hallucinated",
        "hallucination_step": 3,
        "gold_steps": [3],
        "outer_feedback": "wrong",
    }
    forbidden = {
        "is_hallucination", "hallucination_step", "hallucination_reason",
        "hallucination_category", "hallucination_subcategory", "gold_label",
        "gold_steps", "gold_explanation", "outer_feedback", "case_id",
        "record_id", "dataset_id",
    }
    for domain in DOMAIN_CONDITION_FEATURES:
        if domain == "claim_anchor":
            # The anchor projector fail-fasts on a gold-carrying record:
            # the domain's pollution defense is the label-free firewall.
            try:
                project_binding_universe(
                    result, domain=domain, record=poisoned_record,
                )
            except ValueError:
                continue
            raise AssertionError("claim_anchor accepted a gold-carrying record")
        for binding in project_binding_universe(
            result, domain=domain, record=poisoned_record,
        ):
            assert not (set(binding.features) & forbidden), (
                f"{domain} leaked evaluator vocabulary: "
                f"{sorted(set(binding.features) & forbidden)}"
            )


def test_not_equals_is_the_scalar_symmetric_operator() -> None:
    boolean_atom = _atom("has_tool_error", "not_equals", True, "boolean")
    ok, reason = validate_condition_spec(boolean_atom)
    assert ok, reason
    matched = evaluate_condition_spec(boolean_atom, {"has_tool_error": False})
    assert matched.matched
    unequal = evaluate_condition_spec(boolean_atom, {"has_tool_error": True})
    assert not unequal.matched
    absent = evaluate_condition_spec(boolean_atom, {})
    assert not absent.matched


def test_not_equals_rejected_on_membership_features() -> None:
    atom = _atom("candidate_kind", "not_equals", "omission", "string")
    ok, reason = validate_condition_spec(atom)
    assert not ok
    assert reason.startswith("condition_operator_invalid:candidate_kind:")


def test_domain_contract_names_unbindable_features() -> None:
    """The 10b first-killer shape: the model is told candidate_kind does not
    bind on coverage instead of guessing and failing closed."""
    text = render_condition_spec_contract(binding_domains=("coverage",))
    assert "- coverage_state /" in text
    assert "- candidate_kind is NOT bindable in the coverage domain" in text
