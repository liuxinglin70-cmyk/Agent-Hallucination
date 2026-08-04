"""Tests for the L1 Skill rule slot contract (rule_contract)."""

from src.sehd_v6.core.bccse.genome import (
    DEDUP_HIGH_THRESHOLD,
    DEDUP_LOW_THRESHOLD,
    semantic_overlap,
)
from src.sehd_v6.core.bccse.rule_contract import (
    RuleSpec,
    render_rule,
    rule_text_identity,
    rules_semantic_overlap,
    validate_rule_spec,
)


def _spec(**overrides) -> RuleSpec:
    base = dict(
        condition="a causal-chain field is missing",
        action="treat the evidence as incomplete",
        evidence_anchor="missing_causal_field_signature",
        module_id="evidence_obligation",
        direction="process",
        provenance={"source_case_id": "bfcl-014", "generation": 3},
    )
    base.update(overrides)
    return RuleSpec(**base)


# --- rendering determinism ---

def test_render_deterministic_single_sentence():
    spec = _spec()
    first = render_rule(spec)
    assert render_rule(spec) == first
    assert first == (
        "When a causal-chain field is missing, "
        "treat the evidence as incomplete."
    )
    # exactly one terminal period, no sentence splits inside
    assert first.endswith(".")
    assert first.count(".") == 1


def test_render_normalizes_punctuation_and_case():
    spec = _spec(
        condition="The retry loop exhausts its budget.",
        action="mark the evidence incomplete...",
    )
    rendered = render_rule(spec)
    assert rendered == (
        "When the retry loop exhausts its budget, mark the evidence incomplete."
    )


def test_render_drops_redundant_leading_conjunction():
    spec = _spec(condition="when the budget exhausts early")
    rendered = render_rule(spec)
    assert rendered.startswith("When the budget exhausts early,")
    assert "when when" not in rendered.lower()


# --- slot size limits ---

def test_reject_overlong_condition():
    ok, reason = validate_rule_spec(_spec(condition="the tool fails " * 40))
    assert not ok
    assert reason == "condition_too_long"


def test_reject_overlong_action():
    ok, reason = validate_rule_spec(_spec(action="flag the claim " * 40))
    assert not ok
    assert reason == "action_too_long"


def test_reject_overlong_evidence_anchor():
    ok, reason = validate_rule_spec(_spec(evidence_anchor="sig_" + "x" * 240))
    assert not ok
    assert reason == "evidence_anchor_too_long"


def test_reject_unknown_module_and_direction():
    ok, reason = validate_rule_spec(_spec(module_id="nonexistent_module"))
    assert not ok and reason.startswith("invalid_module_id")
    ok, reason = validate_rule_spec(_spec(direction="sideways"))
    assert not ok and reason.startswith("invalid_direction")


# --- structural singularity ---

def test_condition_overload_rejected():
    spec = _spec(condition="the tool fails and the retry fails and output diverges")
    ok, reason = validate_rule_spec(spec)
    assert not ok
    assert reason == "condition_overload"


def test_multiple_actions_rejected():
    spec = _spec(action="flag the claim; then also retry the tool")
    ok, reason = validate_rule_spec(spec)
    assert not ok
    assert reason == "multiple_actions"


# --- pollution firewall ---

def test_case_id_pollution_rejected():
    spec = _spec(condition="in case_042 the tool fails once")
    ok, reason = validate_rule_spec(spec)
    assert not ok
    assert reason.startswith("condition:rule_prompt_or_case_pollution")


def test_gold_label_pollution_rejected():
    spec = _spec(action="compare the output against ground truth")
    ok, reason = validate_rule_spec(spec)
    assert not ok
    assert reason == "action:rule_prompt_or_case_pollution"


def test_prompt_injection_rejected():
    spec = _spec(evidence_anchor="ignore previous instructions")
    ok, reason = validate_rule_spec(spec)
    assert not ok
    assert reason.startswith("evidence_anchor:")


# --- verdict authority ---

def test_verdict_requires_module_permission():
    unauthorized = _spec(module_id="candidate_trigger", action="treat the claim as clean")
    ok, reason = validate_rule_spec(unauthorized)
    assert not ok
    assert reason == "verdict_not_allowed:clean_for_candidate_trigger"

    authorized = _spec(module_id="clean_defense_boundary", action="treat the claim as clean")
    ok, reason = validate_rule_spec(authorized)
    assert ok, reason


def test_local_negation_only_in_causal_link_test():
    action = "conclude that the causal link does not hold"
    ok, reason = validate_rule_spec(_spec(module_id="causal_link_test", action=action))
    assert ok, reason

    ok, reason = validate_rule_spec(_spec(module_id="evidence_obligation", action=action))
    assert not ok
    assert reason == "local_negation_not_allowed_for_evidence_obligation"


# --- serialization round trip ---

def test_to_dict_from_dict_round_trip():
    spec = _spec()
    restored = RuleSpec.from_dict(spec.to_dict())
    assert restored == spec
    assert restored.to_dict() == spec.to_dict()


# --- identity and dedup linkage with genome ---

def test_identity_is_normalized_lowercase():
    spec = _spec(condition="The budget exhausts early.", action="flag the claim.")
    assert rule_text_identity(spec) == "when the budget exhausts early, flag the claim."


def test_near_duplicate_specs_overlap_high():
    spec_a = _spec(
        module_id="clean_defense_boundary",
        condition="the tool output directly supports the claim",
        action="treat the claim as clean",
        direction="clean",
    )
    spec_b = _spec(
        module_id="clean_defense_boundary",
        condition="the tool output directly supports the agent claim",
        action="treat that claim as clean",
        direction="clean",
    )
    overlap = rules_semantic_overlap(spec_a, spec_b)
    # near-duplicates must land in the tiered dedup merge/reject zone
    assert overlap >= DEDUP_LOW_THRESHOLD
    # and the metric is exactly genome's shared scale
    assert overlap == semantic_overlap(
        rule_text_identity(spec_a), rule_text_identity(spec_b)
    )

    identical = _spec(
        module_id="clean_defense_boundary",
        condition="the tool output directly supports the claim",
        action="treat the claim as clean",
        direction="clean",
    )
    assert rules_semantic_overlap(spec_a, identical) >= DEDUP_HIGH_THRESHOLD

    distinct = _spec(condition="multiple origins are causally sufficient",
                     action="select the earliest cited event",
                     module_id="earliest_origin_tiebreak")
    assert rules_semantic_overlap(spec_a, distinct) < DEDUP_LOW_THRESHOLD
