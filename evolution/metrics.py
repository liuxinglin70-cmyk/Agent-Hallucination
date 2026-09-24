"""Gold-side paired metrics for the evolution gates.

LLM never scores a candidate. Every number in HARMLESSNESS/BEHAVIOR/M comes from the
external gold labels joined to the two detection arms.
"""

from __future__ import annotations

from typing import Any, Collection, Mapping, Sequence

from detector.measurement import measurement_status
from detector.result import RESULT_SCHEMA, prediction_state


class EvaluationState:
    """The 8 evaluator-side states for single-case prediction quality and health."""

    AUTHORITATIVE_CORRECT = "authoritative_correct"
    AUTHORITATIVE_WRONG = "authoritative_wrong"
    SEMANTIC_UNRESOLVED_HEALTHY = "semantic_unresolved_healthy"
    DEGRADED = "degraded"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    FATAL_PROVIDER = "fatal_provider"
    NOT_STARTED = "not_started"
    INVALID_ENVELOPE = "invalid_envelope"

    ALL = frozenset({
        "authoritative_correct",
        "authoritative_wrong",
        "semantic_unresolved_healthy",
        "degraded",
        "infrastructure_failure",
        "fatal_provider",
        "not_started",
        "invalid_envelope",
    })


EVALUATION_STATE_SCHEMA = "evolution.evaluation_state.v1"
PAIRED_STATE_TRANSITIONS_SCHEMA = "evolution.paired_state_transitions.v1"


def _bool_prediction(value: bool | None) -> bool | None:
    """A prediction, or ``None`` for "no decision", and nothing else.

    ``None`` is a legitimate value here -- it is how an unresolved row reads,
    and the paired deltas are built around it. Anything *else* (1/0 from a
    hand-written row, "true" from a third-party backend) used to be demoted to
    ``None`` as well, which silently converts a real decision into an
    abstention: the control arm then looks like it decided nothing and the
    candidate appears to gain cases it never moved. The strict reader
    (``detector.result.prediction_state``) rejects those values, so accepting
    them here would be the looser of two standards for the same quantity.
    """
    if value is True or value is False:
        return value
    if value is None:
        return None
    raise ValueError(
        f"prediction must be True, False or None; got {value!r} "
        f"({type(value).__name__})"
    )


def evaluate_row_state(row: Mapping[str, Any]) -> str:
    """Classify a row or detection result into one of the 8 EvaluationState values."""
    if not isinstance(row, Mapping):
        return EvaluationState.INVALID_ENVELOPE

    explicit_state = row.get("evaluation_state")
    if explicit_state in EvaluationState.ALL:
        return str(explicit_state)

    if str(row.get("state") or "") == "not_started":
        return EvaluationState.NOT_STARTED

    result = row.get("result")
    if not isinstance(result, Mapping) and str(row.get("schema_version") or "") == RESULT_SCHEMA:
        result = row

    gold = row.get("gold")
    has_bool_gold = gold is True or gold is False

    if isinstance(result, Mapping) and result:
        status = measurement_status(result)
        if status == "invalid_envelope":
            return EvaluationState.INVALID_ENVELOPE
        if status == "not_started":
            return EvaluationState.NOT_STARTED
        if status == "fatal_provider":
            return EvaluationState.FATAL_PROVIDER
        if status == "infrastructure_failure":
            return EvaluationState.INFRASTRUCTURE_FAILURE
        if status == "degraded":
            return EvaluationState.DEGRADED
        if status == "authoritative":
            pred = prediction_state(result)
            if pred is None and row.get("prediction") is not None:
                try:
                    pred = _bool_prediction(row.get("prediction"))
                except ValueError:
                    pred = None
            if pred is not None and has_bool_gold:
                return (
                    EvaluationState.AUTHORITATIVE_CORRECT
                    if pred == gold
                    else EvaluationState.AUTHORITATIVE_WRONG
                )
            if pred is not None:
                return EvaluationState.DEGRADED
            return EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
        if status == "semantic_unresolved":
            is_valid = result.get("runtime_valid") is True
            authority = str(result.get("decision_authority") or "")
            if not is_valid or authority.startswith("degraded"):
                return EvaluationState.DEGRADED
            statuses = list((result.get("stage_status") or {}).values())
            for hearing in (result.get("proof") or {}).get("hearings") or ():
                statuses.extend(
                    hearing.get(name) or {} for name in ("tracer_status", "defense_status")
                )
            if any(
                str((s or {}).get("state") or "").startswith(
                    ("provider_error", "invalid", "contract_error", "degraded", "no_quorum", "timeout")
                )
                for s in statuses
            ):
                return EvaluationState.INFRASTRUCTURE_FAILURE
            return EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY

    explicit_m_status = row.get("measurement_status")
    if explicit_m_status:
        if explicit_m_status == "fatal_provider":
            return EvaluationState.FATAL_PROVIDER
        if explicit_m_status == "infrastructure_failure":
            return EvaluationState.INFRASTRUCTURE_FAILURE
        if explicit_m_status == "degraded":
            return EvaluationState.DEGRADED
        if explicit_m_status == "not_started":
            return EvaluationState.NOT_STARTED
        if explicit_m_status == "invalid_envelope":
            return EvaluationState.INVALID_ENVELOPE

    authority = str(row.get("authority") or row.get("decision_authority") or "authoritative")
    if authority.startswith("degraded"):
        return EvaluationState.DEGRADED
    if authority in ("infrastructure_failure", "fatal_provider"):
        return authority

    pred = None
    if "prediction" in row and row["prediction"] is not None:
        try:
            pred = _bool_prediction(row["prediction"])
        except ValueError:
            return EvaluationState.INVALID_ENVELOPE

    if pred is not None:
        if has_bool_gold:
            return (
                EvaluationState.AUTHORITATIVE_CORRECT
                if pred == gold
                else EvaluationState.AUTHORITATIVE_WRONG
            )
        return EvaluationState.DEGRADED

    if row.get("error_type") or row.get("authority_failure_reasons"):
        return EvaluationState.INFRASTRUCTURE_FAILURE

    return EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY


def classify_transition(base_state: str, active_state: str) -> str:
    """Classify the transition between baseline and active evaluation states."""
    if active_state in (EvaluationState.INFRASTRUCTURE_FAILURE, EvaluationState.FATAL_PROVIDER):
        if base_state not in (EvaluationState.INFRASTRUCTURE_FAILURE, EvaluationState.FATAL_PROVIDER):
            return "health_regressed"
        return "still_infrastructure_failure"

    if base_state in (EvaluationState.INFRASTRUCTURE_FAILURE, EvaluationState.FATAL_PROVIDER):
        if active_state in (
            EvaluationState.AUTHORITATIVE_CORRECT,
            EvaluationState.AUTHORITATIVE_WRONG,
            EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY,
        ):
            return "service_recovery"

    if active_state == EvaluationState.DEGRADED and base_state != EvaluationState.DEGRADED:
        return "degraded_added"
    if base_state == EvaluationState.DEGRADED and active_state != EvaluationState.DEGRADED:
        return "degraded_recovered"

    if (
        base_state == EvaluationState.AUTHORITATIVE_WRONG
        and active_state == EvaluationState.AUTHORITATIVE_CORRECT
    ):
        return "decisive_fixed"

    if (
        base_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
        and active_state == EvaluationState.AUTHORITATIVE_CORRECT
    ):
        return "resolution_correct"

    if (
        base_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
        and active_state == EvaluationState.AUTHORITATIVE_WRONG
    ):
        return "resolution_wrong"

    if (
        base_state == EvaluationState.AUTHORITATIVE_CORRECT
        and active_state == EvaluationState.AUTHORITATIVE_WRONG
    ):
        return "correctness_worsened"

    if (
        base_state == EvaluationState.AUTHORITATIVE_CORRECT
        and active_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
    ):
        return "unresolved_added"

    if (
        base_state == EvaluationState.AUTHORITATIVE_WRONG
        and active_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
    ):
        return "wrong_to_abstain"

    if (
        base_state == EvaluationState.AUTHORITATIVE_WRONG
        and active_state == EvaluationState.AUTHORITATIVE_WRONG
    ):
        return "still_wrong"

    if (
        base_state == EvaluationState.AUTHORITATIVE_CORRECT
        and active_state == EvaluationState.AUTHORITATIVE_CORRECT
    ):
        return "still_correct"

    if (
        base_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
        and active_state == EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
    ):
        return "still_unresolved"

    return f"{base_state}->{active_state}"


def paired_state_transitions_v1(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
    *,
    case_key: str = "case",
) -> dict[str, Any]:
    """Calculate the paired evaluation state transition matrix and metrics.

    Does not modify paired_delta(), preserving backward compatibility.
    """
    base_map: dict[str, Mapping[str, Any]] = {}
    for row in baseline_rows:
        case = str(row.get(case_key) or "")
        if not case:
            raise ValueError("paired state transitions received a row without a case id")
        if case in base_map:
            raise ValueError(f"duplicate baseline case in paired transitions: {case}")
        base_map[case] = row

    active_seen: set[str] = set()
    per_case_rows: list[dict[str, Any]] = []

    matrix: dict[str, dict[str, int]] = {
        s1: {s2: 0 for s2 in EvaluationState.ALL}
        for s1 in EvaluationState.ALL
    }
    transition_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}

    summary = {
        "decisive_fixed": 0,
        "resolution_correct": 0,
        "resolution_correct_positive": 0,
        "resolution_correct_negative": 0,
        "resolution_wrong": 0,
        "resolution_wrong_fp": 0,
        "resolution_wrong_fn": 0,
        "correctness_worsened": 0,
        "new_fp": 0,
        "new_fn": 0,
        "unresolved_added": 0,
        "wrong_to_abstain": 0,
        "still_wrong": 0,
        "still_correct": 0,
        "still_unresolved": 0,
        "health_regressed": 0,
        "service_recovery": 0,
        "degraded_added": 0,
        "degraded_recovered": 0,
    }

    for active in active_rows:
        case = str(active.get(case_key) or "")
        if not case:
            raise ValueError("paired state transitions received a row without a case id")
        if case in active_seen:
            raise ValueError(f"duplicate active case in paired transitions: {case}")
        active_seen.add(case)
        if case not in base_map:
            raise ValueError(f"paired state transitions received unexpected active case: {case}")

        baseline = base_map[case]
        base_gold = baseline.get("gold")
        active_gold = active.get("gold")
        if (base_gold is True or base_gold is False) and (active_gold is True or active_gold is False):
            if base_gold != active_gold:
                raise ValueError(f"paired state transitions gold mismatch for case {case}")
        gold = base_gold if (base_gold is True or base_gold is False) else active_gold

        base_state = evaluate_row_state(baseline)
        active_state = evaluate_row_state(active)
        transition = f"{base_state}->{active_state}"
        category = classify_transition(base_state, active_state)

        matrix[base_state][active_state] += 1
        transition_counts[transition] = transition_counts.get(transition, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

        if category == "decisive_fixed":
            summary["decisive_fixed"] += 1
        elif category == "resolution_correct":
            summary["resolution_correct"] += 1
            if gold is True:
                summary["resolution_correct_positive"] += 1
            elif gold is False:
                summary["resolution_correct_negative"] += 1
        elif category == "resolution_wrong":
            summary["resolution_wrong"] += 1
            if gold is False:
                summary["resolution_wrong_fp"] += 1
            elif gold is True:
                summary["resolution_wrong_fn"] += 1
        elif category == "correctness_worsened":
            summary["correctness_worsened"] += 1
            if gold is False:
                summary["new_fp"] += 1
            elif gold is True:
                summary["new_fn"] += 1
        elif category == "unresolved_added":
            summary["unresolved_added"] += 1
        elif category == "wrong_to_abstain":
            summary["wrong_to_abstain"] += 1
        elif category == "still_wrong":
            summary["still_wrong"] += 1
        elif category == "still_correct":
            summary["still_correct"] += 1
        elif category == "still_unresolved":
            summary["still_unresolved"] += 1
        elif category == "health_regressed":
            summary["health_regressed"] += 1
        elif category == "service_recovery":
            summary["service_recovery"] += 1
        elif category == "degraded_added":
            summary["degraded_added"] += 1
        elif category == "degraded_recovered":
            summary["degraded_recovered"] += 1

        per_case_rows.append({
            "case": case,
            "gold": gold,
            "baseline_state": base_state,
            "active_state": active_state,
            "transition": transition,
            "category": category,
        })

    missing = sorted(set(base_map) - active_seen)
    if missing:
        raise ValueError(f"paired state transitions missing active rows for case(s): {missing[:5]}")

    return {
        "schema_version": PAIRED_STATE_TRANSITIONS_SCHEMA,
        "n": len(per_case_rows),
        "matrix": matrix,
        "transition_counts": transition_counts,
        "category_counts": category_counts,
        "summary": summary,
        "rows": per_case_rows,
    }


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
    *,
    unstable_cases: Collection[str] | None = None,
) -> dict[str, Any]:
    """Paired confusion delta for the same case set.

    ``marginal_cases`` is the number of all-case correct predictions gained;
    ``new_errors`` is correct-or-decisive -> wrong-or-unresolved transitions.

    ``unstable_cases`` names cases the *baseline configuration itself* read
    differently when it was rolled twice (``flipped_cases``). Supplying it adds
    annotations only -- it never changes a field defined above, and it never
    subtracts a harm. A gain measured on a case the same configuration reads two
    ways is not evidence about the candidate, so the counts are reported
    separately for the gate to weigh:

    * ``marginal_cases_on_unstable`` / ``marginal_cases_on_stable`` split the
      benefit, and the gates score the benefit floor on the stable part;
    * ``new_errors_on_unstable`` / ``unresolved_added_on_unstable`` say how much
      of the recorded harm sits on those same cases. They are never subtracted:
      "the candidate broke it, so the adjudicator gave up on it" being removed
      would let a candidate launder a regression as instrument noise.

    Without ``unstable_cases`` (``None``, the default) every annotation is zero
    and ``stability_applicable`` is false, so callers that have no reroll --
    frozen replays, promotion's cached arms -- keep their exact meaning.
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
    # Decisive-only flips: one real decision replaced by another. Both
    # predictions must exist, so "stopped abstaining" and "started abstaining"
    # are excluded -- those are calibration and infrastructure movements,
    # reported separately as resolved/added above. The boundary BEHAVIOR check
    # (``gates._decisive_correction``) and the final verdict both score this
    # pair; one definition keeps them from disagreeing about what "improved"
    # means.
    decisive_fixed = 0
    decisive_broken = 0
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
        if a is not None and b is not None:
            if active_is_correct and not baseline_is_correct:
                decisive_fixed += 1
            elif baseline_is_correct and not active_is_correct:
                decisive_broken += 1
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
        # Stamped per case, not only summed: "which case lost its health" is
        # what an attribution question asks, and a count cannot answer it.
        row["baseline_health"] = baseline_health
        row["active_health"] = active_health
        if baseline_health and not active_health:
            health_regressed += 1
        elif not baseline_health and active_health:
            health_improved += 1
    stability = _stability_annotation(
        paired, unstable_cases, marginal=active_correct - base_correct,
    )
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
        "decisive_fixed": decisive_fixed,
        "decisive_broken": decisive_broken,
        "baseline_metrics": metrics_base,
        "active_metrics": metrics_active,
        "rows": paired,
        **stability,
    }


def _stability_annotation(
    paired: Sequence[Mapping[str, Any]],
    unstable_cases: Collection[str] | None,
    *,
    marginal: int,
) -> dict[str, Any]:
    """The unstable-case reading of one paired delta, as extra fields.

    Kept in one place so ``paired_delta`` and ``paired_localization_delta``
    cannot disagree about which cases were unstable or about what a "gain on an
    unstable case" counts.
    """
    if unstable_cases is None:
        return {
            "stability_applicable": False,
            "unstable_cases": [],
            "marginal_cases_on_unstable": 0,
            "marginal_cases_on_stable": 0,
            "new_errors_on_unstable": 0,
            "unresolved_added_on_unstable": 0,
        }
    wanted = {str(case) for case in unstable_cases}
    in_scope = sorted(
        str(row["case"]) for row in paired if str(row["case"]) in wanted
    )
    on_unstable = 0
    new_errors_on_unstable = 0
    unresolved_added_on_unstable = 0
    for row in paired:
        if str(row["case"]) not in wanted:
            continue
        gold = row["gold"]
        base = row["baseline_prediction"]
        active = row["active_prediction"]
        base_correct = base is not None and base == gold
        active_correct = active is not None and active == gold
        if active_correct and not base_correct:
            on_unstable += 1
        if gold:
            if (base is True and active is not True) or (
                base is None and active is False
            ):
                new_errors_on_unstable += 1
        elif base is not True and active is True:
            new_errors_on_unstable += 1
        if base is not None and active is None:
            unresolved_added_on_unstable += 1
    return {
        "stability_applicable": True,
        "unstable_cases": in_scope,
        "marginal_cases_on_unstable": on_unstable,
        "marginal_cases_on_stable": marginal - on_unstable,
        "new_errors_on_unstable": new_errors_on_unstable,
        "unresolved_added_on_unstable": unresolved_added_on_unstable,
    }


# How much of the instrument's own jitter a round managed to read. The three
# states are deliberately distinct in the report, because "the instrument
# cannot move" and "nobody looked" are different facts and only one of them
# licenses reading a delta as if it were exact.
NOISE_FLOOR_MEASURED = "measured"
NOISE_FLOOR_DETERMINISTIC = "deterministic_replay"
NOISE_FLOOR_NOT_MEASURED = "not_measured"


def flipped_cases(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Cases one configuration read differently on two independent rolls.

    The two arms are the *same* registry rolled twice, so any difference here
    is the instrument's own jitter -- not the effect of anything. A case counts
    as flipped when its terminal reading moved: the decision
    (``True``/``False``/``None``), the state, or the row's health (authority,
    runtime validity, a failed stage). Health is compared as well as the
    decision because a row that reads "provider_error on causal_upgrade" on one
    roll and decides on the other is not a stable measurement either, even when
    both rolls happen to end in the same verdict.

    A case read on one roll only counts as flipped: it is not a pair, and the
    conservative direction for every caller of this function is "unstable".
    """
    baseline = {str(row.get("case") or ""): row for row in baseline_rows}
    out: set[str] = set()
    seen: set[str] = set()
    for row in active_rows:
        case = str(row.get("case") or "")
        seen.add(case)
        other = baseline.get(case)
        if other is None:
            out.add(case)
            continue
        if _bool_prediction(row.get("prediction")) != _bool_prediction(
            other.get("prediction")
        ):
            out.add(case)
        elif str(row.get("state") or "") != str(other.get("state") or ""):
            out.add(case)
        elif _row_health(row) != _row_health(other):
            out.add(case)
    out.update(case for case in baseline if case not in seen)
    out.discard("")
    return sorted(out)


def noise_floor(
    *,
    reroll: Mapping[str, Any] | None = None,
    flipped: Sequence[str] = (),
    provider_stage: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """The instrument's own jitter for one round, as a report block.

    ``reroll`` is the paired delta between two independent rolls of the *same*
    active configuration, and ``flipped`` the cases those two rolls disagreed
    on. The three possible readings are kept apart on purpose:

    * ``measured`` -- two rolls were paired, so the round has a number;
    * ``deterministic_replay`` -- no provider stage ran, so every arm was a
      frozen replay and the floor is a *known* zero, not an unmeasured one;
    * ``not_measured`` -- a provider stage ran but the round took only one roll
      of the active configuration, so the floor is unknown. The quantities are
      ``None`` rather than 0: reporting an unlooked-at floor as zero would say
      the instrument was checked and found still.

    A reader must be able to tell a measured zero from either of the other two,
    which is what ``status`` and ``applicable`` are for.
    """
    if reroll is not None:
        note = (
            reason
            or "two independent rolls of the active configuration were paired "
            "this round (S+G)"
        )
        return {
            "status": NOISE_FLOOR_MEASURED,
            "applicable": True,
            "rolls": 2,
            "reason": note,
            "paired_n": int(reroll.get("paired_n") or 0),
            "marginal_cases": int(reroll.get("marginal_cases") or 0),
            "flips": dict(reroll.get("flips") or {}),
            "new_errors": int(reroll.get("new_errors") or 0),
            "unresolved_added": int(reroll.get("unresolved_added") or 0),
            "cases_flipped": len(flipped),
            "flipped_cases": sorted(str(case) for case in flipped),
        }
    if provider_stage:
        return {
            "status": NOISE_FLOOR_NOT_MEASURED,
            "applicable": False,
            "rolls": 1,
            "reason": reason or (
                "a provider stage ran this round but only one roll of the "
                "active configuration was taken, so the instrument's own "
                "jitter is unknown"
            ),
            "paired_n": None,
            "marginal_cases": None,
            "flips": None,
            "new_errors": None,
            "unresolved_added": None,
            "cases_flipped": None,
            "flipped_cases": [],
        }
    return {
        "status": NOISE_FLOOR_DETERMINISTIC,
        "applicable": False,
        "rolls": 1,
        "reason": reason or (
            "every arm of this comparison is a frozen replay, so the "
            "instrument's own jitter is zero by construction"
        ),
        "paired_n": None,
        "marginal_cases": 0,
        "flips": {},
        "new_errors": 0,
        "unresolved_added": 0,
        "cases_flipped": 0,
        "flipped_cases": [],
    }


def noise_floor_flipped_cases(block: Mapping[str, Any] | None) -> list[str]:
    """The flipped cases a noise-floor block licenses as unstable.

    Only a *measured* block names cases: an unmeasured floor has no flip set,
    and treating "no reroll was taken" as "nothing flipped" would hand a
    provider-stage round the same confidence as a frozen replay.
    """
    if not block or str(block.get("status") or "") != NOISE_FLOOR_MEASURED:
        return []
    return [str(case) for case in block.get("flipped_cases") or ()]


def repeated_roll_disagreement(
    rolls: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Which cases one configuration read differently across N independent rolls.

    The cycle can afford two rolls, so its noise floor is a single paired
    reading, and a case that disagrees only rarely can pass it unremarked. A
    repeat probe answers the same question with a dispersion estimate instead:
    in the RSI pilot, ``BFCL/008`` and ``BFCL/024`` read 6/2 across 8 repeats of
    one configuration, which no single-case claim can survive. That reading
    belongs in the metrics, not in a paragraph of a report, because the
    decision it feeds -- "this case may not be scored alone" -- is the same one
    ``flipped_cases`` feeds from two rolls.

    ``rolls`` is one sequence of rows per independent roll of the same
    configuration, all covering the same case set.
    """
    tallies: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for roll in rolls:
        for row in roll:
            case = str(row.get("case") or "")
            if not case:
                continue
            if case not in tallies:
                tallies[case] = {}
                order.append(case)
            state = str(row.get("state") or "")
            if not state:
                prediction = _bool_prediction(row.get("prediction"))
                state = (
                    "verified_error" if prediction is True
                    else "verified_clean" if prediction is False
                    else "unresolved"
                )
            tallies[case][state] = tallies[case].get(state, 0) + 1
    cases = [
        {
            "case": case,
            "reads": sum(tallies[case].values()),
            "tallies": dict(sorted(tallies[case].items())),
            "unstable": len(tallies[case]) > 1,
        }
        for case in sorted(order)
    ]
    unstable = [item["case"] for item in cases if item["unstable"]]
    return {
        "rolls": len(rolls),
        "cases": cases,
        "unstable_cases": unstable,
        "unstable_case_count": len(unstable),
        "stable_case_count": len(cases) - len(unstable),
        "unstable_share": (len(unstable) / len(cases)) if cases else None,
    }


def gain_opportunity(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many cases the marginal set can actually reward, per polarity.

    ``marginal_cases`` counts all-case correct predictions gained, so a claim
    can only be scored where the control arm is *wrong in that direction*. This
    is a property of the instrument (the evaluation set together with the
    baseline's behaviour), not of any candidate, which is why it is reported
    per round rather than inferred from a rejection.
    """
    return {
        "recall": sum(
            1 for row in rows
            if row.get("gold") is True and row.get("prediction") is False
        ),
        "precision": sum(
            1 for row in rows
            if row.get("gold") is False and row.get("prediction") is True
        ),
    }


def localization_opportunity(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """How many cases a localization claim can actually reward.

    A localization claim is only scorable where the control arm already
    convicts authoritatively *and* names the wrong responsible step: those are
    the cases whose localization can improve. Cases the control localizes
    correctly are the preservation pool, not opportunities.
    """
    gold_steps = dict(gold_steps or {})
    improvable = preserved = unannotated = 0
    for row in rows:
        gold_step = _gold_step(row, gold_steps)
        if row.get("gold") is not True:
            continue
        if gold_step is None:
            unannotated += 1
            continue
        if row.get("prediction") is not True:
            continue
        if row_responsible_step(row) == gold_step:
            preserved += 1
        else:
            improvable += 1
    return {
        "improvable": improvable,
        "preserved": preserved,
        "unannotated_positive": unannotated,
    }


def resolution_opportunity(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """How many cases a resolution claim can actually reward.

    A resolution opportunity requires:
    1. Evaluator holds a boolean gold label.
    2. Baseline measurement is healthy but unresolved (EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY).
    Ineligible cases (infrastructure failure, degraded mode, missing gold) are excluded.
    """
    pos = neg = 0
    ineligible = 0
    for row in rows:
        gold = row.get("gold")
        if gold is not True and gold is not False:
            ineligible += 1
            continue
        state = evaluate_row_state(row)
        if state != EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY:
            continue
        if gold is True:
            pos += 1
        else:
            neg += 1
    return {
        "total": pos + neg,
        "positive": pos,
        "negative": neg,
        "ineligible": ineligible,
    }


def mechanism_opportunity(
    baseline_rows: Sequence[Mapping[str, Any]],
    mechanism: Mapping[str, Any],
    mechanism_membership: Mapping[str, Any],
    *,
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Count opportunities only in evaluator-confirmed mechanism members.

    Membership is evidence supplied before candidate evaluation, not something
    inferred from an identifier, a source, a prediction or a shared path.
    """
    mechanism_id = str(mechanism.get("mechanism_id") or "")
    objective = str(mechanism.get("objective") or "")
    polarity = str(mechanism.get("target_polarity") or "boundary")
    cases = [row.get("case") for row in baseline_rows]
    if any(not isinstance(case, str) or not case for case in cases):
        raise ValueError("mechanism opportunity needs non-empty case ids")
    if len(set(cases)) != len(cases):
        raise ValueError("mechanism opportunity received duplicate cases")
    opportunity_cases: list[str] = []
    correct_cases: list[str] = []
    unresolved_cases: list[str] = []
    unconfirmed_cases: list[str] = []
    ineligible_cases: list[str] = []
    target_cases: list[str] = []
    detection_errors = localization_errors = 0
    for row in baseline_rows:
        case = row["case"]
        membership = mechanism_membership.get(case)
        if not isinstance(membership, Mapping):
            unconfirmed_cases.append(case)
            continue
        if membership.get("mechanism_id") != mechanism_id:
            continue
        evidence = membership.get("evidence")
        if (
            membership.get("status") != "confirmed"
            or not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
        ):
            unconfirmed_cases.append(case)
            continue
        target_cases.append(case)
        gold, prediction = row.get("gold"), row.get("prediction")
        if objective == "resolution":
            state = evaluate_row_state(row)
            if type(gold) is not bool or state != EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY:
                unresolved_cases.append(case)
                continue
            polarity_match = (
                polarity == "boundary"
                or (polarity == "recall" and gold)
                or (polarity == "precision" and not gold)
            )
            if membership.get("repairable") is True and polarity_match:
                opportunity_cases.append(case)
            else:
                ineligible_cases.append(case)
            continue

        if type(gold) is not bool or type(prediction) is not bool or not mechanism_row_healthy(row):
            unresolved_cases.append(case)
            continue
        gold_step = (gold_steps or {}).get(case, row.get("gold_step"))
        annotated = type(gold_step) is int and gold_step > 0
        step = row.get("responsible_step", (row.get("result") or {}).get("responsible_step"))
        step_hit = annotated and type(step) is int and step == gold_step
        detection_correct = prediction is gold
        if objective in {"localization", "joint"} and gold and not annotated:
            unresolved_cases.append(case)
            continue
        correct = detection_correct if objective == "detection" else (
            prediction is True and step_hit if gold else prediction is False
        )
        if correct:
            correct_cases.append(case)
            continue
        detection_error = (
            not detection_correct
            and objective in {"detection", "joint"}
            and (polarity == "boundary" or (polarity == "recall" and gold)
                 or (polarity == "precision" and not gold))
        )
        localization_error = (
            objective in {"localization", "joint"}
            and gold and prediction and annotated and not step_hit
            and membership.get("localization_viable") is True
        )
        if membership.get("repairable") is True and (detection_error or localization_error):
            opportunity_cases.append(case)
            detection_errors += int(detection_error)
            localization_errors += int(localization_error)
        else:
            ineligible_cases.append(case)
    valid = bool(mechanism_id) and objective in {"detection", "localization", "joint", "resolution"}
    if not valid or polarity not in {"recall", "precision", "boundary"}:
        reason = "mechanism_invalid"
    elif mechanism.get("status") != "confirmed":
        reason = "mechanism_unconfirmed"
    elif unresolved_cases:
        reason = "mechanism_measurement_unresolved"
    elif not opportunity_cases:
        reason = "no_mechanism_opportunity"
    else:
        reason = "measurable"
    return {
        "schema_version": "evolution.mechanism_opportunity.v1",
        "metric_role": "promotion-authoritative",
        "mechanism_id": mechanism_id,
        "target_kind": objective,
        "target_cases": len(target_cases),
        "repairable_errors": len(opportunity_cases),
        "already_correct": len(correct_cases),
        "unresolved": len(unresolved_cases),
        "unconfirmed": len(unconfirmed_cases),
        "ineligible": len(ineligible_cases),
        "detection_repairable_errors": detection_errors,
        "localization_repairable_errors": localization_errors,
        "opportunity_cases": sorted(opportunity_cases),
        "already_correct_cases": sorted(correct_cases),
        "unresolved_cases": sorted(unresolved_cases),
        "unconfirmed_cases": sorted(unconfirmed_cases),
        "ineligible_cases": sorted(ineligible_cases),
        "evaluable": reason == "measurable",
        "reason_code": reason,
    }


def mechanism_row_healthy(row: Mapping[str, Any]) -> bool:
    """Strict health check for a new mechanism-bound measurement."""
    if row.get("authority") != "authoritative":
        return False
    if type(row.get("gold")) is not bool or type(row.get("prediction")) is not bool:
        return False
    result = row.get("result") or {}
    if result and result.get("runtime_valid") is not True:
        return False
    if result.get("decision_authority", "authoritative") != "authoritative":
        return False
    statuses = list((result.get("stage_status") or {}).values())
    for hearing in (result.get("proof") or {}).get("hearings") or ():
        statuses.extend(hearing.get(name) or {} for name in ("tracer_status", "defense_status"))
    return not any(
        str((status or {}).get("state") or "").startswith(
            ("provider_error", "invalid", "contract_error", "degraded", "no_quorum", "timeout")
        )
        for status in statuses
    )


def protection_repair(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Direction-agnostic corrections and regressions on the protection set.

    HARMLESSNESS protects only the cases the control arm already has right, so
    a candidate that *fixes* a control-arm error on the protection set earns
    nothing anywhere: M reads the benefit set, and the sentinel's protected
    list excludes baseline-incorrect rows. On the 2026-09-21 run this made a
    candidate that removed two false positives and lost one true positive read
    as a net ``-1`` when its true effect was "fixed two, broke one".

    This reading closes that hole by *recording* the repairs and the
    regressions separately, on the same set, with no weighting and no
    compensation: the two counts are reported side by side and never summed
    into a score. Plan §5.2 bans netting new errors against gains inside one
    weighted number; reporting both directions without combining them is the
    opposite of that, not an exception to it.

    ``repaired_*`` counts rows the control arm got wrong and the candidate got
    right, split by gold so an FN repair and an FP repair stay distinguishable.
    ``regressed_*`` counts the reverse and is the reading a future gate could
    charge, independently of any benefit-set gain.

    Both counts are over *decisive* flips: resolving a case the control arm left
    undecided is a calibration change, not a repair, and must not be reported as
    one (the same discipline ``gates._decisive_correction`` applies to boundary
    claims). Such rows are counted separately under ``unresolved_*``.
    """
    paired = confusion_rows(list(baseline_rows), list(active_rows))
    repaired_fn = repaired_fp = regressed_fn = regressed_fp = 0
    unresolved_added = unresolved_removed = 0
    rows: list[dict[str, Any]] = []
    for item in paired:
        case = str(item["case"])
        gold = item["gold"]
        base = item["baseline_prediction"]
        active = item["active_prediction"]
        base_correct = base is not None and base == gold
        active_correct = active is not None and active == gold
        if base is None and active is not None:
            unresolved_removed += 1
        elif base is not None and active is None:
            unresolved_added += 1
        decisive = base is not None and active is not None
        direction = "unchanged"
        if decisive and active_correct and not base_correct:
            direction = "repaired"
            if gold is True:
                repaired_fn += 1
            else:
                repaired_fp += 1
        elif decisive and base_correct and not active_correct:
            direction = "regressed"
            if gold is True:
                regressed_fn += 1
            else:
                regressed_fp += 1
        elif base is None and active is None:
            direction = "unresolved_both"
        if direction in {"repaired", "regressed"}:
            rows.append({
                "case": case,
                "gold": gold,
                "baseline_prediction": base,
                "active_prediction": active,
                "direction": direction,
            })
    return {
        "n": len(paired),
        "repaired": repaired_fn + repaired_fp,
        "repaired_fn": repaired_fn,
        "repaired_fp": repaired_fp,
        "regressed": regressed_fn + regressed_fp,
        "regressed_fn": regressed_fn,
        "regressed_fp": regressed_fp,
        "unresolved_added": unresolved_added,
        "unresolved_removed": unresolved_removed,
        "rows": rows,
        "note": (
            "repaired and regressed are recorded independently and are not "
            "combined into a score; a repair here cannot offset a regression "
            "and earns no admission credit"
        ),
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


def row_responsible_step(row: Mapping[str, Any] | None) -> int | None:
    """The responsible step a row asserts, from the row or its result."""
    if not row:
        return None
    value = row.get("responsible_step")
    if value is None:
        value = (row.get("result") or {}).get("responsible_step")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


STEP_KIND_CONTENT = "content_step"
STEP_KIND_ACTION = "action_step"
STEP_KIND_EMPTY = "empty_step"
STEP_KIND_UNCLASSIFIED = "unclassified"

STEP_KINDS = (
    STEP_KIND_CONTENT, STEP_KIND_ACTION, STEP_KIND_EMPTY, STEP_KIND_UNCLASSIFIED,
)


def row_responsible_step_kind(row: Mapping[str, Any] | None) -> str:
    """Whether a row's responsible step is a content step or an action step.

    Read, never guessed. The detector holds the packet at adjudication time and
    records ``responsible_step_kind`` on the envelope
    (``detector.packet.step_kind``: prose in the blamed event -> content step;
    only tool calls -> action step). A row carrying no such field -- an older
    archive, a synthetic test row, a third-party backend -- is reported as
    ``unclassified`` instead of being inferred from anything about the row's
    numbers. Inferring would place a fabricated decomposition next to a
    measured one, which is the failure this reading exists to prevent.
    """
    if not row:
        return STEP_KIND_UNCLASSIFIED
    value = row.get("responsible_step_kind")
    if value is None:
        result = row.get("result") or {}
        value = result.get("responsible_step_kind")
    if value is None:
        value = ((row.get("result") or {}).get("proof") or {}).get(
            "responsible_step_kind"
        )
    text = str(value or "")
    return text if text in STEP_KINDS else STEP_KIND_UNCLASSIFIED


def row_localization_conflicting(row: Mapping[str, Any] | None) -> bool:
    """Whether the row recorded a disagreement about its responsible step.

    False for a row with no conflict object, which is also what a row written
    before the field existed reports: that is absence of evidence, not a claim
    that the sources agreed.
    """
    if not row:
        return False
    value = row.get("localization_conflict")
    if value is None:
        value = ((row.get("result") or {}).get("proof") or {}).get(
            "localization_conflict"
        )
    return isinstance(value, Mapping) and bool(value.get("disagreements"))


def _step_kind_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    hit_of: Any,
) -> dict[str, Any]:
    """Per-step-kind hit counts beside one localization reading.

    ``hit_of`` maps a row to ``(scorable, hit)`` so the same decomposition can
    ride beside several readings without redefining any of them.

    The point of splitting: a hit rate over action steps and one over content
    steps answer different questions -- the first says "it found the right
    tool call", the second "it found the right sentence" -- and one averaged
    number hides which question an arm actually improved. The two are reported
    side by side and never combined into a score.
    """
    by_kind: dict[str, dict[str, int]] = {
        kind: {"scorable": 0, "hits": 0} for kind in STEP_KINDS
    }
    for row in rows:
        scorable, hit = hit_of(row)
        if not scorable:
            continue
        bucket = by_kind[row_responsible_step_kind(row)]
        bucket["scorable"] += 1
        bucket["hits"] += int(bool(hit))
    result: dict[str, Any] = {}
    for kind, bucket in by_kind.items():
        scorable, hits = bucket["scorable"], bucket["hits"]
        result[f"{kind}_scorable"] = scorable
        result[f"{kind}_hits"] = hits
        result[f"{kind}_accuracy"] = (hits / scorable) if scorable else None
    return result


def _step_kind_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    hit_of: Any,
) -> dict[str, Any]:
    """Deprecated shim: unused, kept out of ``__all__``.

    Superseded by ``_joint_kind_split``, which reads the per-arm kind recorded
    on the paired rows. Retained only so an in-flight caller that imported the
    private name is not broken by the change.
    """
    by_kind: dict[str, dict[str, int]] = {
        kind: {"scorable": 0, "hits": 0} for kind in STEP_KINDS
    }
    for row in rows:
        scorable, hit = hit_of(row)
        if not scorable:
            continue
        bucket = by_kind[row_responsible_step_kind(row)]
        bucket["scorable"] += 1
        bucket["hits"] += int(bool(hit))
    result: dict[str, Any] = {}
    for kind, bucket in by_kind.items():
        scorable, hits = bucket["scorable"], bucket["hits"]
        result[f"{kind}_scorable"] = scorable
        result[f"{kind}_hits"] = hits
        result[f"{kind}_accuracy"] = (hits / scorable) if scorable else None
    return result


def _gold_step(row: Mapping[str, Any], gold_steps: Mapping[str, Any]) -> int | None:
    case = str(row.get("case") or "")
    value = gold_steps.get(case)
    if value is None:
        value = gold_steps.get(case.replace("/", "__"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def paired_localization_delta(
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
    unstable_cases: Collection[str] | None = None,
) -> dict[str, Any]:
    """Joint detection-and-localization delta over a frozen case batch.

    The denominator is the frozen batch itself, not the set both arms managed
    to decide: a miss must not be able to leave the denominator, otherwise a
    candidate could look like it improved localization by detecting less. A
    case counts toward ``joint_*`` only when the arm both convicts
    authoritatively and names the annotated responsible step.

    ``joint_broken`` is the localization-preservation guard: cases the control
    arm already had right and the candidate arm no longer does.

    ``unstable_cases`` carries the same meaning as in ``paired_delta``: cases
    the control configuration read two ways when it was rolled twice. It adds
    ``joint_improved_on_unstable`` and ``joint_improved_on_stable`` so a
    localization gain that only exists on cases the instrument cannot read
    twice does not have to be counted as a gain. Nothing is subtracted.
    """
    gold_steps = dict(gold_steps or {})
    paired = confusion_rows(baseline_rows, active_rows)
    base_by_case = {str(row.get("case") or ""): row for row in baseline_rows}
    active_by_case = {str(row.get("case") or ""): row for row in active_rows}
    rows: list[dict[str, Any]] = []
    base_joint = active_joint = 0
    joint_improved = joint_broken = 0
    joint_improved_on_unstable = 0
    localizable = 0
    unstable_set = {str(case) for case in unstable_cases or ()}
    for item in paired:
        case = str(item["case"])
        gold_step = _gold_step(item, gold_steps)
        if item["gold"] is not True or gold_step is None:
            # Localization is scored on annotated gold-positive cases only;
            # such a case is out of the localization batch, not a silent zero.
            continue
        localizable += 1
        base_step = row_responsible_step(base_by_case.get(case))
        active_step = row_responsible_step(active_by_case.get(case))
        base_hit = (
            item["baseline_prediction"] is True and base_step == gold_step
        )
        active_hit = (
            item["active_prediction"] is True and active_step == gold_step
        )
        base_joint += int(base_hit)
        active_joint += int(active_hit)
        if active_hit and not base_hit:
            joint_improved += 1
            if unstable_cases is not None and case in unstable_set:
                joint_improved_on_unstable += 1
        elif base_hit and not active_hit:
            joint_broken += 1
        rows.append({
            "case": case,
            "gold_step": gold_step,
            "baseline_prediction": item["baseline_prediction"],
            "active_prediction": item["active_prediction"],
            "baseline_step": base_step,
            "active_step": active_step,
            # Kind and conflict ride along per arm so the decomposition below
            # classifies each arm by the step *that arm* blamed. Both are read
            # off the envelope the arm wrote, never re-derived here.
            "baseline_step_kind": row_responsible_step_kind(
                base_by_case.get(case)
            ),
            "active_step_kind": row_responsible_step_kind(
                active_by_case.get(case)
            ),
            "localization_conflict": (
                row_localization_conflicting(base_by_case.get(case))
                or row_localization_conflicting(active_by_case.get(case))
            ),
            "baseline_joint": base_hit,
            "active_joint": active_hit,
        })
    return {
        "n": len(paired),
        "localizable_n": localizable,
        "base_joint": base_joint,
        "active_joint": active_joint,
        "joint_delta": active_joint - base_joint,
        "joint_improved": joint_improved,
        "joint_broken": joint_broken,
        "stability_applicable": unstable_cases is not None,
        "joint_improved_on_unstable": joint_improved_on_unstable,
        "joint_improved_on_stable": joint_improved - joint_improved_on_unstable,
        "rows": rows,
        # Decomposition only. Every count above keeps its exact previous
        # meaning; these two blocks are a second view of the same hits, split
        # by whether the arm's adopted step was an action step or a content
        # step. Nothing here feeds a threshold, and the two are never summed.
        "baseline_step_kinds": _joint_kind_split(rows, side="baseline"),
        "active_step_kinds": _joint_kind_split(rows, side="active"),
        "localization_conflict_rows": sum(
            1 for row in rows if row["localization_conflict"]
        ),
    }


def _joint_kind_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    side: str,
) -> dict[str, Any]:
    """Per-step-kind joint hits for one arm of a paired localization delta.

    The joint reading credits a case only when the arm convicts authoritatively
    *and* names the annotated step. This splits those credits by the kind of
    step the arm named, over the same scorable set, so a reader can see whether
    an arm's joint hits come from action steps or from content steps. The
    totals above stay the only scored quantity; these are the same hits,
    sorted, and are never combined into one number.
    """
    by_kind: dict[str, dict[str, int]] = {
        kind: {"scorable": 0, "hits": 0} for kind in STEP_KINDS
    }
    prefix = "baseline" if side == "baseline" else "active"
    step_key = f"{prefix}_step"
    pred_key = f"{prefix}_prediction"
    for row in rows:
        # A conflicted localization is excluded rather than counted either
        # way: the row reported two different steps, so scoring the adopted
        # one as a hit or as a miss would both be a claim the row cannot
        # support. Those rows are counted by ``localization_conflict_rows``.
        if row.get("localization_conflict"):
            continue
        bucket_kind = row.get(f"{step_key}_kind")
        if bucket_kind not in STEP_KINDS:
            bucket_kind = STEP_KIND_UNCLASSIFIED
        bucket = by_kind[bucket_kind]
        bucket["scorable"] += 1
        bucket["hits"] += int(
            row.get(pred_key) is True
            and row.get(step_key) == row.get("gold_step")
        )
    result: dict[str, Any] = {}
    for kind, bucket in by_kind.items():
        scorable, hits = bucket["scorable"], bucket["hits"]
        result[f"{kind}_scorable"] = scorable
        result[f"{kind}_hits"] = hits
        result[f"{kind}_accuracy"] = (hits / scorable) if scorable else None
    return result


def localization_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Standalone localization read for one arm (reporting, not gating).

    The five headline numbers keep their exact previous definitions. Beside
    them, ``step_kinds`` decomposes the same ``joint_hits`` by whether the step
    this arm blamed was an action step or a content step, and
    ``localization_conflict_rows`` counts the rows whose adopted step carried a
    recorded disagreement -- those rows are excluded from the decomposition
    rather than scored under a step the row itself contradicts.
    """
    gold_steps = dict(gold_steps or {})
    annotated = hits = detected = 0
    by_kind: dict[str, dict[str, int]] = {
        kind: {"scorable": 0, "hits": 0} for kind in STEP_KINDS
    }
    conflicted = 0
    for row in rows:
        gold_step = _gold_step(row, gold_steps)
        if row.get("gold") is not True or gold_step is None:
            continue
        annotated += 1
        predicted = row.get("prediction") is True
        hit = predicted and row_responsible_step(row) == gold_step
        if predicted:
            detected += 1
            if hit:
                hits += 1
        if row_localization_conflicting(row):
            conflicted += 1
            continue
        bucket = by_kind[row_responsible_step_kind(row)]
        bucket["scorable"] += 1
        bucket["hits"] += int(bool(hit))
    step_kinds: dict[str, Any] = {}
    for kind, bucket in by_kind.items():
        scorable, kind_hits = bucket["scorable"], bucket["hits"]
        step_kinds[f"{kind}_scorable"] = scorable
        step_kinds[f"{kind}_hits"] = kind_hits
        step_kinds[f"{kind}_accuracy"] = (
            (kind_hits / scorable) if scorable else None
        )
    return {
        "annotated_positive": annotated,
        "authoritative_detected": detected,
        "joint_hits": hits,
        "joint_accuracy": (hits / annotated) if annotated else None,
        "step_accuracy_on_detected": (
            (hits / detected) if detected else None
        ),
        "step_kinds": step_kinds,
        "localization_conflict_rows": conflicted,
    }



BEHAVIOR_EVENT_KINDS = {
    "evidence_check": "evidence_increment",
    "text_boundary": "text_injected",
    "recall_trigger": "recall_triggered",
    "decision_guard": "guard_fired",
}


def deployment_events(
    rows: Sequence[Mapping[str, Any]],
    intervention_id: str,
    intervention_type: str = "",
) -> tuple[int, str]:
    """How many times an intervention actually reached the runtime.

    Returns ``(events, kind)``: the count of the deployment trace entries that
    show this intervention's own effect (``text_injected``, ``guard_fired``,
    ``recall_triggered``), and the machine-readable kind name.

    The detection BEHAVIOR gate computes this per intervention type and reports
    it, because route-insufficiency evidence is a claim about numbers -- "the
    hook fired and nothing moved". The localization gate reads the same
    quantity through this helper, so a localization claim can be recorded as an
    insufficient route too; without it, every localization failure was
    invisible to that store and the next round re-proposed the same route.
    """
    kind = BEHAVIOR_EVENT_KINDS.get(intervention_type, "")
    if not kind:
        return 0, ""
    count = 0
    for row in rows:
        deployment = ((row.get("result") or {}).get("proof") or {}).get(
            "deployment"
        ) or {}
        count += sum(
            1 for item in deployment.get("fired") or ()
            if str(item.get("intervention_id") or "") == intervention_id
            and item.get("event") == kind
        )
    return count, kind


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
    "EvaluationState",
    "binary_metrics",
    "classify_transition",
    "confusion_rows",
    "evaluate_row_state",
    "evidence_increment_rows",
    "fired_rate",
    "flipped_cases",
    "gain_opportunity",
    "localization_metrics",
    "localization_opportunity",
    "mechanism_opportunity",
    "mechanism_row_healthy",
    "noise_floor",
    "noise_floor_flipped_cases",
    "paired_delta",
    "paired_localization_delta",
    "paired_state_transitions_v1",
    "protection_repair",
    "repeated_roll_disagreement",
    "resolution_opportunity",
    "row_responsible_step",
    "source_deltas",
    "source_of",
]
