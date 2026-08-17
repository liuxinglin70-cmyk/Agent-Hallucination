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
    load_json,
    make_intervention,
    make_registry,
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


def _structured_key(entry: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """Executable identity for deterministic intervention types.

    Prose similarity is the wrong redundancy test for deterministic hooks:
    two evidence_check entries that share ``judge.post`` but execute
    different predicates are distinct and must not be rejected just because
    their natural-language claims overlap. ``text_boundary`` remains prose
    compared because its body is natural language by definition.
    """
    entry_type = str(entry.get("type") or "")
    hook = str(entry.get("hook") or "")
    body = entry.get("body") or {}
    if entry_type == "evidence_check":
        return (
            "evidence_check",
            hook,
            str(body.get("predicate") or ""),
            str(body.get("runner") or ""),
        )
    if entry_type == "decision_guard":
        trigger = entry.get("trigger") or {}
        facts = [str(item) for item in (trigger.get("facts") or ())]
        return (
            "decision_guard",
            hook,
            tuple(sorted(facts)),
            str(body.get("action") or ""),
        )
    return None


def v1_structural(
    candidate: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    try:
        validate_intervention(candidate)
    except ValueError as exc:
        return _policy_fail(f"schema/executor rejection: {exc}")

    case_id_hit = candidate_contains_case_id(candidate)
    if case_id_hit:
        return _policy_fail(f"case id leak in directive/body/claim: {case_id_hit}")

    provenance = candidate.get("provenance") or {}
    failure_cases = provenance.get("failure_cases") or []
    if len(failure_cases) < 2:
        return _policy_fail("provenance.failure_cases needs at least two cases")
    family = str(provenance.get("failure_family") or "")
    if not family:
        return _policy_fail("provenance.failure_family is empty")

    candidate_text = candidate_prose_text(candidate)
    base_text = _semantics_text()
    threshold = float(gate_cfg.get("v1_jaccard_threshold", 0.72))
    candidate_key = _structured_key(candidate)
    for existing in active_entries:
        existing_key = _structured_key(existing)
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
    candidate: Mapping[str, Any],
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    backend: Any,
    dataset_root: str,
    sentinel_cases: Sequence[str],
    config: Mapping[str, Any],
    gold_map: Mapping[str, bool | None] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    registry = make_registry([candidate])
    candidate_rows = backend.evaluate(
        list(sentinel_cases),
        dataset_root=dataset_root,
        registry=registry,
        gold_map=gold_map,
        workers=workers,
    )
    delta = paired_delta(list(baseline_rows), candidate_rows)
    checks = {
        "new_fn_allowed": int(gate_cfg.get("v2_new_fn_allowed", 0)),
        "new_fp_allowed": int(gate_cfg.get("v2_new_fp_allowed", 0)),
        "new_unresolved_allowed": int(
            gate_cfg.get("v2_new_unresolved_allowed", 0)
        ),
    }
    failures = []
    if delta["new_fn"] > checks["new_fn_allowed"]:
        failures.append(f"new_fn={delta['new_fn']}")
    if delta["new_fp"] > checks["new_fp_allowed"]:
        failures.append(f"new_fp={delta['new_fp']}")
    if delta["unresolved_added"] > checks["new_unresolved_allowed"]:
        failures.append(f"unresolved_added={delta['unresolved_added']}")
    if failures:
        return _policy_fail(
            "sentinel harmlessness violation: " + ", ".join(failures),
            delta=delta,
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
    return _policy_pass(delta=delta)


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

    rate = fired_rate(candidate_rows)
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
                if item.get("event") == "text_injected"
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
                if item.get("event") in {"guard_fired", "policy_conflict"}
            )
        behavior_ok = guard_fired > 0
        behavior_note = f"guard_fired_cases={guard_fired}"

    if not behavior_ok:
        return _policy_fail(
            f"claim behavior mismatch: {behavior_note}",
            fired_rate=rate,
        )

    delta = paired_delta(list(baseline_rows), candidate_rows)
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


def _calibration_floor(config: Mapping[str, Any], backend_name: str | None) -> int:
    path = config.get("gate_calibration")
    if not path:
        return 0
    try:
        payload = load_json(path)
    except Exception:
        return 0
    if not isinstance(payload, Mapping):
        return 0
    if backend_name and payload.get("backend") != backend_name:
        return 0
    return max(0, int(payload.get("empty_rerun_positive_net") or 0))


def _meets_m(
    delta: Mapping[str, Any],
    config: Mapping[str, Any],
    backend_name: str | None = None,
) -> tuple[bool, list[str]]:
    gate_cfg = config.get("gates") or {}
    failures = []
    minimum = max(
        int(gate_cfg.get("m_min_marginal_cases", 1)),
        _calibration_floor(config, backend_name),
    )
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
    rows = backend.evaluate(
        list(gain_cases),
        dataset_root=dataset_root,
        registry=registry,
        gold_map=gold_map,
        workers=workers,
    )
    delta = paired_delta(list(active_rows), rows)
    passed, failures = _meets_m(
        delta, config, backend_name=getattr(backend, "name", None),
    )
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
        rows = backend.evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        delta = paired_delta(list(active_rows), rows)
        passed, failures = _meets_m(
            delta, config, backend_name=getattr(backend, "name", None),
        )
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
        rows = backend.evaluate(
            list(gain_cases),
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
        )
        delta = paired_delta(list(active_rows), rows)
        passed, _failures = _meets_m(
            delta, config, backend_name=getattr(backend, "name", None),
        )
        if passed:
            selected_reports.append(report)
            budget -= 1

    # Decompose individual combinations that only add width, not joint gain.
    removed: list[str] = []
    if len(selected_reports) > 1:
        for kept in selected_reports:
            without = [item for item in selected_reports if item is not kept]
            if not without:
                continue
            registry = make_registry([
                *active_entries, *[item["candidate"] for item in without],
            ])
            rows = backend.evaluate(
                list(gain_cases),
                dataset_root=dataset_root,
                registry=registry,
                gold_map=gold_map,
                workers=workers,
            )
            delta = paired_delta(list(active_rows), rows)
            passed, _failures = _meets_m(
                delta, config, backend_name=getattr(backend, "name", None),
            )
            if passed:
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
            joint_report["selected"] = True
            selected_entries.extend(entries)
            budget -= len(entries)
        else:
            joint_report["reason"] = (
                f"{joint_report.get('reason') or 'joint M passed'}; "
                f"not selected: remaining budget {budget} < {len(entries)}"
            )
    return {
        "reports": reports,
        "joint_reports": joint_reports,
        "selected": selected_entries,
        "removed_redundant": removed,
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
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Re-read the trajectory once before writing candidates.",
            "not_when": "Never apply when the packet has no tool calls.",
        },
        expected_behavior="gate audit: fires harmlessly but must fail M for zero marginal gain",
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
                # V3 needs S union G candidate rows; run candidate on both.
                candidate_rows = []
                for rows, cases in (
                    (baseline_sentinel_rows, sentinel_cases),
                    (baseline_gain_rows, gain_cases),
                ):
                    candidate_rows.extend(
                        backend.evaluate(
                            list(cases),
                            dataset_root=dataset_root,
                            registry=make_registry([candidate]),
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
