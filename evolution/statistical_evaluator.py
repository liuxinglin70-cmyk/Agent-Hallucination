"""Statistical evaluation, repeated A/B harness, and 3-valued admission gates (PR-08, PR-09 / C13, C14).

Implements case-level clustering, confidence bounds for net causal effect (tau_C),
and upper risk bounds for false positive / false negative regressions.
Produces formal PASS / FAIL / INCONCLUSIVE verdicts (§8.8).
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

STATISTICAL_GATE_SCHEMA = "evolution.statistical_gate.v1"
REPEATED_AB_SCHEMA = "evolution.repeated_ab_harness.v1"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"


def _case_accuracies(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Collect binary correctness per case across repeated runs."""
    by_case: dict[str, list[float]] = {}
    for r in rows:
        case = str(r.get("case") or "")
        if not case:
            continue
        gold = r.get("gold")
        pred = r.get("prediction")
        if gold is not True and gold is not False:
            continue
        # 1.0 if authoritative and correct, 0.0 otherwise
        val = 1.0 if (pred is not None and pred == gold) else 0.0
        by_case.setdefault(case, []).append(val)
    return by_case


def _case_fp_rates(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Collect FP occurrence per negative case across repeated runs."""
    by_case: dict[str, list[float]] = {}
    for r in rows:
        case = str(r.get("case") or "")
        if not case or r.get("gold") is not False:
            continue
        pred = r.get("prediction")
        # 1.0 if false positive
        val = 1.0 if pred is True else 0.0
        by_case.setdefault(case, []).append(val)
    return by_case


def _case_fn_rates(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Collect FN occurrence per positive case across repeated runs."""
    by_case: dict[str, list[float]] = {}
    for r in rows:
        case = str(r.get("case") or "")
        if not case or r.get("gold") is not True:
            continue
        pred = r.get("prediction")
        # 1.0 if false negative (either predicted clean or unresolved)
        val = 1.0 if pred is not True else 0.0
        by_case.setdefault(case, []).append(val)
    return by_case


def cluster_cases_paired_delta(
    runs_baseline: Sequence[Sequence[Mapping[str, Any]]],
    runs_candidate: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Calculate case-level clustered difference in correctness and protection endpoints."""
    flat_base = [row for run in runs_baseline for row in run]
    flat_cand = [row for run in runs_candidate for row in run]

    base_acc = _case_accuracies(flat_base)
    cand_acc = _case_accuracies(flat_cand)
    common_cases = sorted(set(base_acc) & set(cand_acc))
    n_cases = len(common_cases)

    if n_cases == 0:
        return {
            "n_cases": 0,
            "tau_c": 0.0,
            "lcb_tau_c": 0.0,
            "delta_fp": 0.0,
            "ucb_delta_fp": 0.0,
            "delta_fn": 0.0,
            "ucb_delta_fn": 0.0,
            "std_error": 0.0,
        }

    # Case-level mean difference in accuracy: C_i^B - C_i^A
    diffs = []
    for c in common_cases:
        m_b = sum(cand_acc[c]) / len(cand_acc[c])
        m_a = sum(base_acc[c]) / len(base_acc[c])
        diffs.append(m_b - m_a)

    tau_c = sum(diffs) / n_cases
    var_tau = sum((d - tau_c) ** 2 for d in diffs) / (n_cases - 1) if n_cases > 1 else 0.0
    se_tau = math.sqrt(var_tau / n_cases) if n_cases > 0 else 0.0
    # 95% one-sided lower confidence bound (z = 1.645)
    lcb_tau_c = tau_c - 1.645 * se_tau

    # FP difference on negative cases
    base_fp = _case_fp_rates(flat_base)
    cand_fp = _case_fp_rates(flat_cand)
    neg_cases = sorted(set(base_fp) & set(cand_fp))
    n_neg = len(neg_cases)
    if n_neg > 0:
        fp_diffs = [
            (sum(cand_fp[c]) / len(cand_fp[c])) - (sum(base_fp[c]) / len(base_fp[c]))
            for c in neg_cases
        ]
        delta_fp = sum(fp_diffs) / n_neg
        var_fp = sum((d - delta_fp) ** 2 for d in fp_diffs) / (n_neg - 1) if n_neg > 1 else 0.0
        se_fp = math.sqrt(var_fp / n_neg)
        ucb_delta_fp = delta_fp + 1.645 * se_fp
    else:
        delta_fp = ucb_delta_fp = 0.0

    # FN difference on positive cases
    base_fn = _case_fn_rates(flat_base)
    cand_fn = _case_fn_rates(flat_cand)
    pos_cases = sorted(set(base_fn) & set(cand_fn))
    n_pos = len(pos_cases)
    if n_pos > 0:
        fn_diffs = [
            (sum(cand_fn[c]) / len(cand_fn[c])) - (sum(base_fn[c]) / len(base_fn[c]))
            for c in pos_cases
        ]
        delta_fn = sum(fn_diffs) / n_pos
        var_fn = sum((d - delta_fn) ** 2 for d in fn_diffs) / (n_pos - 1) if n_pos > 1 else 0.0
        se_fn = math.sqrt(var_fn / n_pos)
        ucb_delta_fn = delta_fn + 1.645 * se_fn
    else:
        delta_fn = ucb_delta_fn = 0.0

    return {
        "n_cases": n_cases,
        "n_positive_cases": n_pos,
        "n_negative_cases": n_neg,
        "tau_c": tau_c,
        "lcb_tau_c": lcb_tau_c,
        "delta_fp": delta_fp,
        "ucb_delta_fp": ucb_delta_fp,
        "delta_fn": delta_fn,
        "ucb_delta_fn": ucb_delta_fn,
        "std_error": se_tau,
    }


def statistical_gate_evaluate(
    runs_baseline: Sequence[Sequence[Mapping[str, Any]]],
    runs_candidate: Sequence[Sequence[Mapping[str, Any]]],
    *,
    alpha_g: float = 0.05,
    alpha_h: float = 0.05,
    delta_min: float = 0.0,
    epsilon_fp: float = 0.035,
    epsilon_fn: float = 0.035,
    min_effective_sample_size: int = 10,
) -> dict[str, Any]:
    """Evaluate formal statistical gate with PASS / FAIL / INCONCLUSIVE verdict (§8.8)."""
    clustered = cluster_cases_paired_delta(runs_baseline, runs_candidate)
    n_cases = clustered["n_cases"]

    # Check for health regressions across all candidate rows
    flat_cand = [row for run in runs_candidate for row in run]
    health_failures = sum(
        1 for r in flat_cand
        if str(r.get("measurement_status") or "") in ("infrastructure_failure", "fatal_provider")
        or str(r.get("authority") or "") in ("infrastructure_failure", "fatal_provider")
    )

    reasons: list[str] = []

    if health_failures > 0:
        return {
            "schema_version": STATISTICAL_GATE_SCHEMA,
            "status": STATUS_FAIL,
            "passed": False,
            "evaluable": True,
            "reason": f"health_regressed: {health_failures} candidate outage(s) observed",
            "metrics": clustered,
        }

    if n_cases < min_effective_sample_size:
        return {
            "schema_version": STATISTICAL_GATE_SCHEMA,
            "status": STATUS_INCONCLUSIVE,
            "passed": False,
            "evaluable": False,
            "reason": (
                f"insufficient_sample_size: {n_cases} cases < "
                f"required {min_effective_sample_size}"
            ),
            "metrics": clustered,
        }

    # Protection checks: upper confidence bounds of damage must be below thresholds
    if clustered["ucb_delta_fp"] > epsilon_fp:
        reasons.append(
            f"ucb_delta_fp={clustered['ucb_delta_fp']:.4f} > allowed {epsilon_fp}"
        )
    if clustered["ucb_delta_fn"] > epsilon_fn:
        reasons.append(
            f"ucb_delta_fn={clustered['ucb_delta_fn']:.4f} > allowed {epsilon_fn}"
        )

    # Gain check: lower confidence bound of net gain must exceed delta_min
    if clustered["lcb_tau_c"] <= delta_min:
        reasons.append(
            f"lcb_tau_c={clustered['lcb_tau_c']:.4f} <= required {delta_min}"
        )

    if reasons:
        status = STATUS_FAIL
        reason_text = "; ".join(reasons)
    else:
        status = STATUS_PASS
        reason_text = "statistically_significant_gain_within_risk_bounds"

    return {
        "schema_version": STATISTICAL_GATE_SCHEMA,
        "status": status,
        "passed": status == STATUS_PASS,
        "evaluable": True,
        "reason": reason_text,
        "metrics": clustered,
    }


def repeated_ab_evaluate(
    runs_baseline: Sequence[Sequence[Mapping[str, Any]]],
    runs_candidate: Sequence[Sequence[Mapping[str, Any]]],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Run formal repeated A/B evaluation against frozen protocol parameters."""
    stat_params = protocol.get("statistical_params") or {}
    gate_verdict = statistical_gate_evaluate(
        runs_baseline,
        runs_candidate,
        alpha_g=float(stat_params.get("alpha_g", 0.05)),
        alpha_h=float(stat_params.get("alpha_h", 0.05)),
        delta_min=float(stat_params.get("delta_min", 0.0)),
        epsilon_fp=float(stat_params.get("epsilon_fp", 0.035)),
        epsilon_fn=float(stat_params.get("epsilon_fn", 0.035)),
        min_effective_sample_size=int(stat_params.get("min_effective_sample_size", 10)),
    )
    return {
        "schema_version": REPEATED_AB_SCHEMA,
        "protocol_hash": protocol.get("protocol_hash"),
        "repeats_baseline": len(runs_baseline),
        "repeats_candidate": len(runs_candidate),
        "gate_verdict": gate_verdict,
        "passed": gate_verdict["passed"],
        "status": gate_verdict["status"],
        "reason": gate_verdict["reason"],
    }
