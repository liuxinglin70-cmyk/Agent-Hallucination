"""SEHD v6 — 50-case trial evolution experiment.

Runs the HTC-TES v3 self-evolution loop on 50 cases using baseline predictions
from data/baseline_predictions_by_id.json. Generates mock detection results
enriched with CRS scores, coverage, and proof_adjudication fields so that
attribute_failure() and the HTC-TES pipeline can exercise realistic code paths.

Usage:
    cd src
    python -m sehd_v6.trial_run_50
"""

from __future__ import annotations

import json
import sys
import traceback
from collections import Counter
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
    MODULE_IDS,
)
from sehd_v6.core.bccse.safety_gate import create_structural_gate
from sehd_v6.core.htc_tes import (
    HallucinationType,
    HallucinationTypeClassifier,
    TypeConditionedEvolution,
    TypeAwareFitness,
)


# ============================================================
# Mock detection result generator
# ============================================================

# Diverse CRS patterns keyed by (state, gold_label) to exercise the
# multi-signal classifier across all 8 hallucination types.
_CRS_PATTERNS = {
    # unresolved + gold=true  → should have been error (omission/miss)
    ("unresolved", True): [
        {"factuality": 0.2, "counterfactual": 0.4, "recovery": 0.3, "process": 0.5, "scope": 0.4},
        {"factuality": 0.3, "counterfactual": 0.2, "recovery": 0.4, "process": 0.6, "scope": 0.3},
        {"factuality": 0.15, "counterfactual": 0.3, "recovery": 0.2, "process": 0.4, "scope": 0.5},
    ],
    # unresolved + gold=false → should have been clean (boundary hesitation)
    ("unresolved", False): [
        {"factuality": 0.5, "counterfactual": 0.6, "recovery": 0.5, "process": 0.4, "scope": 0.5},
        {"factuality": 0.6, "counterfactual": 0.5, "recovery": 0.7, "process": 0.5, "scope": 0.6},
        {"factuality": 0.4, "counterfactual": 0.4, "recovery": 0.5, "process": 0.5, "scope": 0.4},
    ],
    # verified_error + gold=true  → correct detection (high CRS)
    ("verified_error", True): [
        {"factuality": 0.8, "counterfactual": 0.7, "recovery": 0.6, "process": 0.7, "scope": 0.8},
        {"factuality": 0.9, "counterfactual": 0.6, "recovery": 0.5, "process": 0.8, "scope": 0.7},
    ],
    # verified_error + gold=false → false positive (boundary too strict)
    ("verified_error", False): [
        {"factuality": 0.5, "counterfactual": 0.4, "recovery": 0.6, "process": 0.5, "scope": 0.4},
        {"factuality": 0.6, "counterfactual": 0.5, "recovery": 0.7, "process": 0.4, "scope": 0.5},
    ],
    # verified_clean + gold=true  → false negative (missed error)
    ("verified_clean", True): [
        {"factuality": 0.3, "counterfactual": 0.2, "recovery": 0.4, "process": 0.3, "scope": 0.2},
    ],
    # verified_clean + gold=false → correct clean (low CRS)
    ("verified_clean", False): [
        {"factuality": 0.7, "counterfactual": 0.8, "recovery": 0.7, "process": 0.8, "scope": 0.9},
        {"factuality": 0.8, "counterfactual": 0.7, "recovery": 0.8, "process": 0.7, "scope": 0.8},
    ],
}

_CANDIDATE_KINDS = [
    ("agent_assertion", "The agent claimed the API returned 200 OK"),
    ("agent_action", "The agent called the wrong tool to fetch data"),
    ("agent_assertion", "The agent stated the result was 42 without verification"),
    ("omission", "The agent skipped checking the error code from the tool response"),
    ("agent_action", "The agent adopted a hallucinated value from previous context"),
    ("agent_assertion", "The agent claimed the file exists without checking"),
    ("agent_action", "The agent followed an outdated instruction in the context"),
    ("agent_assertion", "The agent inferred a causal link without evidence"),
]


def _make_mock_detection(
    state: str,
    gold_label: bool,
    case_index: int,
) -> dict:
    """Create a mock detection result enriched with CRS/coverage/proof fields."""
    crs_pattern = _CRS_PATTERNS.get((state, gold_label), _CRS_PATTERNS[("unresolved", False)])
    crs_scores = crs_pattern[case_index % len(crs_pattern)]
    crs_aggregate = sum(crs_scores.values()) / len(crs_scores)

    # Candidates: non-empty for error/unresolved, empty for clean
    has_candidates = state != "verified_clean"
    kind_idx = case_index % len(_CANDIDATE_KINDS)
    kind, claim = _CANDIDATE_KINDS[kind_idx]
    census = [
        {
            "candidate_kind": kind,
            "claim_or_commitment": claim,
            "candidate_id": f"cand_{case_index:03d}",
            "origin_hypothesis_event_id": f"event_{case_index * 3 + 1}",
        }
    ] if has_candidates else []

    # Coverage: varies by state and gold label.
    # FP (verified_error + gold=false) typically has lower defense coverage
    # because the defense had less to rebut (the error was one-sided).
    # TP (verified_error + gold=true) has higher defense coverage because
    # the defense tried to rebut but failed.
    if state == "verified_error":
        p_cov = 0.75
        d_cov = 0.2 if not gold_label else 0.45  # FP vs TP distinction
        p_state, d_state = "complete", "partial"
    elif state == "verified_clean":
        p_cov, d_cov = 0.3, 0.8
        p_state, d_state = "partial", "complete"
    else:  # unresolved
        p_cov, d_cov = 0.5, 0.5
        p_state, d_state = "partial", "partial"

    # evidential_scores_parsed: {dim: {score, evidence}}
    evidential_scores_parsed = {
        dim: {"score": score, "evidence": "high" if score >= 0.6 else "medium" if score >= 0.4 else "low"}
        for dim, score in crs_scores.items()
    }

    return {
        "state": state,
        "runtime_valid": True,
        "census": census,
        "evidential_scores_parsed": evidential_scores_parsed,
        "proof_adjudication": {
            "crs_aggregate": crs_aggregate,
            "consensus_gate_applied": True,
            "prosecution_coverage": p_cov,
            "defense_coverage": d_cov,
            "prosecution_coverage_state": p_state,
            "defense_coverage_state": d_state,
        },
        "prosecution_coverage": p_cov,
        "defense_coverage": d_cov,
        "prosecution_coverage_state": p_state,
        "defense_coverage_state": d_state,
        "responsible_step": f"step_{case_index % 5 + 1}" if state == "verified_error" else None,
        "gold_steps": [f"step_{case_index % 5 + 1}"] if state == "verified_error" and gold_label else [],
        "gold_label": gold_label,
    }


# ============================================================
# Rule-based edit content generator (replaces LLM reflection)
# ============================================================

_EDIT_TEMPLATES = {
    "strict_verification": "当检测到{type}类型幻觉时，必须验证每个声明是否有工具输出或外部证据支持，禁止仅凭推理判定",
    "boundary_enforcement": "当检测到{type}类型幻觉时，应严格区分指令违反与事实错误，避免将边界情况误判为错误",
    "tool_validation": "当检测到{type}类型幻觉时，必须验证工具调用的参数和返回值是否匹配，检查工具选择是否正确",
    "causal_chain_strengthening": "当检测到{type}类型幻觉时，必须验证因果链的完整性，每个因果推断都需要明确的证据支持",
    "trigger_sensitivity": "当检测到{type}类型幻觉时，应降低候选触发阈值，确保遗漏的候选能被发现",
    "adoption_scrutiny": "当检测到{type}类型幻觉时，必须检查代理是否采用了上下文中的幻觉值，验证来源独立性",
    "context_preservation": "当检测到{type}类型幻觉时，必须检查上下文一致性，避免跨步骤的上下文漂移",
}


# ============================================================
# Main experiment
# ============================================================

def run_trial() -> None:
    print("=" * 70)
    print("SEHD v6 — 50-Case Trial Evolution Experiment")
    print("=" * 70)

    # Load baseline predictions
    data_path = Path(__file__).parent / "data" / "baseline_predictions_by_id.json"
    with open(data_path, "r", encoding="utf-8") as f:
        baselines = json.load(f)

    case_ids = list(baselines.keys())
    print(f"Loaded {len(case_ids)} cases from baseline predictions")

    # State distribution
    state_dist = Counter(baselines[cid]["state"] for cid in case_ids)
    gold_dist = Counter(
        (baselines[cid]["state"], baselines[cid]["gold_label"])
        for cid in case_ids
    )
    print(f"State distribution: {dict(state_dist)}")
    print(f"State×Gold distribution: { {f'{s}:{g}': n for (s,g),n in gold_dist.items()} }")
    print()

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

    print(f"Baseline genome: {genome.content_hash[:16]}...")
    print(f"Modules: {len(MODULE_IDS)}")
    print()

    # Per-case results
    results: list[dict] = []
    type_counts: Counter = Counter()
    direction_counts: Counter = Counter()
    strategy_counts: Counter = Counter()
    trigger_counts: Counter = Counter()
    edit_outcomes: Counter = Counter()
    fitness_outcomes: Counter = Counter()
    module_targets: Counter = Counter()

    # Evolution tracking
    champion_genome = genome
    edits_committed = 0
    edits_rejected = 0
    stable_cases = 0
    errors = 0

    for i, case_id in enumerate(case_ids):
        baseline = baselines[case_id]
        state = baseline["state"]
        gold = baseline["gold_label"]

        result = {
            "index": i,
            "case_id": case_id,
            "baseline_state": state,
            "gold_label": gold,
        }

        try:
            # 1. Create mock detection
            detection = _make_mock_detection(state, gold, i)

            # 2. Attribute failure (self-supervised mode)
            signal = attribute_failure(detection, use_gold=False)
            result["error_type"] = signal.error_type.value
            result["trigger_source"] = signal.trigger_source.value
            result["target_module"] = signal.target_module
            result["should_evolve"] = signal.should_evolve
            trigger_counts[signal.trigger_source.value] += 1

            # 3. Check if evolution needed
            if not signal.should_evolve:
                result["outcome"] = "STABLE"
                stable_cases += 1
                results.append(result)
                continue

            # 4. HTC-TES: Classify hallucination type
            type_result = classifier.classify(detection, signal)
            result["hallucination_type"] = type_result.primary_type.value
            result["type_confidence"] = round(type_result.confidence, 3)
            result["secondary_type"] = (
                type_result.secondary_type.value if type_result.secondary_type else None
            )
            type_counts[type_result.primary_type.value] += 1

            # 5. Type-conditioned evolution direction
            direction = type_evo._infer_evolution_direction(state, None)
            result["evolution_direction"] = direction
            direction_counts[direction] += 1

            # 6. Get type guidance
            strategy = type_evo.get_edit_strategy(type_result.primary_type)
            result["edit_strategy"] = strategy
            strategy_counts[strategy] += 1

            target_modules = type_evo.get_target_modules(type_result.primary_type)
            result["routed_modules"] = list(target_modules)

            # Use signal's target_module (more specific) if available,
            # otherwise use the first routed module
            edit_target = signal.target_module or target_modules[0]
            module_targets[edit_target] += 1

            # 7. Create rule-based edit (replaces LLM reflection)
            edit_content = _EDIT_TEMPLATES.get(
                strategy,
                "当检测到{type}类型幻觉时，应增强验证逻辑",
            ).format(type=type_result.primary_type.value)

            test_edit = SkillEdit(
                edit_id=f"trial_edit_{i:03d}",
                target_module=edit_target,
                operation=SkillOp.ADD_RULE,
                new_content=edit_content,
                rationale=(
                    f"[Trial] {strategy} for {type_result.primary_type.value} "
                    f"direction={direction} trigger={signal.trigger_source.value}"
                ),
                source_case_id=case_id,
            )

            # 8. Validate edit against current champion genome
            valid, reason = validate_edit(test_edit, champion_genome)
            result["edit_valid"] = valid
            result["edit_reject_reason"] = reason if not valid else None

            if not valid:
                result["outcome"] = "REJECTED_VALIDATION"
                edit_outcomes["rejected_validation"] += 1
                edits_rejected += 1
                results.append(result)
                continue

            # 9. Structural gate
            gate_passed, _gate_cert, gate_reasons = gate.evaluate(
                case_id=case_id,
                case_index=i,
                target_module=test_edit.target_module,
                operation=test_edit.operation,
                content=test_edit.new_content,
                preconditions=(),
                current_genome=champion_genome,
            )
            result["gate_passed"] = gate_passed
            if not gate_passed:
                result["gate_reasons"] = gate_reasons
                result["outcome"] = "REJECTED_GATE"
                edit_outcomes["rejected_gate"] += 1
                edits_rejected += 1
                results.append(result)
                continue

            # 10. Apply edit to champion
            candidate_genome = apply_edit(champion_genome, test_edit)
            result["genome_before"] = champion_genome.content_hash[:8]
            result["genome_after"] = candidate_genome.content_hash[:8]

            # 11. Type-aware fitness assessment
            # Simulate: after edit, unresolved → clean/error, error FP → clean
            if state == "unresolved":
                after_state = "verified_clean" if not gold else "verified_error"
            elif state == "verified_error" and not gold:
                after_state = "verified_clean"  # FP fixed
            elif state == "verified_clean" and gold:
                after_state = "verified_error"  # FN fixed
            else:
                after_state = state  # no change

            after_detection = _make_mock_detection(after_state, gold, i + 500)
            assessment = type_fitness.assess_edit(
                source_case_id=case_id,
                hallucination_type=type_result.primary_type,
                evolution_direction=direction,
                detection_before=detection,
                detection_after=after_detection,
                type_result=type_result,
                target_module=edit_target,
            )

            result["fitness"] = assessment.global_fitness
            result["dacr_score"] = round(assessment.dacr_score, 3)
            result["overall_improved"] = assessment.overall_improved
            result["after_state"] = after_state
            fitness_outcomes[assessment.global_fitness] += 1

            # 12. Commit or rollback based on fitness
            # global_fitness is "strong" / "weak" / "none"
            if assessment.global_fitness in ("strong", "weak"):
                champion_genome = candidate_genome
                result["outcome"] = "COMMITTED"
                edit_outcomes["committed"] += 1
                edits_committed += 1
            else:
                result["outcome"] = "ROLLED_BACK"
                edit_outcomes["rolled_back"] += 1
                edits_rejected += 1

            results.append(result)

        except Exception as exc:
            result["outcome"] = "ERROR"
            result["error"] = f"{type(exc).__name__}: {exc}"
            errors += 1
            results.append(result)

    # ============================================================
    # Summary
    # ============================================================

    print("=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    print(f"Total cases:           {len(case_ids)}")
    print(f"Stable (no evolution): {stable_cases}")
    print(f"Committed edits:       {edits_committed}")
    print(f"Rejected/rolled back:  {edits_rejected}")
    print(f"Errors:                {errors}")
    print()

    print("--- Trigger Source Distribution ---")
    for src, cnt in trigger_counts.most_common():
        print(f"  {src}: {cnt}")
    print()

    print("--- Hallucination Type Distribution ---")
    for htype, cnt in type_counts.most_common():
        print(f"  {htype}: {cnt}")
    print()

    print("--- Evolution Direction Distribution ---")
    for direction, cnt in direction_counts.most_common():
        print(f"  {direction}: {cnt}")
    print()

    print("--- Edit Strategy Distribution ---")
    for strat, cnt in strategy_counts.most_common():
        print(f"  {strat}: {cnt}")
    print()

    print("--- Module Target Distribution ---")
    for mod, cnt in module_targets.most_common():
        print(f"  {mod}: {cnt}")
    print()

    print("--- Edit Outcomes ---")
    for outcome, cnt in edit_outcomes.most_common():
        print(f"  {outcome}: {cnt}")
    print()

    print("--- Fitness Outcomes ---")
    for fit, cnt in fitness_outcomes.most_common():
        print(f"  {fit}: {cnt}")
    print()

    # Final genome state
    print(f"Baseline genome: {genome.content_hash[:16]}...")
    print(f"Champion genome: {champion_genome.content_hash[:16]}...")
    print(f"Champion generation: {champion_genome.generation}")
    print(f"Champion edits: {len(champion_genome.edits_applied)}")
    print()

    # HTC-TES coverage
    coverage = type_fitness.get_experience_memory().get_type_coverage()
    print("--- HTC-TES Type Coverage ---")
    print(f"  Types with experience: {coverage['types_with_experience']}/{coverage['total_types']}")
    print(f"  Effective coverage: {coverage['effective_coverage']:.1%}")
    for htype_name, stats in coverage["per_type"].items():
        if stats["total_edits"] > 0:
            print(
                f"    {htype_name}: {stats['total_edits']} edits, "
                f"{stats['success_rate']:.0%} success"
            )
    print()

    # Per-case details
    print("=" * 70)
    print("PER-CASE DETAILS")
    print("=" * 70)
    print(f"{'Idx':>3} {'Case ID':<25} {'State':<16} {'Gold':<5} {'Type':<14} {'Dir':<20} {'Strategy':<30} {'Outcome':<20}")
    print("-" * 140)
    for r in results:
        htype = r.get("hallucination_type", "-")
        direction = r.get("evolution_direction", "-")
        strategy = r.get("edit_strategy", "-")
        outcome = r.get("outcome", "-")
        gold_str = str(r.get("gold_label", "-"))
        print(
            f"{r['index']:>3} {r['case_id']:<25} {r['baseline_state']:<16} "
            f"{gold_str:<5} {htype:<14} {direction:<20} {strategy:<30} {outcome:<20}"
        )

    # Error details
    if errors > 0:
        print()
        print("=" * 70)
        print("ERROR DETAILS")
        print("=" * 70)
        for r in results:
            if r.get("outcome") == "ERROR":
                print(f"  [{r['index']}] {r['case_id']}: {r.get('error', 'unknown')}")

    # Save results to JSON
    output_path = Path(__file__).parent / "data" / "trial_run_50_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_cases": len(case_ids),
                "stable_cases": stable_cases,
                "committed_edits": edits_committed,
                "rejected_edits": edits_rejected,
                "errors": errors,
                "trigger_distribution": dict(trigger_counts),
                "type_distribution": dict(type_counts),
                "direction_distribution": dict(direction_counts),
                "strategy_distribution": dict(strategy_counts),
                "module_target_distribution": dict(module_targets),
                "edit_outcomes": dict(edit_outcomes),
                "fitness_outcomes": dict(fitness_outcomes),
                "baseline_genome_hash": genome.content_hash,
                "champion_genome_hash": champion_genome.content_hash,
                "champion_generation": champion_genome.generation,
                "type_coverage": coverage,
                "per_case_results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    run_trial()
