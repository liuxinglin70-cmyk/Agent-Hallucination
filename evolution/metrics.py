"""Gold-side paired metrics for the evolution gates.

LLM never scores a candidate. Every number in HARMLESSNESS/BEHAVIOR/M comes from the
external gold labels joined to the two detection arms.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


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
    valid = bool(mechanism_id) and objective in {"detection", "localization", "joint"}
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
) -> dict[str, Any]:
    """Joint detection-and-localization delta over a frozen case batch.

    The denominator is the frozen batch itself, not the set both arms managed
    to decide: a miss must not be able to leave the denominator, otherwise a
    candidate could look like it improved localization by detecting less. A
    case counts toward ``joint_*`` only when the arm both convicts
    authoritatively and names the annotated responsible step.

    ``joint_broken`` is the localization-preservation guard: cases the control
    arm already had right and the candidate arm no longer does.
    """
    gold_steps = dict(gold_steps or {})
    paired = confusion_rows(baseline_rows, active_rows)
    base_by_case = {str(row.get("case") or ""): row for row in baseline_rows}
    active_by_case = {str(row.get("case") or ""): row for row in active_rows}
    rows: list[dict[str, Any]] = []
    base_joint = active_joint = 0
    joint_improved = joint_broken = 0
    localizable = 0
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
        elif base_hit and not active_hit:
            joint_broken += 1
        rows.append({
            "case": case,
            "gold_step": gold_step,
            "baseline_prediction": item["baseline_prediction"],
            "active_prediction": item["active_prediction"],
            "baseline_step": base_step,
            "active_step": active_step,
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
        "rows": rows,
    }


def localization_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Standalone localization read for one arm (reporting, not gating)."""
    gold_steps = dict(gold_steps or {})
    annotated = hits = detected = 0
    for row in rows:
        gold_step = _gold_step(row, gold_steps)
        if row.get("gold") is not True or gold_step is None:
            continue
        annotated += 1
        if row.get("prediction") is True:
            detected += 1
            if row_responsible_step(row) == gold_step:
                hits += 1
    return {
        "annotated_positive": annotated,
        "authoritative_detected": detected,
        "joint_hits": hits,
        "joint_accuracy": (hits / annotated) if annotated else None,
        "step_accuracy_on_detected": (
            (hits / detected) if detected else None
        ),
    }



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
    "gain_opportunity",
    "localization_metrics",
    "localization_opportunity",
    "paired_delta",
    "paired_localization_delta",
    "protection_repair",
    "row_responsible_step",
    "source_deltas",
    "source_of",
]
