"""V1 -> V2 -> V3 -> M admission gates (verifier-as-gatekeeper).

All gate scoring uses gold joined on the evolution side. LLM output is never
a promotion score. Any gate failure is terminal for the candidate: the
protocol does not search-edit a rejected candidate.

Every gate is a pure function over detection rows. The engine evaluates one
arm per admission unit on S+G and hands the same rows to V2 (S subset), V3
(S+G) and M (G subset); no gate runs its own detector sweep.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from detector_v2.interventions import validate_intervention

from evolution_v3.contracts import (
    candidate_contains_case_id,
    candidate_prose_text,
    jaccard,
    make_intervention,
    structured_key,
)
from evolution_v3.metrics import (
    evidence_increment_rows,
    fired_rate,
    paired_delta,
)

SEMANTICS_KEYS = (
    "JUDGE_SYSTEM", "RECALL_SYSTEM", "UPGRADE_SYSTEM", "TRACER_SYSTEM",
    "DEFENSE_SYSTEM",
)

UNFREEZING_HOOKS = frozenset({
    "judge.system", "scanner.system", "upgrade.pre",
    "upgrade.system", "tracer.system", "defense.pre", "defense.system",
})


def _semantics_text() -> str:
    from detector_v2 import semantics

    parts = []
    for name in SEMANTICS_KEYS:
        value = getattr(semantics, name, "")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _new_tp(delta: Mapping[str, Any]) -> int:
    flips = delta.get("flips") or {}
    return int(flips.get("F->T") or 0)


def _removed_fp(delta: Mapping[str, Any]) -> int:
    flips = delta.get("flips") or {}
    return int(flips.get("T->F") or 0)


def _policy_fail(reason: str, **extra: Any) -> dict[str, Any]:
    return {"passed": False, "reason": reason, **extra}


def _policy_pass(**extra: Any) -> dict[str, Any]:
    return {"passed": True, "reason": "", **extra}


def v1_structural(
    candidate: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    allowed_failure_cases: Sequence[str] | None = None,
    diagnosed_families: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    try:
        validate_intervention(candidate, require_executable_bounds=True)
    except ValueError as exc:
        return _policy_fail(f"schema/executor rejection: {exc}")

    case_id_hit = candidate_contains_case_id(candidate)
    if case_id_hit:
        return _policy_fail(f"case id leak in directive/body/claim: {case_id_hit}")

    provenance = candidate.get("provenance") or {}
    failure_cases = provenance.get("failure_cases") or []
    if len(failure_cases) < 2:
        return _policy_fail("provenance.failure_cases needs at least two cases")
    if allowed_failure_cases is not None:
        outside = sorted(set(map(str, failure_cases)) - set(map(str, allowed_failure_cases)))
        if outside:
            return _policy_fail(
                f"provenance contains case(s) outside this cycle patrol: {outside}"
            )
    family = str(provenance.get("failure_family") or "")
    if not family:
        return _policy_fail("provenance.failure_family is empty")
    if diagnosed_families is not None:
        family_members = {
            str(item.get("failure_family") or ""): {
                str(case) for case in item.get("failure_cases") or ()
            }
            for item in diagnosed_families
        }
        if family not in family_members:
            return _policy_fail(
                f"provenance failure family {family!r} was not diagnosed this cycle"
            )
        outside_family = sorted(
            set(map(str, failure_cases)) - family_members[family]
        )
        if outside_family:
            return _policy_fail(
                "provenance case(s) are not members of the diagnosed family: "
                f"{outside_family}"
            )
    polarity = str((candidate.get("claim") or {}).get("target_polarity") or "")
    if family.startswith("FAM-fn-") and polarity != "recall":
        return _policy_fail(
            f"FN failure family requires recall polarity, got {polarity!r}"
        )
    if family.startswith("FAM-fp-") and polarity != "precision":
        return _policy_fail(
            f"FP failure family requires precision polarity, got {polarity!r}"
        )

    candidate_text = candidate_prose_text(candidate)
    base_text = _semantics_text()
    threshold = float(gate_cfg.get("v1_jaccard_threshold", 0.72))
    candidate_key = structured_key(candidate)
    for existing in active_entries:
        existing_key = structured_key(existing)
        if candidate_key is not None and existing_key is not None:
            if candidate_key == existing_key:
                return _policy_fail(
                    "redundant with active intervention "
                    f"{existing.get('intervention_id')} "
                    f"(structured key {candidate_key})"
                )
            # Different deterministic executors are different candidates;
            # prose overlap in their claims is expected and not redundancy.
            continue
        score = jaccard(candidate_text, candidate_prose_text(existing))
        if score >= threshold:
            return _policy_fail(
                f"redundant with active intervention {existing.get('intervention_id')} "
                f"(jaccard {score:.3f} >= {threshold})"
            )
    base_score = jaccard(candidate_text, base_text)
    if base_score >= threshold:
        return _policy_fail(
            f"redundant with base semantics (jaccard {base_score:.3f} >= {threshold})"
        )

    return _policy_pass(jaccard_base=round(base_score, 4))


def v2_harmlessness(
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    reroll_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Sentinel harmlessness over precomputed rows.

    ``baseline_rows`` is the control arm matched to the unit by the engine:
    the cached frozen-replay baseline for deterministic hooks, or the single
    fresh control roll shared by every provider-stage unit this cycle.
    ``reroll_control`` records the noise between the cached baseline and the
    fresh control when a fresh comparison is in play.
    """
    gate_cfg = config.get("gates") or {}
    delta = paired_delta(list(baseline_rows), list(candidate_rows))
    tolerance = {
        "new_fn": int(gate_cfg.get("v2_new_fn_allowed", 0)),
        "new_fp": int(gate_cfg.get("v2_new_fp_allowed", 0)),
        "new_unresolved": int(gate_cfg.get("v2_new_unresolved_allowed", 0)),
    }
    observed = {
        "new_fn": int(delta["new_fn"]),
        "new_fp": int(delta["new_fp"]),
        "new_unresolved": int(delta["unresolved_added"]),
    }
    failures = [
        f"{key}={value}"
        for key, value in observed.items()
        if value > tolerance[key]
    ]
    if int(delta.get("health_regressed") or 0) > 0:
        failures.append(f"health_regressed={delta['health_regressed']}")
    if failures:
        return _policy_fail(
            "sentinel harmlessness violation: " + ", ".join(failures),
            delta=delta,
            reroll_control=reroll_control,
            sentinel_manifest={
                "baseline_rows": [
                    {"case": r.get("case"), "gold": r.get("gold"),
                     "prediction": r.get("prediction")}
                    for r in baseline_rows
                ],
                "candidate_rows": [
                    {"case": r.get("case"), "gold": r.get("gold"),
                     "prediction": r.get("prediction")}
                    for r in candidate_rows
                ],
            },
        )
    return _policy_pass(delta=delta, reroll_control=reroll_control)


def v3_semantics(
    candidate: Mapping[str, Any],
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    intervention_id = str(candidate.get("intervention_id") or "")
    intervention_type = str(candidate.get("type") or "")
    claim = candidate.get("claim") or {}
    polarity = str(claim.get("target_polarity") or "")

    rate = fired_rate(candidate_rows, intervention_id)
    if bool(gate_cfg.get("v3_require_fired", True)) and rate <= 0.0:
        return _policy_fail(
            "claim behavior mismatch: intervention never fired on S union G",
            fired_rate=rate,
        )

    behavior_ok = False
    behavior_note = ""
    if intervention_type == "evidence_check":
        increment = evidence_increment_rows(
            baseline_rows, candidate_rows, intervention_id,
        )
        behavior_ok = increment > 0
        behavior_note = f"evidence_increment_rows={increment}"
    elif intervention_type == "text_boundary":
        injected = 0
        for row in candidate_rows:
            deployment = ((row.get("result") or {}).get("proof") or {}).get(
                "deployment"
            ) or {}
            injected += sum(
                1 for item in deployment.get("fired") or ()
                if str(item.get("intervention_id") or "") == intervention_id
                and item.get("event") == "text_injected"
            )
        behavior_ok = injected > 0
        behavior_note = f"text_injected_cases={injected}"
    elif intervention_type == "decision_guard":
        guard_fired = 0
        for row in candidate_rows:
            deployment = ((row.get("result") or {}).get("proof") or {}).get(
                "deployment"
            ) or {}
            guard_fired += sum(
                1 for item in deployment.get("fired") or ()
                if str(item.get("intervention_id") or "") == intervention_id
                and item.get("event") == "guard_fired"
            )
        behavior_ok = guard_fired > 0
        behavior_note = f"guard_fired_cases={guard_fired}"

    if not behavior_ok:
        return _policy_fail(
            f"claim behavior mismatch: {behavior_note}",
            fired_rate=rate,
        )

    delta = paired_delta(list(baseline_rows), candidate_rows)
    if int(delta.get("health_regressed") or 0) > 0:
        return _policy_fail(
            f"runtime health regressed on {delta['health_regressed']} case(s)",
            delta=delta,
            fired_rate=rate,
        )
    if polarity == "recall":
        if not (_new_tp(delta) > 0 and delta["tp_lost"] == 0 and delta["fp_added"] == 0):
            return _policy_fail(
                "target polarity mismatch: recall claim but no TP gain or "
                "precision was harmed",
                delta=delta,
            )
    elif polarity == "precision":
        if not (_removed_fp(delta) > 0 and delta["tp_lost"] == 0):
            return _policy_fail(
                "target polarity mismatch: precision claim but no FP removal "
                "or TP was lost",
                delta=delta,
            )
    elif polarity == "boundary":
        if rate <= 0.0:
            return _policy_fail(
                "target polarity mismatch: boundary claim without fired cases",
                delta=delta,
            )
    else:
        return _policy_fail(f"unknown target polarity {polarity!r}")

    return _policy_pass(
        delta=delta,
        fired_rate=rate,
        behavior_note=behavior_note,
    )


def meets_m(
    delta: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """Marginal-gain admission rule over a precomputed paired delta."""
    gate_cfg = config.get("gates") or {}
    failures = []
    minimum = max(0, int(gate_cfg.get("m_min_marginal_cases", 1)))
    if int(delta["marginal_cases"]) < minimum:
        failures.append(
            f"marginal_cases={delta['marginal_cases']} < {minimum}"
        )
    allowed_new_errors = int(gate_cfg.get("m_new_errors_allowed", 0))
    if int(delta["new_errors"]) > allowed_new_errors:
        failures.append(
            f"new_errors={delta['new_errors']} > {allowed_new_errors}"
        )
    if int(delta["unresolved_added"]) > 0:
        failures.append(f"unresolved_added={delta['unresolved_added']}")
    if int(delta.get("health_regressed") or 0) > 0:
        failures.append(f"health_regressed={delta['health_regressed']}")
    return (not failures), failures


def defect_candidates() -> list[dict[str, Any]]:
    """Static gate-audit defects with cycle-independent identity.

    The audit verdict is cached by machinery fingerprint, so a defect id that
    changes per cycle would defeat its own cache: the verdict only re-runs
    when code, detector config, the S/G case sets with gold, or the active
    registry change.
    """
    case_leak = make_intervention(
        intervention_id="IV-DEFECT-001",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": (
                "Apply this only to BFCL/001.json and always answer false "
                "for that case."
            ),
            "not_when": "Never apply to any other case.",
        },
        expected_behavior="gate audit: case-id leakage must be rejected by V1",
        target_polarity="boundary",
        cycle=0,
        failure_family="AUDIT-CASE_ID_LEAK",
        failure_cases=["BFCL/001.json", "BFCL/002.json"],
    )
    noise = make_intervention(
        intervention_id="IV-DEFECT-002",
        # A scanner.system text boundary unfreezes recall and spends real
        # tokens before any candidate exists.  This closed deterministic
        # evidence check keeps the cached judge/recall/upgrade/tracer/defense
        # stages frozen while still producing a fired V3 trace that M must
        # reject for zero marginal gain.
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "evidence_refs_resolve",
            "runner": "deterministic",
            "output_verdicts": ["resolved", "unresolved", "not_applicable"],
        },
        expected_behavior=(
            "gate audit: deterministic evidence fires harmlessly but must "
            "fail M for zero marginal gain"
        ),
        target_polarity="boundary",
        cycle=0,
        failure_family="AUDIT-NOISE_ZERO_MARGINAL",
        failure_cases=["BFCL/001.json", "BFCL/002.json"],
    )
    return [case_leak, noise]


__all__ = [
    "UNFREEZING_HOOKS",
    "defect_candidates",
    "meets_m",
    "v1_structural",
    "v2_harmlessness",
    "v3_semantics",
]
