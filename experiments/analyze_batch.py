"""Per-case skill-experience trajectory extractor for concurrent gold batches.

Reads ``experiments/results/batch<B>_gold/*/evolution_result.json`` and prints
the full per-step audit chain (detection -> attribution -> proposal -> gate ->
fitness -> L2) plus final genomes and experience memory, so each batch can be
reviewed case by case.

Usage (from repo root):

    python experiments/analyze_batch.py --batch 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def proposal_text(step: dict) -> str:
    source = step.get("edit_proposal") or step.get("mutation_applied") or {}
    content = (
        source.get("new_content")
        or (source.get("edit") or {}).get("new_content")
        or (source.get("edit") or {}).get("rule_content")
        or ""
    )
    return str(content)[:400]


def step_line(step: dict) -> list[str]:
    attribution = (
        (step.get("episode") or {}).get("attribution")
        or step.get("attribution")
        or {}
    )
    gate = attribution.get("layer") or ""
    artifact = attribution.get("artifact") or ""
    reason = attribution.get("reason_code") or ""
    target = (
        (step.get("edit_proposal") or {}).get("target_module")
        or (step.get("mutation_applied") or {}).get("target_module")
        or (step.get("edit_proposal") or {}).get("edit") or {}
    )
    if isinstance(target, dict):
        target = target.get("target_module")
    operation = (
        (step.get("edit_proposal") or {}).get("operation")
        or (step.get("mutation_applied") or {}).get("operation")
        or (step.get("edit_proposal") or {}).get("edit") or {}
    )
    if isinstance(operation, dict):
        operation = operation.get("operation")
    fitness = step.get("fitness_check") or {}
    assessment = fitness.get("assessment") or {}
    decision = assessment.get("decision") or fitness.get("decision") or "-"
    reasons = assessment.get("reasons") or fitness.get("reasons") or []
    policy = fitness.get("l2_policy") or {}
    if policy:
        decision = policy.get("decision") or decision
        reasons = [policy.get("reason_code") or ""]
    return [
        f"step[{step.get('attempt_index')}]",
        f"det={step.get('detection_state')}",
        f"trig={step.get('trigger_source')}",
        f"dec={step.get('gate_decision')}",
        f"layer={gate or '-'}",
        f"artifact={artifact or '-'}",
        f"reason={reason or '-'}",
        f"module={target or '-'}",
        f"op={operation or '-'}",
        f"fitness={decision}",
    ] + [f"  fitness_reasons={reasons}"]


def l2_audit_lines(run: dict) -> list[str]:
    lines: list[str] = []
    audit = (run.get("evolution_audit") or {}).get("l2_policy_audit")
    if not isinstance(audit, list):
        return lines
    for item in audit:
        policy = item.get("policy") or {}
        pred = policy.get("predicate") or {}
        action = policy.get("action") or {}
        lines.append(
            f"  L2[{item.get('decision')}] pred={pred.get('predicate') or pred.get('name')} "
            f"action={action.get('action') or action.get('name')} "
            f"params={action.get('params') or action.get('parameters')} "
            f"reason={item.get('reason_code') or item.get('reject_reason') or '-'}"
        )
    return lines


def champion_lines(run: dict) -> list[str]:
    lines: list[str] = []
    genome = run.get("champion_genome") or {}
    for module in genome.get("modules", {}).values():
        meta = module.get("rule_meta") or []
        for index, rule in enumerate(module.get("rules", [])):
            entry = meta[index] if index < len(meta) else {}
            if entry.get("is_baseline"):
                continue
            lines.append(
                f"  RULE {module.get('module_id')}/add_rule: {str(rule)[:300]}"
            )
    return lines


def policy_lines(run: dict) -> list[str]:
    genome = run.get("policy_genome") or {}
    lines: list[str] = []
    for item in genome.get("items", []):
        lines.append(f"  POLICY {json.dumps(item, ensure_ascii=False)[:300]}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, required=True)
    args = parser.parse_args()
    results_root = ROOT / "experiments" / "results" / f"batch{args.batch}_gold"
    if not results_root.is_dir():
        raise SystemExit(f"missing results root: {results_root}")

    for result_path in sorted(results_root.glob("*/evolution_result.json")):
        run = json.loads(result_path.read_text(encoding="utf-8"))
        print("=" * 110)
        print(
            f"{result_path.parent.name} | dur={run.get('duration_seconds', 0):.0f}s "
            f"| total={run.get('total_cases')} triggered={run.get('triggered_cases')} "
            f"accepted={run.get('edits_accepted')} rejected={run.get('edits_rejected')} "
            f"stable={run.get('stable_cases')} invalid={run.get('runtime_invalid_cases')}"
        )
        for step in run.get("steps", []):
            for line in step_line(step):
                print("  " + line)
            content = proposal_text(step)
            if content:
                print(f"    rule: {content}")
        for line in l2_audit_lines(run):
            print(line)
        for line in champion_lines(run):
            print(line)
        for line in policy_lines(run):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
