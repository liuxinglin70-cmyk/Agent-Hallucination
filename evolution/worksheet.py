"""Mechanical failure attribution (worksheet).

This is the only place case ids and mechanism statistics meet. Candidate
provenance may carry a failure family and its case ids, but no case id may
appear in directive/body/claim prose (enforced by STRUCTURAL).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from detector.dataset import load_case
from detector.predicate_context import judge_predicate_context

from evolution.metrics import source_of, binary_metrics
from evolution.causal_failures import (
    build_causal_failure_record, causal_failure_prompt_view,
    shared_action_rule_conditions, matching_action_normal_neighbors,
)
from evolution.mechanisms import (
    infer_mechanism,
    member_reading,
    reading_signature,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _tokens(value: Any) -> list[str]:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True,
    )
    return re.findall(r"[a-z0-9_]{2,}", str(text).lower())


def _bigrams(tokens: Sequence[str]) -> set[str]:
    return {
        f"{tokens[index]} {tokens[index + 1]}"
        for index in range(len(tokens) - 1)
    }


def _shared_literals(
    profiles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Literal candidates shared by every member's typed field profile.

    Proposer-facing only: these values are drawn from the same typed views
    the DSL interpreter can match, so a candidate rule written from them is
    executable without guessing case text.
    """
    if not profiles:
        return {}
    scalar_token_fields = (
        "question_text",
        "last_prose_text",
        "context_judge_hallucination_reason",
        "context_judge_delivered_result",
    )
    scalar_tokens: dict[str, list[str]] = {}
    for field in scalar_token_fields:
        shared = set(_tokens(profiles[0].get(field)))
        for profile in profiles[1:]:
            shared &= set(_tokens(profile.get(field)))
        scalar_tokens[field] = sorted(shared)[:40]
    question_bigrams: set[str] = set()
    for index, profile in enumerate(profiles):
        grams = _bigrams(_tokens(profile.get("question_text")))
        question_bigrams = grams if index == 0 else question_bigrams & grams
    list_fields = (
        "tool_names",
        "tool_arguments",
        "tool_observations",
        "context_contract_kinds",
        "context_contract_requirements",
        "packet_features",
    )
    list_items: dict[str, list[str]] = {}
    for field in list_fields:
        shared = {
            str(item).lower().strip()
            for item in profiles[0].get(field) or ()
            if str(item).strip()
        }
        for profile in profiles[1:]:
            current = {
                str(item).lower().strip()
                for item in profile.get(field) or ()
                if str(item).strip()
            }
            shared &= current
        list_items[field] = sorted(shared)[:40]
    return {
        "scalar_token_intersection": scalar_tokens,
        "question_bigram_intersection": sorted(question_bigrams)[:20],
        "list_item_intersection": list_items,
    }


def _judge_parsed(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return (result.get("proof") or {}).get("judge") or {}


def _coverage(result: Mapping[str, Any]) -> dict[str, Any] | None:
    proof = result.get("proof") or {}
    coverage = proof.get("causal_upgrade_coverage")
    if isinstance(coverage, dict):
        return coverage
    rows = proof.get("causal_upgrade_rows")
    if isinstance(rows, list) and rows:
        return {
            "expected": len(rows),
            "answered": sum(
                1 for row in rows if row.get("coverage") == "answered"
            ),
            "missing": [
                row.get("candidate_id") for row in rows
                if row.get("coverage") == "missing"
            ],
        }
    return None


def _coarse_failure_family(row: Mapping[str, Any]) -> str:
    """Outcome-path family used as a readable mechanism-signature prefix."""
    gold = row.get("gold")
    prediction = row.get("prediction")
    result = row.get("result") or {}
    proof = result.get("proof") or {}
    if prediction is None:
        reasons = result.get("authority_failure_reasons") or []
        code = (
            str(reasons[0].get("component") or "unknown")
            if reasons else "unknown"
        )
        return f"FAM-unresolved-{_sha(code)}"
    if gold is True and prediction is False:
        judge = _judge_parsed(result)
        coverage = _coverage(result)
        if coverage and coverage.get("missing"):
            return "FAM-fn-upgrade_missing_rows"
        defense = proof.get("defense") or {}
        if bool(judge.get("is_hallucination")) and defense.get("verdict") == "refuted":
            return "FAM-fn-defense_refuted_judge"
        if (
            proof.get("causal_upgrade")
            and defense.get("verdict") == "refuted"
        ):
            return "FAM-fn-defense_refuted_recall_upgrade"
        recall_candidates = proof.get("recall_candidates")
        if not recall_candidates:
            return "FAM-fn-judge_false_no_recall_candidates"
        if not proof.get("causal_upgrade"):
            return "FAM-fn-judge_false_upgrade_no_upgrade"
        return f"FAM-fn-{_sha(str(judge.get('confidence') or 'none'))}"
    if gold is False and prediction is True:
        source = (proof.get("terminal_basis") or {}).get(
            "decisive_facts", {}
        ).get("positive_source")
        defense = (proof.get("defense") or {})
        verdict = defense.get("verdict")
        if source == "recall_upgrade":
            return "FAM-fp-recall_upgrade_conviction"
        if source == "judge" and verdict != "substantiated":
            return f"FAM-fp-judge_conviction_defense_{verdict or 'none'}"
        return "FAM-fp-judge_conviction"
    return "FAM-none"


def _mechanism_signature(row: Mapping[str, Any]) -> dict[str, Any]:
    """Label-free execution signature used to keep unlike failures apart.

    The signature keys the failure path (which stage produced the verdict),
    never task-level contract kinds: the same mechanism can appear across
    different task contracts, and contract heterogeneity belongs to member
    summaries and the STRUCTURAL unanimity rule, not to family identity.
    """
    result = row.get("result") or {}
    proof = result.get("proof") or {}
    judge = _judge_parsed(result)
    statuses = result.get("stage_status") or {}
    return {
        "judge_positive": bool(judge.get("is_hallucination")),
        "stages": {
            name: str((statuses.get(name) or {}).get("state") or "not_run")
            for name in (
                "judge", "recall_scanner", "causal_upgrade",
                "step_tracer", "defense",
            )
        },
        "recall_path": (
            "positive" if proof.get("causal_upgrade")
            else "candidates" if proof.get("recall_candidates")
            else str((statuses.get("recall_scanner") or {}).get("state") or "not_run")
        ),
        "upgrade_positive": bool(proof.get("causal_upgrade")),
        "defense_verdict": str((proof.get("defense") or {}).get("verdict") or "none"),
        "positive_source": str(
            ((proof.get("terminal_basis") or {}).get("decisive_facts") or {}).get(
                "positive_source"
            ) or "none"
        ),
    }


def _contract_kinds(row: Mapping[str, Any]) -> list[str]:
    judge = _judge_parsed(row.get("result") or {})
    return sorted({
        str(item.get("kind") or "")
        for item in judge.get("contracts") or ()
        if isinstance(item, Mapping) and str(item.get("kind") or "")
    })


def failure_family(row: Mapping[str, Any]) -> str:
    """Mechanism family id; never case-id or source conditioned."""
    coarse = _coarse_failure_family(row)
    if coarse in {"FAM-none"} or coarse.startswith("FAM-unresolved-"):
        return coarse
    signature = json.dumps(
        _mechanism_signature(row), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return f"{coarse}-{_sha(signature)}"


def stable_failure_families(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_cases: int = 2,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        gold = row.get("gold")
        if gold is not True and gold is not False:
            continue
        if str(row.get("authority") or "") != "authoritative":
            continue
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            continue
        statuses = result.get("stage_status") or {}
        if any(
            str((status or {}).get("state") or "").startswith(
                ("provider_error", "invalid", "contract_error", "degraded")
            )
            for status in statuses.values()
        ):
            continue
        if row.get("prediction") == gold:
            continue
        family = failure_family(row)
        groups.setdefault(family, []).append(row)
    families = []
    for family, members in sorted(groups.items()):
        if len(members) < min_cases:
            continue
        families.append({
            "failure_family": family,
            "count": len(members),
            "failure_cases": [str(row.get("case") or "") for row in members],
            "sources": sorted({
                source_of(str(row.get("case") or "")) for row in members
            }),
            "mechanism_summary": _mechanism_summary(family, members),
            "mechanism_signature": _mechanism_signature(members[0]),
        })
    families.sort(key=lambda item: (-item["count"], item["failure_family"]))
    return families


def localization_state(
    row: Mapping[str, Any],
    gold_steps: Mapping[str, Any] | None = None,
) -> str:
    """Where one evaluated row stands on the localization objective.

    A localization failure is not a detection failure: the row convicted the
    case correctly and named the wrong step. ``stable_failure_families`` skips
    such rows by construction (it keeps only ``prediction != gold``), so
    without this reading the objective has no path into the development loop.

    ``gold_steps`` is the evaluator-side annotation. Using it here is the
    sanctioned development-side use of gold (plan §3.2); the value itself
    never reaches the detector or the proposer.
    """
    from evolution.metrics import row_responsible_step

    gold = row.get("gold")
    if gold is not True:
        return "not_positive"
    if row.get("prediction") is not True:
        return "not_convicted"
    gold_step = (gold_steps or {}).get(str(row.get("case") or ""))
    if gold_step is None:
        gold_step = (gold_steps or {}).get(
            str(row.get("case") or "").replace("/", "__")
        )
    if gold_step is None:
        return "unannotated"
    try:
        gold_step = int(gold_step)
    except (TypeError, ValueError):
        return "unannotated"
    step = row_responsible_step(row)
    if step is None:
        # A conviction with no step is only a localization failure if the
        # tracer stage actually ran and claimed success. Without a recorded
        # tracer stage there is no evidence the stage was reached at all --
        # a synthetic backend, or a result written before the stage existed,
        # would otherwise be reported as "the tracer named nothing", which is
        # a claim about a run that may never have happened.
        result = row.get("result") or {}
        tracer_state = str(
            ((result.get("stage_status") or {}).get("step_tracer") or {}).get(
                "state"
            ) or ""
        )
        if tracer_state != "ok":
            return "unmeasured"
        return "step_missing"
    return "hit" if step == gold_step else "mislocated"


def step_can_host_text_error(
    record: Mapping[str, Any], step: int,
) -> tuple[bool, str]:
    """Whether a step carries any text a content deviation could originate in.

    A deviation needs something to deviate *from*: assistant prose, or a tool
    argument with a string value. ``ls {"a": true}`` carries neither, so it
    cannot be the origin of a content error no matter what the annotation says.
    This is the label-free, mechanism-general half of the localization
    viability check.
    """
    events = list(record.get("history") or ())
    if not (1 <= int(step) <= len(events)):
        return False, f"step {step} is outside the trajectory"
    event = events[int(step) - 1]
    if not isinstance(event, Mapping):
        return False, f"step {step} is not an event"
    if str(event.get("role") or "") != "assistant":
        return (
            False,
            f"step {step} is a {event.get('role')!r} event; an error cannot "
            "originate in an observation",
        )
    if str(event.get("content") or "").strip():
        return True, ""
    for call in event.get("tool_calls") or ():
        if not isinstance(call, Mapping):
            continue
        arguments = call.get("arguments")
        if isinstance(arguments, Mapping) and any(
            isinstance(value, str) and value.strip()
            for value in arguments.values()
        ):
            return True, ""
        if isinstance(arguments, str) and arguments.strip():
            return True, ""
    return (
        False,
        f"step {step} carries no text: no assistant content and no string "
        "tool argument, so it cannot host a content deviation",
    )


# How a mislocated row can be treated by the development loop. Only
# ``structurally_impossible`` is a defect the framework may auto-repair: the
# others need a human to decide which of several real errors the task is about.
LOCALIZATION_VIABILITY = {
    "structurally_impossible": (
        "the named step carries no text at all, so the localization is wrong "
        "regardless of the annotation; usable as a repair task"
    ),
    "needs_review": (
        "the named step carries its own text, so naming it may be a defensible "
        "answer to a different real error; not usable as a repair task until "
        "reviewed"
    ),
}


def localization_repair_viability(
    row: Mapping[str, Any],
    record: Mapping[str, Any] | None = None,
    *,
    gold_steps: Mapping[str, Any] | None = None,
    causal_policy: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Admit structural impossibility or a recomputed same-claim repair.

    A different annotation alone cannot invalidate a hostable named step.
    Scoped arithmetic evidence must identify the annotated position as the
    first declared result and show that symbolic repair reaches delivery.
    Multiple substantiated claims and invalid measurements remain reviewable.
    """
    from evolution.metrics import row_responsible_step

    step = row_responsible_step(row)
    if step is None:
        return "needs_review", "no responsible step to review"
    if record is None:
        return "needs_review", "record not available; viability not checked"
    failure = build_causal_failure_record(row, record, policy=causal_policy)
    if failure["evidence_state"] in {"invalid", "ambiguous", "unmeasurable"}:
        return "needs_review", "; ".join(failure["reasons"])
    hostable, reason = step_can_host_text_error(record, step)
    if not hostable:
        return "structurally_impossible", reason
    gold_step = None
    if gold_steps:
        gold_step = (gold_steps or {}).get(str(row.get("case") or ""))
        if gold_step is None:
            gold_step = (gold_steps or {}).get(
                str(row.get("case") or "").replace("/", "__")
            )
    if gold_step is not None:
        try:
            gold_step = int(gold_step)
        except (TypeError, ValueError):
            gold_step = None
    if (failure["evidence_state"] == "verified" and failure["classification"] in {"late", "early"}
            and failure["first_position"] == gold_step):
        return "verified_same_claim", "recomputed single-task declaration and symbolic repair support the annotated origin"
    if gold_step is not None and step > gold_step:
        return (
            "needs_review",
            f"named step {step} is later than the annotated step {gold_step} "
            "and carries its own text; the annotation chose between two real "
            "errors",
        )
    return (
        "needs_review",
        f"named step {step} carries its own text; treating it as wrong would "
        "require abandoning a possibly true detection",
    )


def localization_repair_viable(
    row: Mapping[str, Any],
    record: Mapping[str, Any] | None = None,
    *,
    gold_steps: Mapping[str, Any] | None = None,
    causal_policy: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """``localization_repair_viability`` reduced to a yes/no plus its reason."""
    classification, reason = localization_repair_viability(
        row, record, gold_steps=gold_steps, causal_policy=causal_policy,
    )
    return classification in {"structurally_impossible", "verified_same_claim"}, reason


def stable_localization_families(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
    min_cases: int = 2,
    records: Mapping[str, Any] | None = None,
    require_viable: bool = True,
    causal_policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Families of cases the detector convicts correctly but localizes wrongly.

    Same admission rules as ``stable_failure_families`` (boolean gold,
    authoritative, runtime-valid, no infrastructure-degraded stage), because a
    mislocation counted during an outage is not a mechanism. Family ids are
    ``FAM-loc-*`` and carry the same label-free execution signature, so the
    mechanism that produced the wrong step stays the distinguishing axis and
    no case id or source enters the identity.
    """
    from evolution.metrics import row_responsible_step

    groups: dict[str, list[Mapping[str, Any]]] = {}
    annotation_choices: list[dict[str, Any]] = []
    for row in rows:
        gold = row.get("gold")
        if gold is not True:
            continue
        if str(row.get("authority") or "") != "authoritative":
            continue
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            continue
        statuses = result.get("stage_status") or {}
        if any(
            str((status or {}).get("state") or "").startswith(
                ("provider_error", "invalid", "contract_error", "degraded")
            )
            for status in statuses.values()
        ):
            continue
        state = localization_state(row, gold_steps)
        if state not in {"mislocated", "step_missing"}:
            continue
        if require_viable:
            viable, reason = localization_repair_viable(
                row,
                (records or {}).get(str(row.get("case") or "")),
                gold_steps=gold_steps,
                causal_policy=causal_policy,
            )
            if not viable:
                annotation_choices.append({
                    "case": str(row.get("case") or ""),
                    "responsible_step": row_responsible_step(row),
                    "reason": reason,
                })
                continue
        family = localization_family(row, state=state)
        groups.setdefault(family, []).append(row)
    families = []
    for family, members in sorted(groups.items()):
        if len(members) < min_cases:
            continue
        families.append({
            "failure_family": family,
            "target_kind": "localization",
            "count": len(members),
            "failure_cases": [str(row.get("case") or "") for row in members],
            "sources": sorted({
                source_of(str(row.get("case") or "")) for row in members
            }),
            "mechanism_summary": _mechanism_summary(family, members),
            "mechanism_signature": _mechanism_signature(members[0]),
            "localization_states": [
                localization_state(row, gold_steps) for row in members
            ],
        })
    families.sort(key=lambda item: (-item["count"], item["failure_family"]))
    return families


def localization_annotation_choices(
    rows: Sequence[Mapping[str, Any]],
    *,
    gold_steps: Mapping[str, Any] | None = None,
    records: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rows excluded from localization tasks because the annotation is ambiguous.

    Reported separately so the exclusion is visible: these are cases where the
    detector named a step that differs from the annotation but cannot be called
    a defect without deciding which of two real errors the task is about.
    Dropping them silently would make the localization opportunity look larger
    than the repairable set.
    """
    from evolution.metrics import row_responsible_step

    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("gold") is not True:
            continue
        if localization_state(row, gold_steps) not in {"mislocated", "step_missing"}:
            continue
        viable, reason = localization_repair_viable(
            row,
            (records or {}).get(str(row.get("case") or "")),
            gold_steps=gold_steps,
        )
        if not viable:
            out.append({
                "case": str(row.get("case") or ""),
                "responsible_step": row_responsible_step(row),
                "reason": reason,
            })
    return out


def localization_family(
    row: Mapping[str, Any],
    *,
    gold_steps: Mapping[str, Any] | None = None,
    state: str | None = None,
) -> str:
    """Mechanism family id for a localization failure.

    ``FAM-loc-`` separates the two ways a step can be wrong: the tracer named
    a step at all (``mislocated``) versus named none (``step_missing``). They
    need different treatments -- a wrong step is a reasoning error, a missing
    step is an output-contract error -- so they must not share a family.

    Only those two states are localization failures. Anything else raises
    instead of minting a family: deriving the state without the annotation
    would otherwise yield ``FAM-loc-unannotated-*``, a family whose members
    were never shown to be mislocated.
    """
    state = state or localization_state(row, gold_steps)
    if state not in {"mislocated", "step_missing"}:
        raise ValueError(
            f"not a localization failure (state {state!r}); localization "
            "families exist only for convicted-correct rows whose responsible "
            "step is wrong or absent"
        )
    signature = json.dumps(
        _mechanism_signature(row), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return f"FAM-loc-{state}-{_sha(signature)}"


def _mechanism_summary(
    family: str,
    members: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {"family": family, "n": len(members)}
    if family.startswith("FAM-fn-upgrade_missing_rows"):
        coverages = []
        for row in members:
            coverage = _coverage(row.get("result") or {})
            if coverage:
                coverages.append(coverage)
        if coverages:
            summary["mean_missing_rows"] = (
                sum(len(c.get("missing") or []) for c in coverages)
                / len(coverages)
            )
    if family.startswith("FAM-fn-judge_false"):
        judge_flags = [
            bool(_judge_parsed(row.get("result") or {}).get("is_hallucination"))
            for row in members
        ]
        summary["judge_false_fraction"] = (
            1.0 - sum(judge_flags) / len(judge_flags)
        )
    return summary


def worksheet_report(
    patrol_rows: Sequence[Mapping[str, Any]],
    families: Sequence[Mapping[str, Any]],
    *,
    unrepresented: Sequence[Mapping[str, Any]] = (),
    excluded: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One patrol's readable summary.

    ``families`` may hold both detection and localization mechanisms. They are
    reported under separate keys because they are scored by different gates and
    satisfied by different evidence; keeping them in one list under the
    detection name made a localization family look like a detection family in
    every downstream report.

    ``unrepresented`` carries mechanism readings too rare to propose on (see
    ``mechanism_subgroups``); they appear under ``mechanisms`` so an excluded
    reading is reported rather than silently missing.
    """
    errors = [row for row in patrol_rows if row.get("prediction") != row.get("gold")]
    metrics = binary_metrics(patrol_rows)
    detection = [
        f for f in families
        if str(f.get("target_kind") or "detection") == "detection"
    ]
    localization = [
        f for f in families if str(f.get("target_kind") or "") == "localization"
    ]
    return {
        "patrol_size": len(patrol_rows),
        "errors": len(errors),
        "fn": metrics["fn"],
        "fp": metrics["fp"],
        "tp": metrics["tp"],
        "tn": metrics["tn"],
        "unresolved": metrics["unresolved"],
        "metrics": metrics,
        "family_evidence": "cross-case co-occurrence; repeated-run stability not measured",
        # Kept for compatibility with reports written before the split; it is
        # now the detection families only.
        "stable_failure_families": detection,
        "detection_families": detection,
        "localization_families": localization,
        "mechanisms": mechanism_report(families, unrepresented, excluded),
    }


_VIOLATION_VERDICTS = {
    "violated", "unresolved", "uncovered", "inconsistent", "missing",
}

ACTIONABILITY = {
    "actionable": (
        "at least one action or capability-creation route exists for this "
        "family's mechanism"
    ),
    "capability_gap": (
        "no action or capability-creation route can treat this mechanism; "
        "the action space does not intersect the diagnosis"
    ),
}

# Treatment routes: route name -> the hook/action that implements it.
# ``evolved_predicate`` is the capability-creation route: the proposer may
# define a new typed-DSL predicate program inline and pair it with the same
# evidence + conviction unit as the closed mechanical route.
TREATMENT_ROUTES = {
    "judge_directive": "judge.system text boundary that changes the preliminary judgment",
    "recall_directive": "scanner.system text boundary widens recall",
    "upgrade_directive": "upgrade.pre/system intervention reshapes upgrades",
    "restore_guard": "adjudicate.guard restore_judge_conviction",
    "withhold_guard": "adjudicate.guard withhold_conviction",
    "mechanical_conviction": (
        "evidence_check + convict_mechanical_violation on a unanimously "
        "violating conviction-authorized predicate"
    ),
    "evolved_predicate": (
        "define a new typed-DSL predicate program inline, then pair its "
        "evidence_check with convict_mechanical_violation"
    ),
    "recall_trigger": (
        "define a recall_trigger intervention (typed-DSL or built-in) that "
        "forces the recall net for judge-negative cases matching the family"
    ),
    "tracer_directive": (
        "tracer.system text boundary that changes which step is named as the "
        "root cause, leaving the conviction itself alone"
    ),
}


def classify_family_actionability(
    family: Mapping[str, Any],
) -> dict[str, Any]:
    """Enumerate the action routes available for one family.

    Routes are derived mechanically from the failure mechanism: which stage
    produced the wrong outcome decides which hooks could legally change it.
    ``mechanical_conviction`` additionally requires a conviction-authorized
    predicate unanimously violated across the family (the STRUCTURAL gate demand, so
    an invalid conviction draft never reaches the provider budget). For FN
    families without a built-in mechanical route, ``evolved_predicate`` opens
    the capability-creation route: the proposer may define a new typed-DSL
    predicate program inline and the STRUCTURAL gate executes it on every family
    member before any provider budget is spent. A family with no route is a
    capability gap — a measured property of the action space, reported
    instead of silently producing an empty proposal batch.
    """
    from detector.predicates import PREDICATE_CONVICTION_VERDICTS

    family_id = str(family.get("failure_family") or "")
    signature = family.get("mechanism_signature") or {}
    diagnostics = family.get("diagnostics") or {}
    signals = diagnostics.get("predicate_signals") or {}
    count = int(family.get("count") or 0)
    routes: list[str] = []
    # The claim direction a family's candidates declare. Read by the
    # development path's experience records and by memory recall, so it is
    # part of this reading rather than something each consumer re-derives:
    # two derivations of the same claim can drift, and one of them silently
    # produced an empty polarity on every development episode.
    polarity = (
        "recall" if family_id.startswith("FAM-fn-")
        else "precision" if family_id.startswith("FAM-fp-")
        else "boundary" if family_id.startswith("FAM-loc-")
        else ""
    )

    if family_id.startswith("FAM-unresolved-"):
        return {
            "classification": "capability_gap",
            "polarity": "",
            "reason": (
                "unresolved families need provider reliability, not an "
                "intervention action"
            ),
            "routes": [],
        }

    if family_id.startswith("FAM-fn-"):
        routes.append("judge_directive")
        defense_verdict = str(signature.get("defense_verdict") or "none")
        recall_path = str(signature.get("recall_path") or "")
        if defense_verdict == "refuted":
            routes.append("restore_guard")
        if recall_path in {"not_run", "not_needed"}:
            routes.append("recall_trigger")
        if recall_path in {"not_run", "not_needed", "ok"} or not (
            signature.get("upgrade_positive")
        ):
            routes.append("recall_directive")
            routes.append("upgrade_directive")
        for predicate, authorized in sorted(
            PREDICATE_CONVICTION_VERDICTS.items()
        ):
            signal = signals.get(predicate) or {}
            if (
                count >= 2
                and authorized
                and int(signal.get("applicable") or 0) == count
                and int(signal.get("violations") or 0) == count
            ):
                routes.append("mechanical_conviction")
                break
        if "mechanical_conviction" not in routes:
            # The capability-creation route is structurally open for every
            # stable FN family: the proposer may define a new typed-DSL
            # observation instead of being limited to the built-ins.
            routes.append("evolved_predicate")
    elif family_id.startswith("FAM-fp-"):
        positive_source = str(signature.get("positive_source") or "none")
        if positive_source in {"judge", "recall_upgrade"}:
            routes.append("withhold_guard")
    elif family_id.startswith("FAM-loc-"):
        # The conviction is already right; only the named step is wrong, so the
        # tracer is the stage that must move. When the step came from an
        # upgrade candidate rather than the tracer, the upgrade route is the
        # one that can change it, and both stay open so the proposer can pick.
        routes.append("tracer_directive")
        if bool(signature.get("upgrade_positive")):
            routes.append("upgrade_directive")

    if not routes:
        return {
            "classification": "capability_gap",
            "polarity": polarity,
            "reason": (
                f"family kind outside the action vocabulary: {family_id!r}"
            ),
            "routes": [],
        }
    return {
        "classification": "actionable",
        "polarity": polarity,
        "reason": ACTIONABILITY["actionable"],
        "routes": routes,
    }


def diagnose_failure_families(
    families: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | None = None,
    causal_policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ground candidate templates in mechanical predicate evidence.

    This is a proposer-side quality improvement, not a gate relaxation: a
    family gets a ``predicate_signals`` table computed from the bare-base
    failure packets. Templates prefer predicates that actually fire on the
    family. No case id ever enters a candidate's prose.
    """
    from detector.packet import build_packet
    from detector.predicate_dsl import typed_field_profile
    from detector.predicates import run_predicate

    row_by_case = {str(row.get("case") or ""): row for row in rows}
    diagnosed: list[dict[str, Any]] = []
    normal_action_pool: list[dict[str, Any]] | None = None
    for family in families:
        item = dict(family)
        members = [
            row_by_case.get(str(case) or "")
            for case in item.get("failure_cases") or ()
        ]
        members = [row for row in members if row is not None]
        signal: dict[str, Any] = {
            "n": len(members),
            "predicate_signals": {},
            "member_summaries": [],
            "shared_literals": {},
        }
        if dataset_root:
            applicable: dict[str, int] = {}
            violations: dict[str, int] = {}
            profiles: list[Mapping[str, Any]] = []
            for row in members:
                case = str(row.get("case") or "")
                result = row.get("result") or {}
                proof = result.get("proof") or {}
                judge = proof.get("judge") or {}
                candidates = proof.get("recall_candidates") or []
                defense = proof.get("defense") or {}
                refs = list(judge.get("evidence_refs") or [])
                quote = (judge.get("root_cause") or {}).get("quote") or ""
                if not refs and candidates:
                    refs = [
                        ref
                        for candidate in candidates
                        for ref in candidate.get("evidence_refs") or ()
                    ]
                    quote = quote or (candidates[0].get("quote") or "")
                if not refs and defense:
                    refs = list(defense.get("evidence_refs") or [])
                contexts = {
                    "required_action_present": {
                        "contracts": judge.get("contracts") or [],
                    },
                    "verbatim_fidelity": {},
                    "evidence_refs_resolve": {
                        "evidence_refs": refs,
                        "quote": quote,
                    },
                    "contract_coverage": {
                        "contracts": judge.get("contracts") or [],
                    },
                    "delivered_anchor_consistency": {
                        "delivered_result": judge.get("delivered_result") or "",
                    },
                    "final_delivery_present": {},
                }
                try:
                    record, _gold = load_case(dataset_root, case)
                    packet = build_packet(record)
                except Exception:
                    continue
                member_predicates: dict[str, str] = {}
                for predicate, context in contexts.items():
                    try:
                        verdict = str(run_predicate(
                            predicate, packet, context,
                        ).get("verdict") or "")
                    except Exception:
                        continue
                    if verdict != "not_applicable":
                        applicable[predicate] = applicable.get(predicate, 0) + 1
                    if verdict in _VIOLATION_VERDICTS:
                        violations[predicate] = violations.get(predicate, 0) + 1
                    member_predicates[predicate] = verdict
                signature = _mechanism_signature(row)
                typed_context = judge_predicate_context(judge)
                profile = typed_field_profile(packet, typed_context)
                profiles.append(profile)
                signal["member_summaries"].append({
                    "member": f"member_{len(signal['member_summaries']) + 1:03d}",
                    "contracts": _contract_kinds(row),
                    "stages": signature["stages"],
                    "judge_positive": signature["judge_positive"],
                    "recall_path": signature["recall_path"],
                    "upgrade_positive": signature["upgrade_positive"],
                    "defense_verdict": signature["defense_verdict"],
                    "predicates": member_predicates,
                    "typed_samples": profile,
                    "causal_failure": causal_failure_prompt_view(
                        build_causal_failure_record(row, record, policy=causal_policy)),
                })
            signal["predicate_signals"] = {
                predicate: {
                    "applicable": applicable.get(predicate, 0),
                    "violations": violations.get(predicate, 0),
                }
                for predicate in (
                    "required_action_present",
                    "verbatim_fidelity",
                    "evidence_refs_resolve",
                    "contract_coverage",
                    "delivered_anchor_consistency",
                    "final_delivery_present",
                )
            }
            signal["shared_literals"] = _shared_literals(profiles)
        item["diagnostics"] = signal
        patterns = shared_action_rule_conditions(item)
        if patterns and dataset_root and causal_policy is not None:
            if normal_action_pool is None:
                normal_action_pool = []
                for row in rows:
                    if row.get("gold") is not False or row.get("prediction") is not False:
                        continue
                    case = str(row.get("case") or "")
                    if not case:
                        continue
                    try:
                        record, _gold = load_case(dataset_root, case)
                    except (ValueError, OSError):
                        continue
                    normal_action_pool.append({**row, "record": record})
            matching = set(matching_action_normal_neighbors(patterns, normal_action_pool, policy=causal_policy))
            item["normal_action_neighbors"] = [row for row in normal_action_pool if row["case"] in matching][:4]
        diagnosed.append(item)
    return diagnosed


def refine_failure_families(
    families: Sequence[Mapping[str, Any]],
    *,
    min_cases: int = 2,
) -> list[dict[str, Any]]:
    """Split execution families by deterministic predicate profiles.

    Stage/contract signatures are available before packet diagnosis. This
    second, still label-free pass prevents members with different mechanical
    evidence from being presented as one repairable mechanism.
    """
    refined: list[dict[str, Any]] = []
    for family in families:
        diagnostics = family.get("diagnostics") or {}
        members = list(diagnostics.get("member_summaries") or ())
        case_ids = list(family.get("failure_cases") or ())
        if len(members) != len(case_ids):
            item = dict(family)
            item["diagnostic_complete"] = False
            refined.append(item)
            continue
        groups: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for case_id, member in zip(case_ids, members):
            profile = json.dumps(
                member.get("predicates") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            groups.setdefault(profile, []).append((str(case_id), member))
        largest = max((len(group) for group in groups.values()), default=0)
        if largest < min_cases:
            # Heterogeneous profiles without a stable subgroup: keep the whole
            # family instead of discarding it, or the loop silently produces
            # nothing. Mechanical conviction stays blocked by the STRUCTURAL unanimity
            # gate; evidence-only candidates remain possible.
            item = dict(family)
            item["diagnostic_complete"] = True
            item["profile_homogeneous"] = False
            refined.append(item)
            continue
        for profile, group in sorted(groups.items()):
            if len(group) < min_cases:
                continue
            item = dict(family)
            item["failure_family"] = (
                f"{family.get('failure_family')}-P{_sha(profile)}"
            )
            item["failure_cases"] = [case_id for case_id, _member in group]
            if "localization_states" in family:
                states_by_case = dict(zip(case_ids, family["localization_states"]))
                item["localization_states"] = [states_by_case.get(case_id, "mislocated") for case_id, _member in group]
            item["count"] = len(group)
            item["diagnostic_complete"] = True
            item["diagnostics"] = {
                "n": len(group),
                "member_summaries": [dict(member) for _case, member in group],
                "predicate_signals": _predicate_signal_counts(
                    [member for _case, member in group]
                ),
                "shared_literals": _shared_literals([
                    (member.get("typed_samples") or {})
                    for _case, member in group
                ]),
            }
            refined.append(item)
    refined.sort(key=lambda item: (-int(item.get("count") or 0), str(
        item.get("failure_family") or ""
    )))
    return refined


def excluded_failure_cases(
    families: Sequence[Mapping[str, Any]],
    covered: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Cases that entered family formation but no surviving family carries.

    ``refine_failure_families`` splits a coarse family by mechanical profile
    and keeps only groups of at least ``min_cases``, so a four-case family can
    leave two cases behind. That rule is right -- a one-case group cannot carry
    the two provenance cases STRUCTURAL demands -- but the loss must be
    reported, or the patrol looks like it found fewer failures than it did and
    the mechanism grouping is judged on a set nobody can reconstruct.

    Reported, not repaired: the cases are evidence about the action space, and
    the same reasoning as ``localization_annotation_choices`` applies.
    """
    kept: dict[str, set[str]] = {}
    for family in covered:
        kept.setdefault(str(family.get("failure_family") or ""), set()).update(
            str(case) for case in family.get("failure_cases") or ()
        )
    covered_cases = {case for cases in kept.values() for case in cases}
    out: list[dict[str, Any]] = []
    for family in families:
        family_id = str(family.get("failure_family") or "")
        cases = [str(case) for case in family.get("failure_cases") or ()]
        dropped = [case for case in cases if case not in covered_cases]
        if not dropped:
            continue
        out.append({
            "failure_family": family_id,
            "parent_count": len(cases),
            "excluded_count": len(dropped),
            "excluded_cases": dropped,
            "reason": (
                "mechanical profile group smaller than the two-case minimum; "
                "kept out of the proposable set rather than mixed into a "
                "subgroup whose repair operation would be wrong for it"
            ),
        })
    out.sort(key=lambda entry: str(entry.get("failure_family") or ""))
    return out


def mechanism_subgroups(
    families: Sequence[Mapping[str, Any]],
    *,
    min_cases: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split coarse families by task-level mechanism, not by execution path.

    A coarse family is a group of cases whose verdicts went wrong in the same
    place. That is not the same as a group of cases that need the same repair:
    a family can mix an omitted tool action with a misquoted span, and a
    proposer handed both drafts a rule too wide for either.

    The split is by ``evolution.mechanisms`` reading -- obligation, deviation,
    failing stage, repair operation -- so it is decided by what would repair
    the case. A family whose members agree, or whose members carry no readings
    at all, passes through whole with a mechanism attached.

    Returns ``(families, unrepresented)``. Members whose reading is shared by
    fewer than ``min_cases`` cases cannot be proposed on: STRUCTURAL demands at
    least two provenance cases per candidate, so a one-case subgroup could only
    ever produce drafts the validator must reject. They are returned separately
    as evidence -- with their mechanism id -- instead of being dropped or
    merged into a neighbouring mechanism.

    Detector identity is preserved: every subgroup keeps the coarse
    ``failure_family`` id and its signature, so gates that route on the family
    prefix (polarity, target kind, provenance membership) keep working.
    """
    out: list[dict[str, Any]] = []
    unrepresented: list[dict[str, Any]] = []
    for family in families:
        item = dict(family)
        family_id = str(item.get("failure_family") or "")
        objective = (
            "localization"
            if str(item.get("target_kind") or "") == "localization"
            or family_id.startswith("FAM-loc-")
            else "detection"
        )
        members = list((item.get("diagnostics") or {}).get(
            "member_summaries") or ())
        case_ids = [str(case) for case in item.get("failure_cases") or ()]
        local_states = list(item.get("localization_states") or ())
        signature = item.get("mechanism_signature") or {}
        if not members or len(members) != len(case_ids):
            # Without one summary per case there is nothing to split on, and
            # the coarse family is still a mechanism -- reported as a
            # path-only hypothesis rather than silently dropped.
            mechanism = infer_mechanism(
                family_id,
                target_kind=objective,
                members=(),
                path_signature=signature,
                member_cases=case_ids,
                count=int(item.get("count") or len(case_ids)),
            )
            item["mechanism"] = mechanism
            item["mechanism_subgroup"] = False
            out.append(item)
            continue
        readings = [
            member_reading(
                member,
                family_id=family_id,
                objective=objective,
                local_state=(
                    local_states[index]
                    if index < len(local_states) else "mislocated"
                ),
            )
            for index, member in enumerate(members)
        ]
        groups: dict[tuple[str, ...], list[int]] = {}
        for index, reading in enumerate(readings):
            groups.setdefault(reading_signature(reading), []).append(index)
        split = any(len(indices) >= min_cases for indices in groups.values())
        if len(groups) <= 1 or not split:
            # Either every member agrees, or no single reading is shared by
            # two cases. A split that produces no proposable subgroup would
            # empty the family -- a two-case family becomes two one-case
            # readings and the proposer loses the family it used to have. The
            # split only happens when it yields at least one homogeneous
            # subgroup of proposable size; otherwise the coarse family stays
            # whole and its mechanism is reported as mixed.
            mechanism = infer_mechanism(
                family_id,
                target_kind=objective,
                members=members,
                path_signature=signature,
                local_states=local_states,
                member_cases=case_ids,
                count=int(item.get("count") or len(case_ids)),
            )
            item["mechanism"] = mechanism
            item["mechanism_subgroup"] = False
            out.append(item)
            continue
        for signature_key, indices in sorted(groups.items()):
            group = [members[index] for index in indices]
            cases = [case_ids[index] for index in indices]
            mechanism = infer_mechanism(
                family_id,
                target_kind=objective,
                members=group,
                path_signature=signature,
                local_states=[
                    local_states[index]
                    if index < len(local_states) else "mislocated"
                    for index in indices
                ],
                member_cases=cases,
                count=len(cases),
            )
            mechanism["reading"] = list(signature_key)
            if len(group) < min_cases:
                unrepresented.append({
                    **{
                        key: mechanism.get(key)
                        for key in (
                            "mechanism_id", "schema_version", "objective",
                            "status", "confidence_basis", "task_obligation",
                            "observed_deviation", "detector_failure",
                            "repair_hypothesis", "member_cases",
                        )
                    },
                    "family_id": family_id,
                    "case_count": len(cases),
                    "reason": (
                        "fewer than two cases share this reading; STRUCTURAL "
                        "requires two provenance cases, so this reading is "
                        "reported as evidence and not proposed on"
                    ),
                })
                continue
            sub = dict(item)
            # A subgroup needs its own family id: gates and the proposal
            # validator key the diagnosed families by ``failure_family``, so
            # two subgroups sharing the coarse id would overwrite each other
            # and one of them would look undiagnosed. The coarse prefix is
            # kept verbatim so the polarity and target-kind routing that reads
            # ``FAM-fn-`` / ``FAM-fp-`` / ``FAM-loc-`` keeps working.
            subgroup_id = (
                f"{family_id}-M{str(mechanism['mechanism_id'])[2:]}"
            )
            mechanism["family_id"] = subgroup_id
            mechanism["coarse_family_id"] = family_id
            sub["failure_family"] = subgroup_id
            sub["coarse_failure_family"] = family_id
            sub["failure_cases"] = cases
            if "localization_states" in item:
                sub["localization_states"] = [local_states[index] for index in indices]
            sub["count"] = len(cases)
            sub["sources"] = sorted({source_of(case) for case in cases})
            sub["diagnostics"] = {
                **(item.get("diagnostics") or {}),
                "n": len(group),
                "member_summaries": [dict(member) for member in group],
                "predicate_signals": _predicate_signal_counts(group),
                "shared_literals": _shared_literals([
                    (member.get("typed_samples") or {}) for member in group
                ]),
            }
            sub["mechanism_subgroup"] = True
            sub["mechanism"] = mechanism
            out.append(sub)
    out.sort(key=lambda entry: (
        -int(entry.get("count") or 0),
        str(entry.get("failure_family") or ""),
    ))
    unrepresented.sort(key=lambda entry: str(entry.get("mechanism_id") or ""))
    return out, unrepresented


def attach_normal_neighbors(
    families: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Give each mechanism the cases the bare base already handles.

    ``normal_neighbors`` is the other half of a mechanism: the cases where the
    failure direction is *not* present and the base detector is already right.
    Both development checks need them -- the deterministic predicate must not
    fire on a normal neighbour (§7.1), and L1 must show the neighbours held
    (§7.4). Left empty, those two checks silently pass over nothing.

    They are taken from the same rows the mechanism was diagnosed on, so a
    neighbour is a case from the same patrol rather than a case invented for
    the check. They count as development exposure: a case used to check a
    candidate may not also decide its promotion.
    """
    healthy = []
    for row in rows:
        gold = row.get("gold")
        if gold is not True and gold is not False:
            continue
        if str(row.get("authority") or "") != "authoritative":
            continue
        if row.get("prediction") != gold:
            continue
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            continue
        case = str(row.get("case") or "")
        if case:
            healthy.append(case)
    healthy = sorted(dict.fromkeys(healthy))[:max(0, int(limit))]
    out: list[dict[str, Any]] = []
    for family in families:
        item = dict(family)
        mechanism = item.get("mechanism")
        if isinstance(mechanism, Mapping):
            mechanism = dict(mechanism)
            members = {str(case) for case in mechanism.get("member_cases") or ()}
            mechanism["normal_neighbors"] = [
                case for case in healthy if case not in members
            ]
            item["mechanism"] = mechanism
        out.append(item)
    return out


def mechanism_report(
    families: Sequence[Mapping[str, Any]],
    unrepresented: Sequence[Mapping[str, Any]] = (),
    excluded: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The deduplicated mechanism view of one patrol, for any report.

    Public because both the cycle worksheet and the cheaper preview need it:
    the preview exists to show what a cycle would propose, and since a
    proposal is aimed at a mechanism, a preview that shows only family ids
    cannot answer that.
    """
    mechanisms: dict[str, dict[str, Any]] = {}
    for family in families:
        mechanism = family.get("mechanism")
        if not isinstance(mechanism, Mapping):
            continue
        mechanism_id = str(mechanism.get("mechanism_id") or "")
        if not mechanism_id:
            continue
        entry = mechanisms.get(mechanism_id)
        if entry is None:
            mechanisms[mechanism_id] = {
                **{
                    key: mechanism.get(key)
                    for key in (
                        "mechanism_id", "schema_version", "objective",
                        "status", "confidence_basis", "task_obligation",
                        "observed_deviation", "detector_failure",
                        "repair_hypothesis",
                    )
                },
                "families": [str(family.get("failure_family") or "")],
                "case_count": int(family.get("count") or 0),
                "proposable": True,
            }
            continue
        family_id = str(family.get("failure_family") or "")
        if family_id not in entry["families"]:
            entry["families"].append(family_id)
        entry["case_count"] += int(family.get("count") or 0)
        if mechanism.get("status") == "confirmed":
            entry["status"] = "confirmed"
    return {
        "count": len(mechanisms),
        "mechanisms": [mechanisms[key] for key in sorted(mechanisms)],
        # Readings too rare to propose on (fewer than two cases) stay visible
        # here: a mechanism with one case is evidence, not a silent gap.
        "unrepresented": [
            {
                **{
                    key: item.get(key)
                    for key in (
                        "mechanism_id", "schema_version", "objective",
                        "status", "confidence_basis", "task_obligation",
                        "observed_deviation", "detector_failure",
                        "repair_hypothesis",
                    )
                },
                "families": [str(item.get("family_id") or "")],
                "case_count": int(item.get("case_count") or 0),
                "proposable": False,
                "reason": item.get("reason"),
            }
            for item in unrepresented
        ],
        # Cases dropped by the profile split upstream: reported so the patrol's
        # true failure count stays reconstructible from the report alone.
        "excluded": [dict(item) for item in excluded],
    }


def _predicate_signal_counts(
    members: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    predicates = sorted({
        str(name)
        for member in members
        for name in (member.get("predicates") or {})
    })
    return {
        predicate: {
            "applicable": sum(
                1 for member in members
                if str((member.get("predicates") or {}).get(predicate) or "")
                != "not_applicable"
            ),
            "violations": sum(
                1 for member in members
                if str((member.get("predicates") or {}).get(predicate) or "")
                in _VIOLATION_VERDICTS
            ),
        }
        for predicate in predicates
    }


__all__ = [
    "attach_normal_neighbors",
    "diagnose_failure_families",
    "excluded_failure_cases",
    "failure_family",
    "localization_family",
    "localization_state",
    "mechanism_report",
    "mechanism_subgroups",
    "refine_failure_families",
    "stable_failure_families",
    "stable_localization_families",
    "worksheet_report",
]
