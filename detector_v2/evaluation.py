"""Evaluator-side metrics for a detector_v2 run tree.

Reports both decisive-only metrics (comparable to an always-decisive judge)
and all-case metrics (unresolved counted as wrong), plus macro-F1 in the
same label convention the benchmark uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector_v2.dataset import load_case
from detector_v2.result import prediction_state
from detector_v2.runner import safe_case_id


def binary_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rows have ``gold`` (bool) and ``prediction`` (bool or None)."""
    decisive = [row for row in rows if row["prediction"] is not None]
    if not decisive:
        return {
            "decisive": 0,
            "unresolved": len(rows),
            "completion": 0.0,
            "accuracy": None,
            "tp": 0, "fp": 0, "fn": 0, "tn": 0,
            "macro_precision": None, "macro_recall": None, "macro_f1": None,
        }
    tp = sum(1 for r in decisive if r["gold"] and r["prediction"])
    fp = sum(1 for r in decisive if not r["gold"] and r["prediction"])
    fn = sum(1 for r in decisive if r["gold"] and not r["prediction"])
    tn = sum(1 for r in decisive if not r["gold"] and not r["prediction"])

    def div(num: int, den: int) -> float:
        return num / den if den else 0.0

    pos_prec = div(tp, tp + fp)
    pos_rec = div(tp, tp + fn)
    neg_prec = div(tn, tn + fn)
    neg_rec = div(tn, tn + fp)
    pos_f1 = div(2 * pos_prec * pos_rec, pos_prec + pos_rec)
    neg_f1 = div(2 * neg_prec * neg_rec, neg_prec + neg_rec)
    return {
        "decisive": len(decisive),
        "unresolved": len(rows) - len(decisive),
        "completion": len(decisive) / len(rows),
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


def evaluate_run(
    *,
    runs_root: str | Path,
    run_id: str,
    dataset_root: str | Path,
    cases: Sequence[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    missing_cases: list[str] = []
    for case_id in cases:
        result_path = (
            Path(runs_root) / run_id / "cases"
            / safe_case_id(case_id) / "result.json"
        )
        _record, gold = load_case(dataset_root, case_id)
        raw = json.loads(
            (Path(dataset_root) / case_id).read_text(encoding="utf-8-sig")
        )
        gold_step = raw.get("hallucination_step")
        if str(gold_step or "").isdigit():
            gold_step = int(gold_step)
        else:
            gold_step = None
        label = gold.get("is_hallucination")
        if isinstance(label, bool):
            gold_bool = label
        elif label is None:
            raise ValueError(f"missing is_hallucination label for {case_id}")
        elif isinstance(label, str) and label.strip().lower() in {"true", "false"}:
            gold_bool = label.strip().lower() == "true"
        else:
            raise ValueError(f"invalid is_hallucination label for {case_id}: {label!r}")
        if not result_path.exists():
            missing_cases.append(case_id)
            rows.append({
                "case": case_id,
                "gold": gold_bool,
                "prediction": None,
                "gold_step": gold_step,
                "responsible_step": None,
                "state": "missing_result",
                "authority": "invalid",
            })
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        # A degraded terminal decision is still a detector outcome.  Keep
        # authoritative-only filtering for evolution gates, but Stage A's
        # provider-unresolved gate must only count an actual unresolved state.
        prediction = prediction_state(result, require_authoritative=False)
        rows.append({
            "case": case_id,
            "gold": gold_bool,
            "prediction": prediction,
            "gold_step": gold_step,
            "responsible_step": result.get("responsible_step"),
            "state": result.get("state"),
            "authority": result.get("decision_authority"),
        })
        if prediction is not None and prediction != gold_bool:
            errors.append({
                "case": case_id,
                "kind": "fp" if prediction and not gold_bool else "fn",
                "state": result.get("state"),
                "authority": result.get("decision_authority"),
                "judge": (
                    (result.get("proof") or {}).get("judge") or {}
                ),
                "defense": (
                    (result.get("proof") or {}).get("defense") or {}
                ),
            })

    metrics = binary_metrics(rows)
    metrics["authoritative"] = sum(
        1 for row in rows if row["authority"] == "authoritative"
    )
    metrics["degraded"] = sum(
        1 for row in rows
        if row["authority"] not in {"authoritative", "invalid"}
        and row["prediction"] is not None
    )
    metrics["provider_unresolved"] = sum(
        1 for row in rows if row["state"] in {"unresolved", "missing_result"}
    )
    metrics["requested_cases"] = len(cases)
    metrics["found_results"] = len(cases) - len(missing_cases)
    metrics["missing_cases"] = len(missing_cases)
    step_hits = 0
    step_total = 0
    for row in rows:
        if not row["gold"] or row["gold_step"] is None:
            continue
        step_total += 1
        if row["prediction"] is True and row["responsible_step"] == row["gold_step"]:
            step_hits += 1
    metrics["step_localization"] = {
        "n": step_total,
        "hits": step_hits,
        "accuracy": step_hits / step_total if step_total else None,
    }
    return {
        "run_id": run_id,
        "metrics": metrics,
        "rows": rows,
        "errors": errors,
        "missing_cases": missing_cases,
    }


__all__ = ["binary_metrics", "evaluate_run", "prediction_state"]
