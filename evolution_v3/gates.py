"""V1 -> V2 -> V3 -> M admission gates (verifier-as-gatekeeper).

All gate scoring uses gold joined on the evolution side. LLM output is never
a promotion score. Any gate failure is terminal for the candidate: the
protocol does not search-edit a rejected candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from detector_v2.interventions import validate_intervention

from evolution_v3.contracts import (
    candidate_contains_case_id,
    candidate_prose_text,
    jaccard,
    make_intervention,
    make_registry,
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


def _reroll_backend(backend: Any) -> Any:
    """Return the live detector behind all cache/replay wrappers."""
    current = backend
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, "live"):
            current = current.live
            continue
        nested = getattr(current, "backend", None)
        if nested is None:
            break
        current = nested
    return current


def v2_harmlessness(
    candidate: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]] = (),
    baseline_rows: Sequence[Mapping[str, Any]],
    backend: Any,
    dataset_root: str,
    sentinel_cases: Sequence[str],
    config: Mapping[str, Any],
    gold_map: Mapping[str, bool | None] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    registry = make_registry([*active_entries, candidate])
    candidate_rows: list[dict[str, Any]]
    delta: dict[str, Any]
    base_tolerance = {
        "new_fn": int(gate_cfg.get("v2_new_fn_allowed", 0)),
        "new_fp": int(gate_cfg.get("v2_new_fp_allowed", 0)),
        "new_unresolved": int(gate_cfg.get("v2_new_unresolved_allowed", 0)),
    }
    failures = []
    reroll = None
    tolerance = dict(base_tolerance)
    if str(candidate.get("hook") or "") in UNFREEZING_HOOKS and bool(
        gate_cfg.get("v2_reroll_control", True)
    ):
        # Compare two fresh arms with the same deployed registry. Aggregate
        # noise allowances can hide harm on different cases, so no count-based
        # subtraction is used.
        live = _reroll_backend(backend)
        evaluate_fresh = getattr(live, "evaluate_fresh", live.evaluate)
        control_rows = evaluate_fresh(
            list(sentinel_cases),
            dataset_root=dataset_root,
            registry=make_registry(active_entries),
            gold_map=gold_map,
            workers=workers,
        )
        candidate_rows = evaluate_fresh(
            list(sentinel_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        reroll = paired_delta(list(baseline_rows), control_rows)
        delta = paired_delta(control_rows, candidate_rows)
    else:
        candidate_rows = backend.evaluate(
            list(sentinel_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        delta = paired_delta(list(baseline_rows), candidate_rows)
    observed = {
        "new_fn": int(delta["new_fn"]),
        "new_fp": int(delta["new_fp"]),
        "new_unresolved": int(delta["unresolved_added"]),
    }
    for key, value in observed.items():
        if value > tolerance[key]:
            failures.append(f"{key}={value}")
    if int(delta.get("health_regressed") or 0) > 0:
        failures.append(f"health_regressed={delta['health_regressed']}")
    if failures:
        return _policy_fail(
            "sentinel harmlessness violation: " + ", ".join(failures),
            delta=delta,
            reroll_control=reroll,
            sentinel_manifest={
                "cases": list(sentinel_cases),
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
    return _policy_pass(delta=delta, reroll_control=reroll)


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


def _meets_m(
    delta: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    gate_cfg = config.get("gates") or {}
    failures = []
    minimum = max(0, int(gate_cfg.get("m_min_marginal_cases", 1)))
    if delta["marginal_cases"] < minimum:
        failures.append(
            f"marginal_cases={delta['marginal_cases']} < {minimum}"
        )
    allowed_new_errors = int(gate_cfg.get("m_new_errors_allowed", 0))
    if delta["new_errors"] > allowed_new_errors:
        failures.append(
            f"new_errors={delta['new_errors']} > {allowed_new_errors}"
        )
    if delta["unresolved_added"] > 0:
        failures.append(f"unresolved_added={delta['unresolved_added']}")
    if int(delta.get("health_regressed") or 0) > 0:
        failures.append(f"health_regressed={delta['health_regressed']}")
    return (not failures), failures


def marginal_candidate(
    candidate: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]],
    backend: Any,
    dataset_root: str,
    gain_cases: Sequence[str],
    active_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    gold_map: Mapping[str, bool | None] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    registry = make_registry([*active_entries, candidate])
    fresh = str(candidate.get("hook") or "") in UNFREEZING_HOOKS
    evaluate = backend.evaluate_fresh if fresh else backend.evaluate
    control_rows = (
        evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=make_registry(active_entries),
            gold_map=gold_map,
            workers=workers,
        )
        if fresh
        else list(active_rows)
    )
    rows = evaluate(
        list(gain_cases),
        dataset_root=dataset_root,
        registry=registry,
        gold_map=gold_map,
        workers=workers,
    )
    delta = paired_delta(control_rows, rows)
    passed, failures = _meets_m(delta, config)
    return {
        "candidate": dict(candidate),
        "passed": passed,
        "reason": "; ".join(failures),
        "delta": delta,
        "rows": rows,
    }


def marginal_selection(
    candidates: Sequence[Mapping[str, Any]],
    *,
    active_entries: Sequence[Mapping[str, Any]],
    backend: Any,
    dataset_root: str,
    gain_cases: Sequence[str],
    active_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    gold_map: Mapping[str, bool | None] | None = None,
    workers: int = 1,
    max_admits: int = 2,
    joint_groups: Sequence[Sequence[str]] = (),
) -> dict[str, Any]:
    by_id = {
        str(candidate.get("intervention_id") or ""): dict(candidate)
        for candidate in candidates
    }
    joint_ids = {
        str(identifier) for group in joint_groups for identifier in group
    }

    # Individual M reports (joint members are never sold separately).
    reports = []
    for candidate in candidates:
        if str(candidate.get("intervention_id") or "") in joint_ids:
            continue
        reports.append(marginal_candidate(
            candidate,
            active_entries=active_entries,
            backend=backend,
            dataset_root=dataset_root,
            gain_cases=gain_cases,
            active_rows=active_rows,
            config=config,
            gold_map=gold_map,
            workers=workers,
        ))

    # Joint M reports: the group is a single pre-commit unit.
    joint_reports: list[dict[str, Any]] = []
    for group in joint_groups:
        candidate_ids = [str(identifier) for identifier in group]
        missing = [
            identifier for identifier in candidate_ids
            if identifier not in by_id
        ]
        if missing:
            joint_reports.append({
                "candidate_ids": candidate_ids,
                "passed": False,
                "selected": False,
                "reason": f"joint member(s) missing: {missing}",
                "delta": None,
            })
            continue
        entries = [by_id[identifier] for identifier in candidate_ids]
        registry = make_registry([*active_entries, *entries])
        fresh = any(
            str(entry.get("hook") or "") in UNFREEZING_HOOKS
            for entry in entries
        )
        evaluate = backend.evaluate_fresh if fresh else backend.evaluate
        control_rows = (
            evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=make_registry(active_entries),
                gold_map=gold_map,
                workers=workers,
            )
            if fresh else list(active_rows)
        )
        rows = evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        delta = paired_delta(control_rows, rows)
        passed, failures = _meets_m(delta, config)
        joint_reports.append({
            "candidate_ids": candidate_ids,
            "passed": passed,
            "selected": False,
            "reason": "; ".join(failures),
            "delta": delta,
        })

    selected_reports: list[dict[str, Any]] = []
    qualified = [report for report in reports if report["passed"]]
    qualified.sort(
        key=lambda report: -int(report["delta"]["marginal_cases"])
    )
    budget = int(max_admits)
    for report in qualified:
        if budget < 1:
            break
        trial = [
            *active_entries,
            *[item["candidate"] for item in selected_reports],
            report["candidate"],
        ]
        registry = make_registry(trial)
        fresh = any(
            str(entry.get("hook") or "") in UNFREEZING_HOOKS
            for entry in [
                *[item["candidate"] for item in selected_reports],
                report["candidate"],
            ]
        )
        evaluate = backend.evaluate_fresh if fresh else backend.evaluate
        control_rows = (
            evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=make_registry(active_entries),
                gold_map=gold_map,
                workers=workers,
            )
            if fresh else list(active_rows)
        )
        rows = evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        delta = paired_delta(control_rows, rows)
        passed, _failures = _meets_m(delta, config)
        if passed:
            selected_reports.append(report)
            budget -= 1

    # Decompose individual combinations by testing each member leave-one-out.
    # Keep every selected member whose removal breaks the accepted combined
    # configuration. Independent candidates are both redundant only when the
    # full configuration remains accepted after removing each one.
    removed: list[str] = []
    if len(selected_reports) > 1:
        full_registry = make_registry([
            *active_entries,
            *[item["candidate"] for item in selected_reports],
        ])
        full_fresh = any(
            str(item["candidate"].get("hook") or "") in UNFREEZING_HOOKS
            for item in selected_reports
        )
        full_evaluate = (
            backend.evaluate_fresh if full_fresh else backend.evaluate
        )
        full_control = (
            full_evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=make_registry(active_entries),
                gold_map=gold_map,
                workers=workers,
            )
            if full_fresh else list(active_rows)
        )
        full_rows = full_evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=full_registry,
            gold_map=gold_map,
            workers=workers,
        )
        full_delta = paired_delta(full_control, full_rows)
        ordered = sorted(
            selected_reports,
            key=lambda item: -int(item["delta"]["marginal_cases"]),
        )
        for kept in ordered[1:]:
            without = [item for item in selected_reports if item is not kept]
            if not without:
                continue
            registry = make_registry([
                *active_entries, *[item["candidate"] for item in without],
            ])
            fresh = any(
                str(item["candidate"].get("hook") or "") in UNFREEZING_HOOKS
                for item in without
            )
            evaluate = backend.evaluate_fresh if fresh else backend.evaluate
            control_rows = (
                evaluate(
                    list(gain_cases),
                    dataset_root=dataset_root,
                    registry=make_registry(active_entries),
                    gold_map=gold_map,
                    workers=workers,
                )
                if fresh else list(active_rows)
            )
            rows = evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=registry,
                gold_map=gold_map,
                workers=workers,
            )
            delta = paired_delta(control_rows, rows)
            passed, _failures = _meets_m(delta, config)
            if (
                passed
                and int(delta["marginal_cases"])
                >= int(full_delta["marginal_cases"])
            ):
                removed.append(str(kept["candidate"].get("intervention_id") or ""))
        if removed:
            selected_reports = [
                item for item in selected_reports
                if str(item["candidate"].get("intervention_id") or "")
                not in set(removed)
            ]

    selected_entries = [item["candidate"] for item in selected_reports]
    for joint_report in sorted(
        joint_reports,
        key=lambda item: (
            not bool(item.get("passed")),
            -int((item.get("delta") or {}).get("marginal_cases", 0)),
        ),
    ):
        if not joint_report.get("passed"):
            continue
        entries = [by_id[identifier] for identifier in joint_report["candidate_ids"]]
        if budget >= len(entries):
            trial = [*active_entries, *selected_entries, *entries]
            fresh = any(
                str(entry.get("hook") or "") in UNFREEZING_HOOKS
                for entry in [*selected_entries, *entries]
            )
            evaluate = backend.evaluate_fresh if fresh else backend.evaluate
            control_rows = (
                evaluate(
                    list(gain_cases),
                    dataset_root=dataset_root,
                    registry=make_registry(active_entries),
                    gold_map=gold_map,
                    workers=workers,
                )
                if fresh else list(active_rows)
            )
            rows = evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=make_registry(trial),
                gold_map=gold_map,
                workers=workers,
            )
            combined_delta = paired_delta(control_rows, rows)
            combined_passed, failures = _meets_m(combined_delta, config)
            if combined_passed:
                joint_report["selected"] = True
                joint_report["combined_delta"] = combined_delta
                selected_entries.extend(entries)
                budget -= len(entries)
            else:
                joint_report["reason"] = (
                    "combined with already selected candidates: "
                    + "; ".join(failures)
                )
        else:
            joint_report["reason"] = (
                f"{joint_report.get('reason') or 'joint M passed'}; "
                f"not selected: remaining budget {budget} < {len(entries)}"
            )
    # Attribute the committed combination from the same final configuration.
    # The pre-selection report is relative to the old active registry and is
    # not a valid per-member contribution when candidates interact.
    selected_attribution: list[dict[str, Any]] = []
    if selected_entries:
        full_registry = make_registry([*active_entries, *selected_entries])
        full_fresh = any(
            str(entry.get("hook") or "") in UNFREEZING_HOOKS
            for entry in selected_entries
        )
        full_evaluate = backend.evaluate_fresh if full_fresh else backend.evaluate
        full_rows = full_evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=full_registry,
            gold_map=gold_map,
            workers=workers,
        )
        for candidate in selected_entries:
            candidate_id = str(candidate.get("intervention_id") or "")
            without_entries = [
                entry for entry in selected_entries
                if str(entry.get("intervention_id") or "") != candidate_id
            ]
            without_registry = make_registry([*active_entries, *without_entries])
            without_rows = full_evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=without_registry,
                gold_map=gold_map,
                workers=workers,
            )
            selected_attribution.append({
                "intervention_id": candidate_id,
                "delta": paired_delta(without_rows, full_rows),
                "attribution": "leave_one_out_final_configuration",
            })
    return {
        "reports": reports,
        "joint_reports": joint_reports,
        "selected": selected_entries,
        "removed_redundant": removed,
        "selected_attribution": selected_attribution,
    }


def defect_candidates(cycle: int) -> list[dict[str, Any]]:
    case_leak = make_intervention(
        intervention_id=f"IV-DEFECT-{cycle}-001",
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
        cycle=cycle,
        failure_family="AUDIT-CASE_ID_LEAK",
        failure_cases=["BFCL/001.json", "BFCL/002.json"],
    )
    noise = make_intervention(
        intervention_id=f"IV-DEFECT-{cycle}-002",
        # This audit must exercise V2/V3/M without re-running a baseline
        # provider stage.  A scanner.system text boundary unfreezes recall
        # and spends real tokens before any candidate exists.  This closed
        # deterministic evidence check keeps the cached judge/recall/upgrade/
        # tracer/defense stages frozen while still producing a fired V3 trace
        # that M must reject for zero marginal gain.
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
        cycle=cycle,
        failure_family="AUDIT-NOISE_ZERO_MARGINAL",
        failure_cases=["BFCL/001.json", "BFCL/002.json"],
    )
    return [case_leak, noise]


def audit_defects(
    *,
    cycle: int,
    config: Mapping[str, Any],
    backend: Any,
    dataset_root: str,
    active_entries: Sequence[Mapping[str, Any]],
    baseline_sentinel_rows: Sequence[Mapping[str, Any]],
    sentinel_cases: Sequence[str],
    gain_cases: Sequence[str],
    baseline_gain_rows: Sequence[Mapping[str, Any]],
    gold_map: Mapping[str, bool | None] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    defects = defect_candidates(int(cycle))
    reports = []
    passed_any = False
    for candidate in defects:
        v1 = v1_structural(
            candidate, active_entries=active_entries, config=config,
        )
        report: dict[str, Any] = {
            "intervention_id": candidate.get("intervention_id"),
            "v1": v1,
            "v2": None,
            "v3": None,
            "m": None,
            "audit_expected_reject": True,
            "audit_rejected": not v1["passed"],
            "audit_passed": False,
        }
        if v1["passed"]:
            v2 = v2_harmlessness(
                candidate,
                active_entries=active_entries,
                baseline_rows=baseline_sentinel_rows,
                backend=backend,
                dataset_root=dataset_root,
                sentinel_cases=sentinel_cases,
                config=config,
                gold_map=gold_map,
                workers=workers,
            )
            report["v2"] = v2
            if v2["passed"]:
                # V3 needs S union G rows from the same active->active+candidate
                # comparison used by M.
                candidate_rows = []
                for rows, cases in (
                    (baseline_sentinel_rows, sentinel_cases),
                    (baseline_gain_rows, gain_cases),
                ):
                    candidate_rows.extend(
                        backend.evaluate(
                            list(cases),
                            dataset_root=dataset_root,
                            registry=make_registry([*active_entries, candidate]),
                            gold_map=gold_map,
                            workers=workers,
                        )
                    )
                v3 = v3_semantics(
                    candidate,
                    baseline_rows=[
                        *list(baseline_sentinel_rows),
                        *list(baseline_gain_rows),
                    ],
                    candidate_rows=candidate_rows,
                    config=config,
                )
                report["v3"] = v3
                if v3["passed"]:
                    m = marginal_candidate(
                        candidate,
                        active_entries=active_entries,
                        backend=backend,
                        dataset_root=dataset_root,
                        gain_cases=gain_cases,
                        active_rows=baseline_gain_rows,
                        config=config,
                        gold_map=gold_map,
                        workers=workers,
                    )
                    report["m"] = m
                    if m["passed"]:
                        report["audit_rejected"] = False
                        report["audit_passed"] = True
        if not report["audit_passed"]:
            # V1, V2, or V3/M failure is a successful audit rejection.
            report["audit_rejected"] = True
        if report["audit_passed"]:
            passed_any = True
        reports.append(report)
    return {
        "cycle": int(cycle),
        "passed": not passed_any,
        "frozen": passed_any,
        "reports": reports,
    }


__all__ = [
    "audit_defects",
    "defect_candidates",
    "marginal_candidate",
    "marginal_selection",
    "v1_structural",
    "v2_harmlessness",
    "v3_semantics",
]
