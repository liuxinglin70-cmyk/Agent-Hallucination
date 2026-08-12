"""Single dispatch point for the two-skill-layer execution model (§2.7).

A RepairPlan carries its ``executor`` field (``EXECUTOR_PROVIDER_BEHAVIOR``
for L1, ``EXECUTOR_ADJUDICATION_POLICY`` for L2); this module is the only
place that field is read to choose how the plan executes:

* ``PROVIDER_BEHAVIOR`` — rule-injection replay. The candidate rule lives
  in ``genome`` (an L1 SkillGenome with the proposed edit applied); the
  detector replays with the provider boundary skill-aware over those rules.
  Deployed L2 policies ride along in ``policy_genome`` at the working scope.
* ``ADJUDICATION_POLICY`` — deterministic policy execution. The candidate
  policy item lives in ``policy_genome``; the court evaluates it via
  ``detection.policy.evaluate_policies`` against the replay's adjudication
  facts. The candidate-validation channel (``policy_scope=None``) forces
  the full candidate item set so a newly proposed (still
  ``fitness_status=none``) item is observable — a scoped replay would
  silently exclude it and every L2 proposal would die as no-effect.

The loop layer is the only caller; it passes the candidate in the slot
the plan's executor names, and this function chooses ``policy_scope`` from
the executor field so the caller never decides the route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from sehd_rise.skills.genome import SkillGenome
from sehd_rise.skills.policy import PolicyGenome
from sehd_rise.platform.usage import ProviderUsageLedger
from sehd_rise.causal.plan import (
    EXECUTOR_ADJUDICATION_POLICY,
    EXECUTOR_PROVIDER_BEHAVIOR,
    RepairPlan,
)
from sehd_rise.execution.replay import _run_counted_detection


def execute_repair_plan(
    plan: RepairPlan,
    *,
    genome: SkillGenome,
    case_id: str,
    output_path: Path,
    budget_kind: str,
    budget_usage: dict[str, int],
    records: Mapping[str, dict[str, Any]],
    config: dict[str, Any],
    provider_usage_ledger: ProviderUsageLedger,
    policy_genome: PolicyGenome | None = None,
) -> dict[str, Any]:
    """Dispatch one repair plan to its executor and return the replay result.

    The plan's ``executor`` field is the sole determinant of the route: a
    ``PROVIDER_BEHAVIOR`` plan replays the candidate rule through the
    skill-aware provider boundary at the working scope; an
    ``ADJUDICATION_POLICY`` plan replays the candidate policy through the
    court's deterministic ``evaluate_policies`` on the candidate-validation
    channel (full item set, scope ``None``).
    """
    if plan.executor == EXECUTOR_PROVIDER_BEHAVIOR:
        return _run_counted_detection(
            budget_kind=budget_kind, genome=genome, case_id=case_id,
            output_path=output_path, budget_usage=budget_usage,
            records=records, config=config,
            provider_usage_ledger=provider_usage_ledger,
            policy_genome=policy_genome, policy_scope="working",
        )
    if plan.executor == EXECUTOR_ADJUDICATION_POLICY:
        if policy_genome is None:
            raise ValueError(
                "ADJUDICATION_POLICY plan requires a candidate policy_genome"
            )
        return _run_counted_detection(
            budget_kind=budget_kind, genome=genome, case_id=case_id,
            output_path=output_path, budget_usage=budget_usage,
            records=records, config=config,
            provider_usage_ledger=provider_usage_ledger,
            policy_genome=policy_genome, policy_scope=None,
        )
    raise ValueError(f"unknown RepairPlan.executor: {plan.executor!r}")


__all__ = ["execute_repair_plan"]
