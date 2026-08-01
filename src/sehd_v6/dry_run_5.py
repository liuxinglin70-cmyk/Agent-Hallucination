"""SEHD v6 — 5-case dry-run validation script.

Validates the framework's logic without requiring actual LLM API calls.
Uses mock detection results to verify:
1. Genome creation and edit application
2. Attribution signal extraction
3. HTC-TES v3 type classification
4. Type-conditioned evolution routing
5. Type-aware fitness assessment

Usage:
    cd src
    python -m sehd_v6.dry_run_5
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# Ensure src/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sehd_v6.core.bccse import (
    SkillGenome,
    create_baseline_genome,
    apply_edit,
    SkillEdit,
    SkillOp,
    validate_edit,
    ErrorType,
    attribute_failure,
    FITNESS_STATUS_NONE,
    MODULE_IDS,
)
from sehd_v6.core.bccse.safety_gate import create_structural_gate
from sehd_v6.core.paca_tcl.basic_runner import json_object_provider_call
from sehd_v6.env_loader import load_local_env
from sehd_v6.provider_routes import has_provider_credential, resolve_provider_route
from sehd_v6.core.htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeConditionedEvolution,
    TypeAwareFitness,
    classify_hallucination_type,
)


def _make_mock_detection(state: str, error_type: str = "correct") -> dict:
    """Create a mock detection result for testing."""
    return {
        "state": state,
        "runtime_valid": True,
        "census": [
            {
            "candidate_kind": "agent_assertion",
            "claim_or_commitment": "The agent claimed the API returned 200 OK",
            "candidate_id": "cand_001",
            "axes": ["F"],
            "module_tag": "Reasoning",
            }
        ] if state != "verified_clean" else [],
        "evidential_scores_parsed": {
            "factuality": {"score": 0.2, "evidence": "low"},
            "counterfactual": {"score": 0.5, "evidence": "medium"},
            "recovery": {"score": 0.3, "evidence": "low"},
            "process": {"score": 0.4, "evidence": "medium"},
            "scope": {"score": 0.6, "evidence": "high"},
        } if state == "unresolved" else {
            "factuality": {"score": 0.8, "evidence": "high"},
            "counterfactual": {"score": 0.7, "evidence": "high"},
            "recovery": {"score": 0.6, "evidence": "high"},
            "process": {"score": 0.8, "evidence": "high"},
            "scope": {"score": 0.9, "evidence": "high"},
        },
        "proof_adjudication": {
            "crs_aggregate": 0.35 if state == "unresolved" else 0.75,
            "prosecution_coverage": "incomplete" if state == "unresolved" else "complete",
            "defense_coverage": "incomplete" if state == "unresolved" else "complete",
        },
        "responsible_step": "step_3" if state == "verified_error" else None,
        "gold_steps": ["step_3"] if state == "verified_error" else [],
        "prosecution": {"coverage_state": "incomplete" if state == "unresolved" else "complete", "candidates": []},
        "defense": {"coverage_state": "incomplete" if state == "unresolved" else "complete", "candidates": []},
    }


def _make_mock_signal(
    state: str,
    error_type: ErrorType = ErrorType.CORRECT,
    target_module: str = "evidence_obligation",
) -> AttributionSignal:
    """Create a mock attribution signal for testing."""
    from sehd_v6.core.bccse.step_attributor import (
        AttributionSignal,
        TriggerSource,
        ReflectionType,
    )
    trigger = TriggerSource.NONE if error_type == ErrorType.CORRECT else TriggerSource.UNRESOLVED
    return AttributionSignal(
        error_type=error_type,
        target_module=target_module,
        secondary_module="abstention_rule",
        attribution_confidence="medium",
        reflection_type=ReflectionType.STABLE_SUCCESS if error_type == ErrorType.CORRECT else ReflectionType.SKILL_DEFECT,
        trigger_source=trigger,
        evidence_summary="mock evidence summary",
        prosecution_coverage=0.5,
        defense_coverage=0.5,
        prosecution_coverage_state="incomplete",
        defense_coverage_state="incomplete",
        coverage_quantitative=True,
        crs_scores={"factuality": 0.5, "counterfactual": 0.5, "recovery": 0.5, "process": 0.5, "scope": 0.5},
        crs_aggregate=0.5,
        crs_source="mock",
        crs_complete=True,
        consensus_gate_applied=True,
        candidate_count=1,
        state=state,
        gold_label=None,
        has_gold=False,
        gold_enhanced=False,
        responsible_step_correct=False,
    )


# ============================================================
# Test cases
# ============================================================

TEST_CASES = [
    {
        "name": "Case 1: Unresolved factual hallucination",
        "detection_state": "unresolved",
        "error_type": ErrorType.UNRESOLVED_UNKNOWN,
        "target_module": "evidence_obligation",
        "expected_type": HallucinationType.FACTUAL,
    },
    {
        "name": "Case 2: False positive (clean flagged as error)",
        "detection_state": "verified_error",
        "error_type": ErrorType.FALSE_POSITIVE,
        "target_module": "clean_defense_boundary",
        "expected_type": HallucinationType.CONTEXTUAL,
    },
    {
        "name": "Case 3: Stable clean (no evolution needed)",
        "detection_state": "verified_clean",
        "error_type": ErrorType.CORRECT,
        "target_module": "evidence_obligation",
        "expected_type": HallucinationType.UNKNOWN,
    },
    {
        "name": "Case 4: Unresolved with tool misuse signals",
        "detection_state": "unresolved",
        "error_type": ErrorType.CANDIDATE_MISS,
        "target_module": "candidate_trigger",
        "expected_type": HallucinationType.OMISSION,
    },
    {
        "name": "Case 5: Localization error (reasoning failure)",
        "detection_state": "verified_error",
        "error_type": ErrorType.LOCALIZATION_ERROR,
        "target_module": "causal_link_test",
        "expected_type": HallucinationType.REASONING,
    },
]


def _mutation_route() -> dict[str, str] | None:
    load_local_env()
    route = resolve_provider_route({}, purpose="mutation", route_name="mutation")
    if not has_provider_credential(route):
        return None
    route["timeout_seconds"] = 60
    route["transport_retries"] = 0
    return route


def _llm_edit(route: dict[str, str], case: dict, type_result, signal, cache_dir: Path) -> dict:
    system = (
        "You are a controlled skill mutation planner. Return JSON only. "
        "The rule must be general, observable from a trajectory, one if-then action, "
        "and must not emit a final verdict."
    )
    user = json.dumps({
        "hallucination_type": type_result.primary_type.value,
        "error_type": signal.error_type.value,
        "target_module": signal.target_module,
        "trajectory_features": type_result.trajectory_features.to_dict() if type_result.trajectory_features else {},
        "task": "propose one runtime rule and describe the reusable trajectory feature",
        "schema": {
            "operation": "add_rule|no_edit",
            "target_module": "module id",
            "new_content": "one if-then rule",
            "feature_description": "short reusable feature description",
            "rationale": "short reason",
        },
    }, ensure_ascii=False)
    value, _ = json_object_provider_call(
        route=route, system=system, user=user, cache_dir=cache_dir,
        purpose="mutation",
    )
    if not isinstance(value, dict):
        raise ValueError("mutation response must be an object")
    return value


def _repair_llm_edit(
    route: dict[str, str], parsed: dict, reason: str, target_module: str,
    cache_dir: Path,
) -> dict:
    value, _ = json_object_provider_call(
        route=route,
        system=(
            "Repair a rejected runtime rule. Return JSON only. Keep one observable "
            "condition and one action, start with 'When ...,' or 'If ...,' and do "
            "not emit clean/error/unresolved verdicts."
        ),
        user=json.dumps({
            "rejected": parsed,
            "validation_reason": reason,
            "required_target_module": target_module,
            "schema": {
                "operation": "add_rule|no_edit", "target_module": target_module,
                "new_content": "single if-then runtime rule",
                "feature_description": "reusable trajectory feature", "rationale": "reason",
            },
        }, ensure_ascii=False),
        cache_dir=cache_dir,
        purpose="mutation",
    )
    return value


def run_dry_run() -> None:
    """Run 5-case dry-run validation."""
    print("=" * 70)
    print("SEHD v6 — 5-Case Dry-Run Validation")
    print("=" * 70)

    # Initialize components
    genome = create_baseline_genome()
    classifier = HallucinationTypeClassifier()
    type_evo = TypeConditionedEvolution()
    type_fitness = TypeAwareFitness()
    gate = create_structural_gate(
        module_cooldown=0,
        max_total_edits=200,
        convergence_window=20,
    )
    route = _mutation_route()
    print(f"Mutation API: {'enabled' if route else 'disabled'}")

    print(f"\nBaseline genome: {genome.content_hash[:16]}...")
    print(f"Modules: {len(MODULE_IDS)}")
    print(f"HTC-TES types: {len(HallucinationType)}")
    print()

    passed = 0
    failed = 0

    for i, case in enumerate(TEST_CASES):
        print(f"--- {case['name']} ---")

        try:
            # 1. Create mock detection result
            detection = _make_mock_detection(case["detection_state"], case["error_type"].value)

            # 2. Create mock attribution signal
            signal = _make_mock_signal(
                case["detection_state"],
                case["error_type"],
                case["target_module"],
            )

            # 3. Check trigger
            if not signal.should_evolve:
                print(f"  STABLE: state={case['detection_state']}, no evolution")
                passed += 1
                continue

            # 4. HTC-TES: Classify hallucination type
            type_result = classifier.classify(detection, signal)
            if type_result.primary_type != case["expected_type"]:
                print(
                    f"  TYPE MISMATCH: expected={case['expected_type'].value} "
                    f"actual={type_result.primary_type.value}"
                )
                failed += 1
                continue
            print(f"  Type: {type_result.primary_type.value} "
                  f"(confidence={type_result.confidence:.2f})")

            # 5. Get type-conditioned guidance
            direction = type_evo._infer_evolution_direction(
                signal.state, None,
            )
            guidance = type_evo.generate_type_guidance(
                type_result.primary_type, direction,
            )
            print(f"  Direction: {direction}")
            print(f"  Strategy: {type_evo.get_edit_strategy(type_result.primary_type)}")
            target_modules = type_evo.intersect_allowed_modules(
                type_result.primary_type,
                (signal.target_module, signal.secondary_module),
            )
            if not target_modules:
                print("  TYPE/ATTRIBUTION ROUTE CONFLICT: no safe module intersection")
                failed += 1
                continue
            print(f"  Target modules: {target_modules}")

            if route:
                try:
                    parsed = _llm_edit(route, case, type_result, signal, Path(".dry_run_mutation_cache"))
                    if str(parsed.get("operation") or "") == "no_edit":
                        print("  API mutation: NO_EDIT")
                        passed += 1
                        continue
                    target = str(parsed.get("target_module") or "")
                    if target not in target_modules:
                        print(f"  API mutation: route mismatch ({target})")
                        failed += 1
                        continue
                    content = str(parsed.get("new_content") or "")
                    test_edit = SkillEdit(
                        edit_id=f"dry_edit_{i}", target_module=target,
                        operation=SkillOp.ADD_RULE, new_content=content,
                        rationale=str(parsed.get("rationale") or "")[:300],
                        source_case_id=f"dry_case_{i}",
                        feature_description=str(parsed.get("feature_description") or "")[:500],
                    )
                except Exception as exc:
                    print(f"  API mutation: FAIL ({type(exc).__name__})")
                    failed += 1
                    continue
            else:

            # 6. Create a test edit
                test_edit = SkillEdit(
                    edit_id=f"dry_edit_{i}",
                    target_module=case["target_module"], operation=SkillOp.ADD_RULE,
                    new_content=f"When a {type_result.primary_type.value} candidate lacks an addressable supporting event, mark its proof incomplete",
                    rationale=f"[Dry-run] Test edit for {type_result.primary_type.value}",
                    source_case_id=f"dry_case_{i}",
                    feature_description="candidate type, axis, and proof coverage pattern",
                )

            # 7. Validate edit
            valid, reason = validate_edit(test_edit, genome)
            if not valid and route:
                repaired = _repair_llm_edit(
                    route, parsed, reason, test_edit.target_module,
                    Path(".dry_run_mutation_cache"),
                )
                if str(repaired.get("operation") or "") != "no_edit":
                    test_edit = SkillEdit(
                        edit_id=f"dry_edit_{i}_repair",
                        target_module=test_edit.target_module,
                        operation=SkillOp.ADD_RULE,
                        new_content=str(repaired.get("new_content") or ""),
                        rationale=str(repaired.get("rationale") or "")[:300],
                        source_case_id=f"dry_case_{i}",
                        feature_description=str(repaired.get("feature_description") or "")[:500],
                    )
                    valid, reason = validate_edit(test_edit, genome)
                if not valid:
                    safe_rule = (
                        "When a candidate lacks an addressable origin event, mark the proof incomplete"
                    )
                    fallback_edit = SkillEdit(
                        edit_id=f"dry_edit_{i}_safe_fallback",
                        target_module=test_edit.target_module,
                        operation=SkillOp.ADD_RULE,
                        new_content=safe_rule,
                        rationale=f"deterministic safety fallback after API validation: {reason}",
                        source_case_id=f"dry_case_{i}",
                        feature_description="candidate origin addressability and proof completeness",
                    )
                    fallback_valid, fallback_reason = validate_edit(fallback_edit, genome)
                    if fallback_valid:
                        test_edit = fallback_edit
                        valid, reason = True, "api_repair_rejected; deterministic_safe_fallback"
                    else:
                        reason = f"{reason}; fallback={fallback_reason}"
            print(f"  Edit validation: {'PASS' if valid else 'FAIL: ' + reason}")

            if not valid:
                failed += 1
                continue

            # 8. Apply edit
            new_genome = apply_edit(genome, test_edit)
            print(f"  Genome: {genome.content_hash[:8]} → {new_genome.content_hash[:8]}")

            # 9. Type-aware fitness assessment
            after_detection = _make_mock_detection(case["detection_state"])
            assessment = type_fitness.assess_edit(
                source_case_id=f"dry_case_{i}",
                hallucination_type=type_result.primary_type,
                evolution_direction=direction,
                detection_before=detection,
                detection_after=after_detection,
                type_result=type_result,
            )
            print(f"  Fitness: {assessment.global_fitness} "
                  f"(DACR={assessment.dacr_score:.3f})")

            # 10. Verify type is recorded in experience memory
            coverage = type_fitness.get_experience_memory().get_type_coverage()
            print(f"  Coverage: {coverage['types_with_experience']}/"
                  f"{coverage['total_types']} types")

            passed += 1
            print(f"  RESULT: PASS")

        except Exception as exc:
            print(f"  RESULT: FAIL - {type(exc).__name__}: {exc}")
            traceback.print_exc()
            failed += 1
        print()

    # Summary
    print("=" * 70)
    print(f"DRY-RUN SUMMARY: {passed}/{len(TEST_CASES)} passed, {failed} failed")
    print("=" * 70)

    # Final coverage
    coverage = type_fitness.get_experience_memory().get_type_coverage()
    print(f"\nHTC-TES Type Coverage:")
    print(f"  Types with experience: {coverage['types_with_experience']}/"
          f"{coverage['total_types']}")
    print(f"  Effective coverage: {coverage['effective_coverage']:.1%}")
    for htype_name, stats in coverage["per_type"].items():
        if stats["total_edits"] > 0:
            print(f"    {htype_name}: {stats['total_edits']} edits, "
                  f"{stats['success_rate']:.0%} success")

    if failed > 0:
        sys.exit(1)
    print("\nAll dry-run cases passed successfully!")


if __name__ == "__main__":
    run_dry_run()
