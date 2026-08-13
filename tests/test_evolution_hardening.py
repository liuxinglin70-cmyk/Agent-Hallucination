"""Framework-hardening regressions: adaptive budget, L1 feature vocabulary,
Goodhart divergence, lineage diversity, and meta evidence wiring.

These lock the self-evolution framework's "greater freedom without
over-freedom" changes: they verify that the search budget scales on the
highest-value gold-polarity signal, that the new L1 observable feature is
projected and validated through the closed vocabulary, that the Goodhart /
collapse telemetry is pure and totals correctly, and that the meta learner's
newly-wired evidence sources are the exact closed-code mappings.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.causal.cut import (
    REASON_GOLD_FALSE_NEGATIVE_CLEAN,
    REASON_GOLD_FALSE_POSITIVE,
)
from sehd_rise.causal.episode import build_runtime_observation
from sehd_rise.causal.signals import project_detection
from sehd_rise.loop.result import (
    EvolutionStep,
    _goodhart_divergence,
    _lineage_diversity,
)
from sehd_rise.loop.runner import _MAX_ATTEMPT_CAP, _attempt_cap
from sehd_rise.meta.review import (
    FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE,
    FEEDBACK_EXECUTION_NOT_ESTABLISHED,
    FEEDBACK_INDEPENDENT_REGRESSION_RISK,
    FEEDBACK_INSUFFICIENT_GROUNDING,
    FEEDBACK_REDUNDANT_INTERVENTION,
    FEEDBACK_EVIDENCE_SOURCES,
    project_strategy_for_prompt,
)
from sehd_rise.meta.strategy import (
    MECHANISM_RANKING,
    OPERATION_PREFERENCE,
    SEARCH_PRIOR,
    StrategyState,
)
from sehd_rise.planning.candidates import _prevalidate_candidate
from sehd_rise.skills.genome import create_baseline_genome
from sehd_rise.skills.policy import create_baseline_policy_genome
from sehd_rise.skills.rule import (
    evaluate_condition_spec,
    project_result_condition_features,
    validate_condition_spec,
)


# ============================================================
# #10 — adaptive search budget
# ============================================================

def test_gold_polarity_cases_get_attempt_bonus() -> None:
    assert _attempt_cap(2, REASON_GOLD_FALSE_POSITIVE) == 4
    assert _attempt_cap(2, REASON_GOLD_FALSE_NEGATIVE_CLEAN) == 4


def test_non_polarity_cases_keep_configured_cap() -> None:
    assert _attempt_cap(2, "provider_artifact_missing") == 2
    assert _attempt_cap(3, "consensus_gate_blocked") == 3


def test_attempt_cap_is_ceiling_bounded() -> None:
    assert _attempt_cap(5, REASON_GOLD_FALSE_POSITIVE) == _MAX_ATTEMPT_CAP


# ============================================================
# #4 — L1 observable feature vocabulary (has_tool_retry)
# ============================================================

_RETRY_SPEC = {
    "schema_version": "sehd.condition_spec.v3",
    "feature_source": "court_result",
    "feature": "has_tool_retry",
    "operator": "equals",
    "value_type": "boolean",
    "value": True,
    "scope": "l1_court_transform",
    "evidence_binding": "post_court_projection",
    "combinator": "atom",
    "binding": "entity_scoped",
}


def test_has_tool_retry_project_and_evaluate() -> None:
    record = {
        "history": [
            {"event_id": "event_001", "role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
            {"event_id": "event_002", "role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
        ]
    }
    features = project_result_condition_features({}, record)
    assert features["has_tool_retry"] is True
    assert features["has_tool_call"] is True
    assert validate_condition_spec(_RETRY_SPEC)[0] is True
    assert evaluate_condition_spec(_RETRY_SPEC, features).matched is True


def test_has_tool_retry_false_on_single_call() -> None:
    record = {
        "history": [
            {"event_id": "event_001", "role": "assistant", "tool_calls": [
                {"name": "search", "arguments": {}}]},
        ]
    }
    features = project_result_condition_features({}, record)
    assert features["has_tool_retry"] is False


# ============================================================
# #4c — trajectory-content condition feature (module_tag)
# ============================================================

_MODULE_TAG_SPEC = {
    "schema_version": "sehd.condition_spec.v3",
    "feature_source": "court_result",
    "feature": "module_tag",
    "operator": "contains",
    "value_type": "string",
    "value": "tool_use",
    "scope": "l1_court_transform",
    "evidence_binding": "post_court_projection",
    "combinator": "atom",
    "binding": "entity_scoped",
}


def test_module_tag_project_normalizes_and_evaluates() -> None:
    result = {
        "census": [
            {"candidate_id": "c1", "module_tag": "Tool-Use"},
        ]
    }
    features = project_result_condition_features(result)
    assert features["module_tag"] == ["tool_use"]
    assert validate_condition_spec(_MODULE_TAG_SPEC)[0] is True
    assert evaluate_condition_spec(_MODULE_TAG_SPEC, features).matched is True


def test_module_tag_rejects_unregistered_value() -> None:
    unregistered = dict(_MODULE_TAG_SPEC, value="not_a_tag")
    ok, reason = validate_condition_spec(unregistered)
    assert ok is False
    assert "condition_value_unregistered" in reason


def test_condition_binding_only_entity_scoped() -> None:
    """Only the implemented court-result binding is admitted."""
    ok, _ = validate_condition_spec(_MODULE_TAG_SPEC)
    assert ok is True
    unimplemented = dict(_MODULE_TAG_SPEC, binding="candidate_bound")
    ok, reason = validate_condition_spec(unimplemented)
    assert ok is False
    assert "condition_binding_invalid" in reason


# ============================================================
# #4b — condition-feature trace wiring (P0)
# ============================================================

_MISSING_FIELD_SPEC = {
    "schema_version": "sehd.condition_spec.v3",
    "feature_source": "court_result",
    "feature": "missing_field",
    "operator": "contains",
    "value_type": "string",
    "value": "responsible_event_id",
    "scope": "l1_court_transform",
    "evidence_binding": "post_court_projection",
    "combinator": "atom",
    "binding": "entity_scoped",
}


def _origin_repair_candidate() -> dict:
    return {
        "target_module": "earliest_origin_tiebreak",
        "operation": "add_rule",
        "condition": "the responsible event id is absent",
        "action": "select_earliest_origin: responsible_event_id",
        "evidence_anchor": "localization_ambiguous",
        "condition_spec": _MISSING_FIELD_SPEC,
    }


def _source_detection_result(state: str = "verified_error") -> dict:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "proof_adjudication": {
            "state": state,
            "decision_authority": "authoritative",
            "surviving_error_certificate_ids": ["e1"],
        },
    }


def test_runtime_observation_threads_condition_feature_trace() -> None:
    trace = {"missing_field": ["responsible_event_id"], "candidate_kind": ["omission"]}
    signal = project_detection(_source_detection_result())
    observation = build_runtime_observation(
        signal, allowed_modules=("earliest_origin_tiebreak",),
        condition_feature_trace=trace,
    )
    assert observation["runtime_condition_features"] == trace


def test_runtime_observation_defaults_to_empty_trace() -> None:
    signal = project_detection(_source_detection_result())
    observation = build_runtime_observation(
        signal, allowed_modules=("earliest_origin_tiebreak",),
    )
    assert observation["runtime_condition_features"] == {}


def test_prevalidate_rejects_unmatchable_condition_on_source() -> None:
    trace = {"candidate_kind": ["omission"], "missing_field": []}
    reason = _prevalidate_candidate(
        _origin_repair_candidate(), ("earliest_origin_tiebreak",),
        runtime_condition_features=trace,
    )
    assert reason == "condition_not_observed_on_source_route"


def test_prevalidate_accepts_matchable_condition_on_source() -> None:
    trace = {"candidate_kind": ["omission"], "missing_field": ["responsible_event_id"]}
    reason = _prevalidate_candidate(
        _origin_repair_candidate(), ("earliest_origin_tiebreak",),
        runtime_condition_features=trace,
    )
    assert reason is None


# ============================================================
# #11 — Goodhart divergence telemetry
# ============================================================

def _step(gate_decision: str, assessment: dict | None) -> EvolutionStep:
    return EvolutionStep(
        case_id="c", case_index=0, trigger_source="t", attribution=None,
        edit_proposal=None, mutation_applied=None,
        gate_decision=gate_decision, genome_hash_before="a",
        genome_hash_after="b", detection_state="unresolved", timestamp=0.0,
        fitness_check={"assessment": assessment} if assessment else None,
    )


def test_goodhart_divergence_separates_proxy_and_target() -> None:
    steps = [
        _step("provisional", {"decision": "directional_evidence"}),
        _step("ACCEPTED", {"decision": "strong_improvement",
                           "task_score_before": 0, "task_score_after": 2}),
        _step("SOURCE_NOT_IMPROVED", {"decision": "worse",
                                      "task_score_before": 2,
                                      "task_score_after": 0}),
    ]
    divergence = _goodhart_divergence(steps)
    assert divergence["proxy_only_movement"] == 1
    assert divergence["target_improved"] == 1
    assert divergence["target_regressed"] == 1
    # 1 proxy-only / (1 proxy-only + 1 target-improved) = 0.5
    assert abs(divergence["divergence_rate"] - 0.5) < 1e-9


def test_goodhart_divergence_empty_is_zero() -> None:
    divergence = _goodhart_divergence([])
    assert divergence["proxy_only_movement"] == 0
    assert divergence["divergence_rate"] == 0.0


# ============================================================
# #12 — lineage diversity / collapse telemetry
# ============================================================

def test_lineage_diversity_counts_evolved_rules() -> None:
    genome = create_baseline_genome()
    policy = create_baseline_policy_genome()
    summary = _lineage_diversity([], genome, policy)
    assert summary["evolved_rules_total"] == 0
    assert summary["l2_working_items"] == 0
    assert summary["collapse_flags"]["no_evolved_rules"] is True


# ============================================================
# meta — newly-wired evidence sources
# ============================================================

def test_meta_evidence_sources_wired() -> None:
    assert FEEDBACK_EVIDENCE_SOURCES[
        ("search_prior", "prefer_observable_discriminator")
    ] == (FEEDBACK_INSUFFICIENT_GROUNDING,)
    assert FEEDBACK_EVIDENCE_SOURCES[
        ("search_prior", "require_independent_anchor")
    ] == (FEEDBACK_INDEPENDENT_REGRESSION_RISK,)
    assert FEEDBACK_EVIDENCE_SOURCES[
        ("candidate_diversity", "seek_distinct_candidate_direction")
    ] == (FEEDBACK_REDUNDANT_INTERVENTION,)
    assert FEEDBACK_EVIDENCE_SOURCES[
        ("candidate_diversity", "duplicate_risk")
    ] == (FEEDBACK_REDUNDANT_INTERVENTION, FEEDBACK_CANDIDATE_QUALITY_INCONCLUSIVE)
    assert FEEDBACK_EVIDENCE_SOURCES[
        ("retry_strategy", "avoid_unexecuted_action_shape")
    ] == (FEEDBACK_EXECUTION_NOT_ESTABLISHED,)


def test_strategy_projection_matches_reflection_consumers() -> None:
    """The reflection prompt builders read guidance_tokens /
    ranking_preferences; the projection must emit exactly those keys."""
    state = StrategyState(state_id="s")
    state.enabled = {
        SEARCH_PRIOR: ("prefer_observable_discriminator",),
        OPERATION_PREFERENCE: ("cost_efficiency",),
        MECHANISM_RANKING: ("action_shape_previously_effective",),
    }
    projection = project_strategy_for_prompt(state)
    assert projection["authority"] == "evolution_guidance_only"
    assert set(projection["guidance_tokens"]) == {
        "prefer_observable_discriminator", "cost_efficiency",
    }
    assert projection["ranking_preferences"] == [
        "action_shape_previously_effective",
    ]


def test_strategy_projection_empty_on_baseline() -> None:
    projection = project_strategy_for_prompt(StrategyState(state_id="b"))
    assert projection["guidance_tokens"] == []
    assert projection["ranking_preferences"] == []


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
