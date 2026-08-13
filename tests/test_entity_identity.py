"""Entity-identity / generalization-predicate split tests (review section 6).

The binding contract is now two-layer:

- ``target_entity_id`` names the training entity (identity, unambiguous);
- ``condition_spec`` is the REUSE predicate (generalization).

An over-broad predicate that would otherwise return BINDING_AMBIGUOUS must bind
exactly the named entity on the source record, while still generalizing to the
full predicate on other records (where the named id is absent).
"""

from __future__ import annotations

import copy

from sehd_rise.planning.candidates import _resolve_source_binding
from sehd_rise.skills.anchors import project_claim_anchors
from sehd_rise.skills.binding import (
    BINDING_AMBIGUOUS,
    BINDING_BOUND,
    BINDING_NOT_FOUND,
    match_condition_bindings,
    predicate_matches_binding,
    render_binding_menu,
)


def _record() -> dict:
    return {
        "history": [
            {"role": "user", "content": "Check the release date."},
            {"role": "assistant", "content": "The package was released on March 3."},
            {"role": "assistant", "content": "It also added a new API endpoint."},
        ],
    }


def _broad_role_spec() -> dict:
    """True on every assistant anchor — over-broad, ambiguous on its own."""
    return {
        "schema_version": "sehd.condition_spec.v3",
        "feature_source": "court_result",
        "feature": "anchor_role",
        "operator": "contains",
        "value_type": "string",
        "value": "assistant",
        "scope": "l1_court_transform",
        "evidence_binding": "post_court_projection",
        "combinator": "atom",
        "binding": "entity_scoped",
    }


def _baseline() -> dict:
    return {
        "state": "verified_clean",
        "census": [],
        "proof_adjudication": {
            "state": "verified_clean",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": [],
        },
    }


def test_overbroad_predicate_is_ambiguous_without_identity() -> None:
    anchors = project_claim_anchors(_record())
    assert len(anchors) >= 2
    match = match_condition_bindings(
        _broad_role_spec(), _baseline(), domain="claim_anchor",
        cardinality="exactly_one", record=_record(),
    )
    assert match.status == BINDING_AMBIGUOUS


def test_entity_identity_disambiguates_overbroad_predicate() -> None:
    anchors = project_claim_anchors(_record())
    target = anchors[0].anchor_id
    match = match_condition_bindings(
        _broad_role_spec(), _baseline(), domain="claim_anchor",
        cardinality="exactly_one", record=_record(),
        preferred_entity_id=target,
    )
    assert match.status == BINDING_BOUND
    assert match.reason == "preferred_entity_grounded"
    assert [binding.entity_id for binding in match.bindings] == [target]


def test_predicate_consistency_check_on_named_entity() -> None:
    anchors = project_claim_anchors(_record())
    # The first anchor is "The package was released on March 3." — numeric.
    numeric = next(a for a in anchors if a.observable_features["anchor_has_number"])
    numeric_spec = {
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
    binding = match_condition_bindings(
        numeric_spec, _baseline(), domain="claim_anchor",
        cardinality="exactly_one", record=_record(),
        preferred_entity_id=numeric.anchor_id,
    )
    assert binding.status == BINDING_BOUND
    assert predicate_matches_binding(numeric_spec, binding.bindings[0]) is True


def test_resolve_source_binding_rejects_inconsistent_named_entity() -> None:
    anchors = project_claim_anchors(_record())
    non_numeric = next(a for a in anchors if not a.observable_features["anchor_has_number"])
    numeric_spec = {
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
    match = _resolve_source_binding(
        {"condition_spec": numeric_spec, "target_entity_id": non_numeric.anchor_id},
        _baseline(), domain="claim_anchor", cardinality="exactly_one",
        record=_record(),
    )
    assert match.status == BINDING_NOT_FOUND
    assert match.reason.startswith("condition_not_consistent_with_target_entity")


def test_resolve_source_binding_grounds_named_entity() -> None:
    anchors = project_claim_anchors(_record())
    target = anchors[0].anchor_id
    match = _resolve_source_binding(
        {"condition_spec": _broad_role_spec(), "target_entity_id": target},
        _baseline(), domain="claim_anchor", cardinality="exactly_one",
        record=_record(),
    )
    assert match.status == BINDING_BOUND
    assert match.bindings[0].entity_id == target


def test_render_binding_menu_is_prompt_safe_and_deterministic() -> None:
    first = render_binding_menu(_baseline(), "claim_anchor", record=_record())
    second = render_binding_menu(
        _baseline(), "claim_anchor", record=copy.deepcopy(_record()),
    )
    assert [row["entity_id"] for row in first] == [row["entity_id"] for row in second]
    assert first
    assert all("text" in row for row in first)
    assert "gold" not in str(first).lower() or "gold_label" not in str(first)
