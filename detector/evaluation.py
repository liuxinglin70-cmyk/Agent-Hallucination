"""Evaluator-side metrics for a detector run tree.

Reports decisive-only metrics in the same label convention the benchmark uses,
and reports them over **authoritative** terminal decisions: a degraded read (a
judge-only verdict taken while the recall net was down, say) is a measured
statement about the run, not a prediction about the case, so it is counted
separately instead of entering tp/fp/fn/tn. A non-authoritative row is not a
wrong answer by the detector -- it is an answer the detector did not give.

``terminal_metrics`` carries the older, looser reading for the caller that wants
it: every terminal decision counted, degraded ones included. The provider-health
gate reads that one, so the distinction stays available without being the number
the accuracy line reports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.dataset import load_case
from detector.result import prediction_state
from detector.runner import safe_case_id


def binary_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rows have ``gold`` (bool) and ``prediction`` (bool or None).

    The all-undecided case returns the same key set as the ordinary one -- with
    ``None`` where a ratio has no denominator. Two different shapes meant a
    reader could not ask for ``decisive_accuracy`` without first checking
    whether any case had been decided, which is exactly the state a
    provider outage produces.
    """
    decisive = [row for row in rows if row["prediction"] is not None]
    if not decisive:
        return {
            "decisive": 0,
            "unresolved": len(rows),
            "completion": 0.0,
            "accuracy": None,
            "decisive_accuracy": None,
            "all_case_accuracy": 0.0,
            "tp": 0, "fp": 0, "fn": 0, "tn": 0,
            "positive_precision": None,
            "positive_recall": None,
            "positive_f1": None,
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
    degraded_rows: list[dict[str, Any]] = []
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
                "terminal_prediction": None,
                "gold_step": gold_step,
                "responsible_step": None,
                "state": "missing_result",
                "authority": "invalid",
            })
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        # Two readings of one envelope, kept apart on purpose.
        #
        # ``prediction`` is what the accuracy line may count: only an
        # ``authoritative`` terminal decision is the detector's answer. The
        # loose reader below accepts a degraded terminal state, which is what
        # the provider-health accounting needs -- a judge-only clear produced
        # while the recall net was unreachable has to be visible as such --
        # but counting it as a prediction turned an outage into a false
        # positive against the detector.
        prediction = prediction_state(result, require_authoritative=True)
        terminal_prediction = prediction_state(
            result, require_authoritative=False,
        )
        rows.append({
            "case": case_id,
            "gold": gold_bool,
            "prediction": prediction,
            "terminal_prediction": terminal_prediction,
            "gold_step": gold_step,
            "responsible_step": result.get("responsible_step"),
            # What kind of step the verdict blames: the event's own structure
            # carries either assistant prose or tool calls, and the two are
            # not comparable. Left None for a row written before the envelope
            # recorded it, so "unclassified" stays distinguishable from
            # "classified as empty".
            "responsible_step_kind": (
                result.get("responsible_step_kind")
                or (result.get("proof") or {}).get("responsible_step_kind")
            ),
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
        elif (
            prediction is None
            and terminal_prediction is not None
            and terminal_prediction != gold_bool
        ):
            # Non-authoritative and, read loosely, wrong. Listed rather than
            # scored: a run has to be able to see these without having them
            # enter the accuracy line.
            degraded_rows.append({
                "case": case_id,
                "gold": gold_bool,
                "terminal_prediction": terminal_prediction,
                "state": result.get("state"),
                "authority": result.get("decision_authority"),
                "kind": (
                    "fp" if terminal_prediction and not gold_bool else "fn"
                ),
            })

    metrics = binary_metrics(rows)
    metrics["authoritative"] = sum(
        1 for row in rows if row["authority"] == "authoritative"
    )
    metrics["degraded"] = sum(
        1 for row in rows
        if row["authority"] not in {"authoritative", "invalid"}
        and row["terminal_prediction"] is not None
    )
    metrics["degraded_disagreeing"] = len(degraded_rows)
    # The older, looser reading: every terminal decision, degraded included.
    # Named ``terminal_metrics`` so a caller that wants it says so, and the
    # headline numbers stay authoritative-only.
    metrics["terminal_metrics"] = binary_metrics([
        {**row, "prediction": row["terminal_prediction"]} for row in rows
    ])
    metrics["provider_unresolved"] = sum(
        1 for row in rows if row["state"] in {"unresolved", "missing_result"}
    )
    # Rows that produced no terminal decision at all, whatever the authority
    # said: an unresolved envelope is not a degraded *answer*, and the two are
    # reported apart because the remedies differ (a provider problem versus a
    # stage that ran under a reduced view).
    metrics["no_terminal_decision"] = sum(
        1 for row in rows if row["terminal_prediction"] is None
    )
    metrics["requested_cases"] = len(cases)
    metrics["found_results"] = len(cases) - len(missing_cases)
    metrics["missing_cases"] = len(missing_cases)
    step_hits = 0
    step_total = 0
    by_kind: dict[str, dict[str, int]] = {}
    for row in rows:
        if not row["gold"] or row["gold_step"] is None:
            continue
        step_total += 1
        # Localization is only credited for an authoritative positive; a
        # responsible step read off a degraded verdict is not the detector's
        # answer any more than the verdict is.
        hit = (
            row["prediction"] is True
            and row["responsible_step"] == row["gold_step"]
        )
        if hit:
            step_hits += 1
        # Split by what the blamed step actually is. The overall rate mixes
        # two populations that the corpus annotates differently -- a verdict
        # that blames the tool call the agent made and one that blames the
        # prose it wrote are not the same claim -- so the reading is broken
        # out instead of being reported only as one average.
        kind = str(row.get("responsible_step_kind") or "unclassified")
        bucket = by_kind.setdefault(kind, {"n": 0, "hits": 0})
        bucket["n"] += 1
        bucket["hits"] += int(hit)
    for bucket in by_kind.values():
        bucket["accuracy"] = (
            bucket["hits"] / bucket["n"] if bucket["n"] else None
        )
    metrics["step_localization"] = {
        "n": step_total,
        "hits": step_hits,
        "accuracy": step_hits / step_total if step_total else None,
        # Reporting only; the overall rate above is unchanged. Two step kinds
        # are not comparable, so a reader must not average across them.
        "by_step_kind": by_kind,
        "note": (
            "action-step and content-step accuracy answer different questions; "
            "read them apart, never as one rate"
        ),
    }
    return {
        "run_id": run_id,
        "metrics": metrics,
        "rows": rows,
        "errors": errors,
        "degraded_disagreements": degraded_rows,
        "missing_cases": missing_cases,
    }


__all__ = ["binary_metrics", "evaluate_run", "prediction_state"]
