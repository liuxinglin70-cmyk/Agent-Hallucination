"""Gold-side paired metrics for the evolution gates.

LLM never scores a candidate. Every number in HARMLESSNESS/BEHAVIOR/M comes from the
external gold labels joined to the two detection arms.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def _bool_prediction(value: bool | None) -> bool | None:
    return True if value is True else (False if value is False else None)


def confusion_rows(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
    *,
    case_key: str = "case",
) -> list[dict[str, Any]]:
    base: dict[str, Mapping[str, Any]] = {}
    for row in baseline_rows:
        case = str(row.get(case_key) or "")
        if not case:
            raise ValueError("paired metrics received a row without a case id")
        if case in base:
            raise ValueError(f"duplicate baseline case in paired metrics: {case}")
        if row.get("gold") is not True and row.get("gold") is not False:
            raise ValueError(
                f"paired metrics baseline gold is not boolean for case {case}"
            )
        base[case] = row
    active_seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for active in active_rows:
        case = str(active.get(case_key) or "")
        if not case:
            raise ValueError("paired metrics received a row without a case id")
        if case in active_seen:
            raise ValueError(f"duplicate active case in paired metrics: {case}")
        active_seen.add(case)
        if case not in base:
            raise ValueError(
                f"paired metrics received unexpected active case: {case}"
            )
        baseline = base[case]
        if active.get("gold") is not True and active.get("gold") is not False:
            raise ValueError(
                f"paired metrics active gold is not boolean for case {case}"
            )
        if active.get("gold") is not baseline.get("gold"):
            raise ValueError(f"paired metrics gold mismatch for case {case}")
        base_pred = _bool_prediction(baseline.get("prediction"))
        active_pred = _bool_prediction(active.get("prediction"))
        rows.append({
            "case": case,
            "gold": bool(baseline.get("gold")),
            "baseline_prediction": base_pred,
            "active_prediction": active_pred,
            "baseline_state": baseline.get("state"),
            "active_state": active.get("state"),
        })
    missing = sorted(set(base) - active_seen)
    if missing:
        raise ValueError(
            "paired metrics missing active rows for case(s): "
            f"{missing[:5]}"
        )
    return rows


def binary_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisive = [row for row in rows if row.get("prediction") is not None]
    if not decisive:
        return {
            "n": len(rows), "decisive": 0, "unresolved": len(rows),
            "decisive_accuracy": None, "all_case_accuracy": None,
            "tp": 0, "fp": 0, "fn": 0, "tn": 0,
            "positive_precision": None, "positive_recall": None,
            "positive_f1": None, "macro_f1": None,
        }
    tp = sum(1 for r in decisive if r["gold"] and r["prediction"])
    fp = sum(1 for r in decisive if not r["gold"] and r["prediction"])
    fn = sum(1 for r in decisive if r["gold"] and not r["prediction"])
    tn = sum(1 for r in decisive if not r["gold"] and not r["prediction"])

    def div(a: int, b: int) -> float:
        return a / b if b else 0.0

    pos_prec = div(tp, tp + fp)
    pos_rec = div(tp, tp + fn)
    neg_prec = div(tn, tn + fn)
    neg_rec = div(tn, tn + fp)
    pos_f1 = div(2 * pos_prec * pos_rec, pos_prec + pos_rec)
    neg_f1 = div(2 * neg_prec * neg_rec, neg_prec + neg_rec)
    return {
        "n": len(rows),
        "decisive": len(decisive),
        "unresolved": len(rows) - len(decisive),
        "decisive_accuracy": (tp + tn) / len(decisive),
        "all_case_accuracy": (tp + tn) / len(rows),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "positive_precision": pos_prec,
        "positive_recall": pos_rec,
        "positive_f1": pos_f1,
        "macro_precision": (pos_prec + neg_prec) / 2,
        "macro_recall": (pos_rec + neg_rec) / 2,
        "macro_f1": (pos_f1 + neg_f1) / 2,
    }


def paired_delta(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Paired confusion delta for the same case set.

    ``marginal_cases`` is the number of all-case correct predictions gained;
    ``new_errors`` is correct-or-decisive -> wrong-or-unresolved transitions.
    """
    paired = confusion_rows(baseline_rows, active_rows)
    base_correct = sum(
        1 for r in paired
        if r["baseline_prediction"] is not None
        and r["baseline_prediction"] == r["gold"]
    )
    active_correct = sum(
        1 for r in paired
        if r["active_prediction"] is not None
        and r["active_prediction"] == r["gold"]
    )
    tp_lost = sum(
        1 for r in paired
        if r["gold"] and r["baseline_prediction"] is True
        and r["active_prediction"] is not True
    )
    fp_added = sum(
        1 for r in paired
        if not r["gold"] and r["baseline_prediction"] is not True
        and r["active_prediction"] is True
    )
    new_fn = sum(
        1 for r in paired
        if r["gold"]
        and (
            (r["baseline_prediction"] is True
             and r["active_prediction"] is not True)
            or (r["baseline_prediction"] is None
                and r["active_prediction"] is False)
        )
    )
    new_fp = fp_added
    unresolved_added = sum(
        1 for r in paired
        if r["baseline_prediction"] is not None
        and r["active_prediction"] is None
    )
    unresolved_removed = sum(
        1 for r in paired
        if r["baseline_prediction"] is None
        and r["active_prediction"] is not None
    )
    flips: dict[str, int] = {}
    transitions_by_gold: dict[str, dict[str, int]] = {"error": {}, "clean": {}}
    correctness_improved = 0
    correctness_worsened = 0
    for r in paired:
        a = r["baseline_prediction"]
        b = r["active_prediction"]
        key = f"{'T' if a is True else ('F' if a is False else 'U')}->"
        key += "T" if b is True else ("F" if b is False else "U")
        flips[key] = flips.get(key, 0) + 1
        gold_key = "error" if r["gold"] else "clean"
        transitions_by_gold[gold_key][key] = (
            transitions_by_gold[gold_key].get(key, 0) + 1
        )
        baseline_is_correct = a is not None and a == r["gold"]
        active_is_correct = b is not None and b == r["gold"]
        if active_is_correct and not baseline_is_correct:
            correctness_improved += 1
        elif baseline_is_correct and not active_is_correct:
            correctness_worsened += 1
    metrics_base = binary_metrics(baseline_rows)
    metrics_active = binary_metrics(active_rows)
    baseline_by_case = {
        str(item.get("case") or ""): item for item in baseline_rows
    }
    active_by_case = {
        str(item.get("case") or ""): item for item in active_rows
    }
    health_regressed = 0
    health_improved = 0
    for row in paired:
        baseline_health = _row_health(baseline_by_case.get(row["case"]))
        active_health = _row_health(active_by_case.get(row["case"]))
        if baseline_health and not active_health:
            health_regressed += 1
        elif not baseline_health and active_health:
            health_improved += 1
    return {
        "paired_n": len(paired),
        "base_all_case_correct": base_correct,
        "active_all_case_correct": active_correct,
        "marginal_cases": active_correct - base_correct,
        "tp_lost": tp_lost,
        "fp_added": fp_added,
        "new_fn": new_fn,
        "new_fp": new_fp,
        "new_errors": new_fn + new_fp,
        "unresolved_added": unresolved_added,
        "unresolved_removed": unresolved_removed,
        "health_regressed": health_regressed,
        "health_improved": health_improved,
        "flips": flips,
        "transitions_by_gold": transitions_by_gold,
        "correctness_improved": correctness_improved,
        "correctness_worsened": correctness_worsened,
        "baseline_metrics": metrics_base,
        "active_metrics": metrics_active,
        "rows": paired,
    }


def _row_health(row: Mapping[str, Any] | None) -> bool:
    if not row:
        return False
    if str(row.get("authority") or "") != "authoritative":
        return False
    result = row.get("result") or {}
    if result and not bool(result.get("runtime_valid", True)):
        return False
    for status in (result.get("stage_status") or {}).values():
        if str((status or {}).get("state") or "").startswith(
            ("provider_error", "invalid", "contract_error", "degraded")
        ):
            return False
    return True


def fired_rate(
    rows: Sequence[Mapping[str, Any]],
    intervention_id: str | None = None,
) -> float:
    if not rows:
        return 0.0
    fired_count = 0
    for row in rows:
        deployment = ((row.get("result") or {}).get("proof") or {}).get(
            "deployment"
        ) or {}
        fired = [item for item in deployment.get("fired") or []
                 if item.get("event") not in {"recall_trigger_not_matched", "predicate_evaluated"}]
        if intervention_id is not None:
            fired = [
                item for item in fired
                if str(item.get("intervention_id") or "") == intervention_id
            ]
        if fired:
            fired_count += 1
    return fired_count / len(rows)


def evidence_increment_rows(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
    intervention_id: str,
) -> int:
    base_map = {str(r.get("case") or ""): r for r in baseline_rows}
    count = 0
    for row in active_rows:
        base = base_map.get(str(row.get("case") or ""))
        result = row.get("result") or {}
        proof = result.get("proof") or {}
        active_evidence = proof.get("evidence") or []
        if not any(
            str(item.get("intervention_id") or "") == intervention_id
            for item in active_evidence
        ):
            continue
        base_evidence = ((base or {}).get("result") or {}).get("proof") or {}
        base_ids = {
            str(item.get("intervention_id") or "")
            for item in base_evidence.get("evidence") or ()
        }
        if intervention_id not in base_ids:
            count += 1
    return count


def source_of(case_id: str) -> str:
    return str(case_id).split("/", 1)[0].split("__", 1)[0]


def source_deltas(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    base_map = {str(r.get("case") or ""): r for r in baseline_rows}
    for row in active_rows:
        source = source_of(str(row.get("case") or ""))
        by_source.setdefault(source, []).append(
            {"baseline": base_map.get(str(row.get("case") or "")), "active": row}
        )
    result = {}
    for source, rows in by_source.items():
        base = [r["baseline"] for r in rows if r["baseline"] is not None]
        active = [r["active"] for r in rows if r["baseline"] is not None]
        result[source] = paired_delta(base, active)
    return result


__all__ = [
    "binary_metrics",
    "confusion_rows",
    "evidence_increment_rows",
    "fired_rate",
    "paired_delta",
    "source_deltas",
    "source_of",
]
