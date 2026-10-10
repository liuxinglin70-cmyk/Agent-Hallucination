"""Shared patrol sampling and failure-family preparation for cycle entry points."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Callable, Mapping, Sequence

from evolution.capability_planning import plan_capabilities
from evolution.worksheet import (
    attach_normal_neighbors, classify_family_actionability, diagnose_failure_families,
    excluded_failure_cases, mechanism_subgroups, refine_failure_families,
)


@dataclass
class PatrolPreparation:
    initial_size: int
    cases: list[str]
    rows: list[dict[str, Any]]
    contrast: dict[str, Any]
    bootstrap_cases: list[str]
    families: list[dict[str, Any]]


@dataclass
class FamilyPreparation:
    families: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    unrepresented: list[dict[str, Any]]
    capability_gaps: list[dict[str, Any]]
    capability_plan: dict[str, Any]


def prepare_patrol(
    *, cycle: int, split: Any, budget: Mapping[str, Any], arms: Any,
    evaluate: Callable[..., Any], find_families: Callable[..., Any], extend: Callable[..., Any],
) -> PatrolPreparation:
    initial_size = int(budget.get("patrol_cases", 120))
    cases = split.patrol_sample(cycle, initial_size)
    rows, bare_rows, contrast = evaluate(arms, cases)
    families = find_families(rows)
    bootstrap_cases = []
    if not families:
        rerun_budget = int(budget.get("stable_failure_rerun_cases", 40))
        selected = set(cases)
        remaining = [case for case in split.patrol_pool if case not in selected]
        if rerun_budget > 0 and remaining:
            rng = random.Random(int(split.seed) + int(cycle) * 104729 + 17)
            bootstrap_cases = rng.sample(remaining, min(rerun_budget, len(remaining)))
            cases = [*cases, *bootstrap_cases]
            rows, bare_rows, contrast = extend(
                arms, patrol_rows=rows, bare_rows=bare_rows, bootstrap_cases=bootstrap_cases,
            )
            families = find_families(rows)
    return PatrolPreparation(initial_size, cases, rows, contrast, bootstrap_cases, families)


def split_actionable_families(
    families: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actionable = []
    gaps = []
    for family in families:
        verdict = classify_family_actionability(family)
        item = {**family, "actionability": verdict}
        if verdict["classification"] == "actionable":
            actionable.append(item)
        else:
            gaps.append({
                "failure_family": item.get("failure_family"), "count": item.get("count"),
                "sources": item.get("sources"), "classification": verdict["classification"],
                "reason": verdict["reason"],
            })
    return actionable, gaps


def prepare_families(
    families: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], *,
    dataset_root: str, causal_policy: Mapping[str, Any] | None,
    route_failure_records: Sequence[Mapping[str, Any]],
) -> FamilyPreparation:
    diagnosed = diagnose_failure_families(families, rows, dataset_root=dataset_root, causal_policy=causal_policy)
    diagnosed = refine_failure_families(diagnosed)
    excluded = excluded_failure_cases(families, diagnosed)
    diagnosed, unrepresented = mechanism_subgroups(diagnosed)
    diagnosed = attach_normal_neighbors(diagnosed, rows)
    diagnosed, gaps = split_actionable_families(diagnosed)
    plan = plan_capabilities(diagnosed, route_failure_records=route_failure_records)
    return FamilyPreparation(diagnosed, excluded, unrepresented, gaps, plan)
