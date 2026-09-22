"""STRUCTURAL -> HARMLESSNESS -> BEHAVIOR -> M admission gates (verifier-as-gatekeeper).

All gate scoring uses gold joined on the evolution side. LLM output is never
a promotion score. Any gate failure is terminal for the candidate: the
protocol does not search-edit a rejected candidate.

Every gate is a pure function over detection rows. The engine evaluates one
arm per admission unit on S+G and hands the same rows to HARMLESSNESS (S subset), BEHAVIOR
(S+G) and M (G subset); no gate runs its own detector sweep.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from detector.dataset import load_case
from detector.interventions import (
    custom_conviction_verdicts_for,
    custom_fact_vocab_for,
    inline_predicate_programs,
    inline_trigger_programs,
    validate_intervention,
)
from detector.packet import build_packet
from detector.predicate_context import judge_predicate_context, missing_judge_inputs
from detector.predicate_dsl import run_predicate_program

from evolution.contracts import (
    candidate_contains_case_id,
    candidate_prose_text,
    identity_incompleteness,
    jaccard,
    make_intervention,
    structured_key,
)
from evolution.metrics import (
    evidence_increment_rows,
    fired_rate,
    paired_delta,
    paired_localization_delta,
)
from evolution.metamorphic import admission_error

SEMANTICS_KEYS = (
    "JUDGE_SYSTEM", "RECALL_SYSTEM", "UPGRADE_SYSTEM", "TRACER_SYSTEM",
    "DEFENSE_SYSTEM",
)


def requires_fresh_evaluation(
    entries: Sequence[Mapping[str, Any]],
) -> bool:
    """Whether the entire provider flow needs a fresh paired control.

    Downstream hooks instead reuse the judge and rerun their affected suffix.
    """
    return any(
        str(entry.get("hook") or "") == "judge.system"
        or (
            entry.get("type") == "decision_guard"
            and (entry.get("body") or {}).get("action")
            == "require_full_view_defense"
        )
        for entry in entries
    )


def _semantics_text() -> str:
    from detector import semantics

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


def _decisive_correction(delta: Mapping[str, Any]) -> tuple[int, int]:
    """Count decisive-only corrections and regressions in a paired delta.

    ``correctness_improved`` also counts unresolved -> correct, which lets a
    mere calibration change (deciding a previously-undecided case) satisfy a
    polarity claim without correcting any actual decision. Boundary claims
    are scored on decisive flips only.
    """
    fixed = broken = 0
    for row in delta.get("rows") or ():
        gold = row.get("gold")
        base = row.get("baseline_prediction")
        active = row.get("active_prediction")
        base_correct = base is not None and base == gold
        active_correct = active is not None and active == gold
        if active is not None and base is not None and active_correct and not base_correct:
            fixed += 1
        elif base_correct and active is not None and not active_correct:
            broken += 1
    return fixed, broken


INFRASTRUCTURE_STATE_PREFIXES = (
    "provider_error", "invalid", "contract_error", "degraded", "no_quorum",
    "timeout", "not_rendered",
)


def arm_has_infrastructure_failure(
    rows: Sequence[Mapping[str, Any]],
) -> str:
    """Return a reason when an arm could not be scored honestly.

    A gate rejection caused by an unresolved row, a missing label, a
    non-authoritative terminal, or a failed stage is not evidence that the
    gate *logic* rejected the candidate. The defect-injection audit must
    distinguish the two, otherwise a deadline or outage that fail-closes every
    row would certify the gates without exercising them.
    """
    if not rows:
        return "arm produced no rows"
    for row in rows:
        if row.get("gold") is not True and row.get("gold") is not False:
            return f"missing boolean gold for case {row.get('case')}"
        if row.get("prediction") is None:
            return f"unresolved row for case {row.get('case')}"
        if str(row.get("authority") or "") != "authoritative":
            return (
                f"non-authoritative row for case {row.get('case')} "
                f"({row.get('authority')})"
            )
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            return f"runtime-invalid row for case {row.get('case')}"
        for status in (result.get("stage_status") or {}).values():
            if str((status or {}).get("state") or "").startswith(
                INFRASTRUCTURE_STATE_PREFIXES
            ):
                return (
                    f"failed stage {status.get('state')!r} on case "
                    f"{row.get('case')}"
                )
    return ""


def _policy_pass(**extra: Any) -> dict[str, Any]:
    return {"passed": True, "reason": "", **extra}


def _run_program_on_family(
    program: Mapping[str, Any],
    failure_cases: Sequence[str],
    dataset_root: str,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str | None, str]:
    """Run one evolved predicate on every family member's label-free packet.

    The gate re-uses the same judge.post context the detector would provide
    when the patrol row carries a judge result, and an empty context otherwise.
    A program that fails to produce the same conviction-authorized verdict on
    every member cannot authorize a terminal conviction and is rejected at STRUCTURAL
    before provider budget.
    """
    contexts = dict(contexts or {})
    verdicts: set[str] = set()
    details: list[str] = []
    for case in failure_cases:
        try:
            record, _gold = load_case(dataset_root, case)
            packet = build_packet(record)
            context = contexts.get(str(case), {})
            result = run_predicate_program(program, packet, context)
        except Exception as exc:
            return None, f"{case}: {type(exc).__name__}: {exc}"
        verdicts.add(str(result.get("verdict") or ""))
        details.append(str(result.get("detail") or ""))
    if len(verdicts) == 1:
        return next(iter(verdicts)), "; ".join(details[:3])
    return None, f"heterogeneous verdicts {sorted(verdicts)}"


def _family_contexts(
    failure_cases: Sequence[str],
    patrol_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    row_by_case = {str(row.get("case") or ""): row for row in patrol_rows}
    contexts: dict[str, Mapping[str, Any]] = {}
    for case in failure_cases:
        row = row_by_case.get(str(case))
        if not isinstance(row, Mapping):
            contexts[str(case)] = {}
            continue
        judge = (
            ((row.get("result") or {}).get("proof") or {}).get("judge")
            or {}
        )
        contexts[str(case)] = judge_predicate_context(judge)
    return contexts


def _missing_family_inputs(program, contexts):
    missing = {case: fields for case, context in contexts.items()
               if (fields := missing_judge_inputs(program, context))}
    if missing:
        return _policy_fail(
            f"family replay is missing recorded judge inputs: {missing}",
            evaluable=False, status="not_evaluated",
        )
    return None


def structural_gate(
    candidate: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    allowed_failure_cases: Sequence[str] | None = None,
    diagnosed_families: Sequence[Mapping[str, Any]] | None = None,
    dataset_root: str | None = None,
    batch_candidates: Sequence[Mapping[str, Any]] | None = None,
    patrol_rows: Sequence[Mapping[str, Any]] | None = None,
    joint_groups: Sequence[Sequence[str]] = (),
) -> dict[str, Any]:
    gate_cfg = config.get("gates") or {}
    batch = [candidate] if batch_candidates is None else list(batch_candidates)
    programs = inline_predicate_programs(batch)
    trigger_programs = inline_trigger_programs(batch)
    fact_vocab = custom_fact_vocab_for(programs)
    custom_convictions = custom_conviction_verdicts_for(programs)
    try:
        validate_intervention(
            candidate,
            require_executable_bounds=True,
            custom_fact_vocab=fact_vocab,
            custom_predicate_verdicts={
                name: frozenset(program.get("verdicts") or ())
                for name, program in programs.items()
            },
            custom_conviction_verdicts=custom_convictions,
        )
    except ValueError as exc:
        return _policy_fail(f"schema/executor rejection: {exc}")

    case_id_hit = candidate_contains_case_id(candidate)
    if case_id_hit:
        return _policy_fail(f"case id leak in directive/body/claim: {case_id_hit}")

    # §15.2 / P2.3: the runner a candidate names must have passed the
    # metamorphic admission gate. The check sits immediately after the schema
    # check for the same reason the schema check is first: a candidate whose
    # executor has no invariance contract is refused on the executor, not
    # measured and then compared with others. Its verdict is admission-only and
    # replaces nothing -- every later gate still runs.
    runner_error = admission_error(str((candidate.get("body") or {}).get("runner") or ""))
    if runner_error:
        return _policy_fail(f"metamorphic admission: {runner_error}")

    # §23.2: a learned artifact must have a digest. An entry whose runner reads
    # an artifact it does not pin cannot be told apart from a different model
    # wearing the same declared fields, and the STRUCTURAL redundancy check
    # below -- and every later reading -- would be comparing on an identity
    # that does not determine behaviour.
    incomplete = identity_incompleteness(candidate)
    if incomplete:
        return _policy_fail(f"incomplete executable identity: {incomplete}")

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
    target_kind = candidate_target_kind(candidate)
    if family.startswith("FAM-fn-") and polarity != "recall":
        return _policy_fail(
            f"FN failure family requires recall polarity, got {polarity!r}"
        )
    if family.startswith("FAM-fp-") and polarity != "precision":
        return _policy_fail(
            f"FP failure family requires precision polarity, got {polarity!r}"
        )
    # The family's mechanism and the claim's objective must agree. A
    # localization family treats a case the detector already convicts
    # correctly, so a candidate declaring the binary objective (or the
    # reverse) would be scored by a gate that cannot see its mechanism at all.
    if family.startswith("FAM-loc-") and target_kind not in {
        "localization", "joint",
    }:
        return _policy_fail(
            "localization failure family requires target_kind localization or "
            f"joint, got {target_kind!r}"
        )
    if (
        family.startswith(("FAM-fn-", "FAM-fp-"))
        and target_kind != "detection"
    ):
        return _policy_fail(
            f"detection failure family {family!r} cannot claim target_kind "
            f"{target_kind!r}"
        )
    body = candidate.get("body") or {}
    if (
        candidate.get("type") == "decision_guard"
        and body.get("action") == "convict_mechanical_violation"
        and diagnosed_families is not None
    ):
        diagnosed = next(
            (
                item for item in diagnosed_families
                if str(item.get("failure_family") or "") == family
            ),
            {},
        )
        predicate = str(body.get("evidence_predicate") or "")
        family_count = int(diagnosed.get("count") or 0)
        signal = ((diagnosed.get("diagnostics") or {}).get(
            "predicate_signals", {}
        ) or {}).get(predicate) or {}
        if predicate in programs:
            # A new typed-DSL predicate has no historical worksheet signal.
            # Its STRUCTURAL evidence is execution on every patrol member of the
            # family: unanimous conviction-authorized verdict or reject.
            if dataset_root is None:
                return _policy_fail(
                    "evolved predicate requires dataset_root for STRUCTURAL family "
                    "execution"
                )
            program = programs[predicate]
            contexts = _family_contexts(failure_cases, patrol_rows or ())
            unavailable = _missing_family_inputs(program, contexts)
            if unavailable:
                return unavailable
            verdict, detail = _run_program_on_family(
                program, failure_cases, dataset_root, contexts,
            )
            authorized = frozenset(program.get("conviction_verdicts") or ())
            if verdict is None or verdict not in authorized:
                return _policy_fail(
                    "mechanical conviction requires unanimous applicable "
                    f"violation evidence across its family: predicate={predicate!r}, "
                    f"observed={verdict!r}, detail={detail!r}"
                )
        elif (
            family_count < 2
            or int(signal.get("applicable") or 0) != family_count
            or int(signal.get("violations") or 0) != family_count
        ):
            return _policy_fail(
                "mechanical conviction requires unanimous applicable violation "
                f"evidence across its family: predicate={predicate!r}, "
                f"applicable={int(signal.get('applicable') or 0)}, "
                f"violations={int(signal.get('violations') or 0)}, "
                f"family_count={family_count}"
            )

    if (
        candidate.get("type") == "recall_trigger"
        and diagnosed_families is not None
    ):
        diagnosed = next(
            (
                item for item in diagnosed_families
                if str(item.get("failure_family") or "") == family
            ),
            {},
        )
        predicate = str(body.get("evidence_predicate") or body.get("predicate") or "")
        if predicate in trigger_programs:
            if dataset_root is None:
                return _policy_fail(
                    "recall trigger program requires dataset_root for STRUCTURAL "
                    "family execution"
                )
            program = trigger_programs[predicate]
            contexts = _family_contexts(failure_cases, patrol_rows or ())
            unavailable = _missing_family_inputs(program, contexts)
            if unavailable:
                return unavailable
            verdict, detail = _run_program_on_family(
                program, failure_cases, dataset_root, contexts,
            )
            trigger_verdicts = frozenset(body.get("trigger_verdicts") or ())
            if verdict is None or verdict not in trigger_verdicts:
                return _policy_fail(
                    "recall_trigger requires its predicate to emit a trigger "
                    f"verdict on every family member: predicate={predicate!r}, "
                    f"observed={verdict!r}, detail={detail!r}"
                )
        else:
            family_count = int(diagnosed.get("count") or 0)
            signal = ((diagnosed.get("diagnostics") or {}).get(
                "predicate_signals", {}
            ) or {}).get(predicate) or {}
            if (
                family_count < 2
                or int(signal.get("applicable") or 0) != family_count
                or int(signal.get("violations") or 0) != family_count
            ):
                return _policy_fail(
                    "recall_trigger requires unanimous applicable violation "
                    f"evidence across its family: predicate={predicate!r}, "
                    f"applicable={int(signal.get('applicable') or 0)}, "
                    f"violations={int(signal.get('violations') or 0)}, "
                    f"family_count={family_count}"
                )

    if candidate.get("hook") == "upgrade.system" and patrol_rows is not None:
        family_rows = [r for r in patrol_rows if r.get("case") in failure_cases]
        partners = {identifier for group in joint_groups
                    if candidate.get("intervention_id") in group for identifier in group}
        upstream = any(entry.get("hook") in {"judge.system", "scanner.system", "recall.trigger"}
                       for entry in [*active_entries, *[e for e in batch
                           if e.get("intervention_id") in partners
                           and e.get("intervention_id") != candidate.get("intervention_id")]])
        if family_rows and not upstream and all(
            ((r.get("result") or {}).get("stage_status") or {}).get("causal_upgrade", {}).get("state")
            in {"not_needed", "not_run"} for r in family_rows
        ):
            return _policy_fail("unreachable upgrade hook on every family member; declare an upstream joint dependency")

    candidate_text = candidate_prose_text(candidate)
    base_text = _semantics_text()
    threshold = float(gate_cfg.get("structural_jaccard_threshold", 0.72))
    candidate_key = structured_key(candidate)
    for existing in active_entries:
        existing_key = structured_key(existing)
        # The structural comparison is authoritative only when both keys
        # describe what actually executes. An entry whose runner reads an
        # unpinned artifact has an incomplete key, so the comparison falls
        # through to prose instead of claiming the two are different executors.
        if (
            candidate_key is not None
            and existing_key is not None
            and not identity_incompleteness(existing)
        ):
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


def sentinel_coverage(
    rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
) -> dict[str, Any]:
    """Check measurement capacity using the control arm, before any proposal."""
    gate_cfg = config.get("gates") or {}
    counts = {
        key: sum(row.get("gold") is gold and row.get("prediction") is gold
                 and row.get("authority") == "authoritative" for row in rows)
        for key, gold in (("protected_tp", True), ("protected_tn", False))
    }
    minima = {
        "protected_tp": int(gate_cfg.get("harmlessness_min_protected_tp", 2)),
        "protected_tn": int(gate_cfg.get("harmlessness_min_protected_tn", 2)),
    }
    missing = [f"{key}={counts[key]} < {minimum}" for key, minimum in minima.items()
               if counts[key] < minimum]
    return {**counts, "minimum": minima, "evaluable": not missing,
            "reason": "; ".join(missing)}


def harmlessness_gate(
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
    # Validate the complete matched arm before filtering to protected TP/TN.
    # This catches duplicate, missing, extra, or gold-mismatched rows that a
    # dict projection below would otherwise hide.
    paired_delta(list(baseline_rows), list(candidate_rows))
    candidate_by_case = {
        str(row.get("case") or ""): row for row in candidate_rows
    }
    protected_baseline = [
        row for row in baseline_rows
        if row.get("gold") in (True, False)
        and row.get("prediction") is row.get("gold")
        and str(row.get("authority") or "") == "authoritative"
    ]
    protected_cases = {
        str(row.get("case") or "") for row in protected_baseline
    }
    protected_candidate = [
        candidate_by_case[case]
        for case in [str(row.get("case") or "") for row in protected_baseline]
        if case in candidate_by_case
    ]
    protected_tp = sum(
        1 for row in protected_baseline if row.get("gold") is True
    )
    protected_tn = sum(
        1 for row in protected_baseline if row.get("gold") is False
    )
    excluded = []
    for row in baseline_rows:
        case = str(row.get("case") or "")
        if case in protected_cases:
            continue
        excluded.append({
            "case": case,
            "gold": row.get("gold"),
            "prediction": row.get("prediction"),
            "authority": row.get("authority"),
            "reason": (
                "baseline_unresolved_or_invalid"
                if row.get("prediction") is None
                or str(row.get("authority") or "") != "authoritative"
                else "baseline_incorrect"
            ),
        })
    sentinel_manifest = {
        "nominated": len(baseline_rows),
        "protected": len(protected_baseline),
        "protected_tp": protected_tp,
        "protected_tn": protected_tn,
        "excluded": excluded,
        "baseline_rows": [
            {"case": r.get("case"), "gold": r.get("gold"),
             "prediction": r.get("prediction")}
            for r in protected_baseline
        ],
        "candidate_rows": [
            {"case": r.get("case"), "gold": r.get("gold"),
             "prediction": r.get("prediction")}
            for r in protected_candidate
        ],
    }
    minimum_tp = int(gate_cfg.get("harmlessness_min_protected_tp", 2))
    minimum_tn = int(gate_cfg.get("harmlessness_min_protected_tn", 2))
    coverage_sufficient = protected_tp >= minimum_tp and protected_tn >= minimum_tn
    delta = paired_delta(protected_baseline, protected_candidate)
    tolerance = {
        "new_fn": int(gate_cfg.get("harmlessness_new_fn_allowed", 0)),
        "new_fp": int(gate_cfg.get("harmlessness_new_fp_allowed", 0)),
        "new_unresolved": int(gate_cfg.get("harmlessness_new_unresolved_allowed", 0)),
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
            sentinel_manifest=sentinel_manifest,
            evaluable=True,
            coverage_sufficient=coverage_sufficient,
        )
    if not coverage_sufficient:
        return _policy_fail(
            "sentinel has insufficient baseline-correct coverage: "
            f"protected_tp={protected_tp} (minimum {minimum_tp}), "
            f"protected_tn={protected_tn} (minimum {minimum_tn})",
            delta=delta,
            reroll_control=reroll_control,
            sentinel_manifest=sentinel_manifest,
            evaluable=False,
            status="not_evaluated",
            coverage_sufficient=False,
        )
    return _policy_pass(
        delta=delta,
        reroll_control=reroll_control,
        sentinel_manifest=sentinel_manifest,
    )


def behavior_gate(
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
    if bool(gate_cfg.get("behavior_require_fired", True)) and rate <= 0.0:
        return _policy_fail(
            "claim behavior mismatch: intervention never fired on S union G",
            fired_rate=rate,
        )

    behavior_ok = False
    behavior_note = ""
    # Machine-readable counterpart of ``behavior_note``. The note is prose for
    # a report; route-insufficiency evidence (``evolution.route_failures``)
    # needs the count itself, because "the hook fired and moved nothing" is a
    # claim about numbers, not about a sentence.
    behavior_events = 0
    behavior_event_kind = {
        "evidence_check": "evidence_increment",
        "text_boundary": "text_injected",
        "recall_trigger": "recall_triggered",
        "decision_guard": "guard_fired",
    }.get(intervention_type, "")
    if intervention_type == "evidence_check":
        increment = evidence_increment_rows(
            baseline_rows, candidate_rows, intervention_id,
        )
        behavior_ok = increment > 0
        behavior_events = increment
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
        behavior_events = injected
        behavior_note = f"text_injected_cases={injected}"
    elif intervention_type == "recall_trigger":
        forced = 0
        newly_opened = 0
        base_by_case = {r.get("case"): r for r in baseline_rows}
        for row in candidate_rows:
            deployment = ((row.get("result") or {}).get("proof") or {}).get(
                "deployment"
            ) or {}
            matched = any(
                1 for item in deployment.get("fired") or ()
                if str(item.get("intervention_id") or "") == intervention_id
                and item.get("event") == "recall_triggered"
            )
            forced += int(matched)
            base = base_by_case.get(row.get("case")) or {}
            state = (((base.get("result") or {}).get("stage_status") or {}).get("recall_scanner") or {}).get("state")
            newly_opened += int(matched and state in {"not_needed", "not_run"})
        behavior_ok = forced > 0
        behavior_events = forced
        behavior_note = f"recall_triggered_cases={forced}; newly_opened_recall_cases={newly_opened}"
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
        behavior_events = guard_fired
        behavior_note = f"guard_fired_cases={guard_fired}"

    if not behavior_ok:
        return _policy_fail(
            f"claim behavior mismatch: {behavior_note}",
            fired_rate=rate,
            behavior_events=behavior_events,
            behavior_event_kind=behavior_event_kind,
        )

    delta = paired_delta(list(baseline_rows), candidate_rows)
    if int(delta.get("health_regressed") or 0) > 0:
        return _policy_fail(
            f"runtime health regressed on {delta['health_regressed']} case(s)",
            delta=delta,
            fired_rate=rate,
            behavior_events=behavior_events,
            behavior_event_kind=behavior_event_kind,
        )
    # Every branch below reports the firing readings as well as the delta: this
    # is where "the intervention fired, moved what it promised, and the outcome
    # did not move" lands, and that reading is exactly what route-insufficiency
    # evidence is made of. Reporting only the delta made the case
    # indistinguishable from an intervention that never ran.
    if polarity == "recall":
        if not (_new_tp(delta) > 0 and delta["tp_lost"] == 0 and delta["fp_added"] == 0):
            return _policy_fail(
                "target polarity mismatch: recall claim but no TP gain or "
                "precision was harmed",
                delta=delta,
                fired_rate=rate,
                behavior_events=behavior_events,
                behavior_event_kind=behavior_event_kind,
            )
    elif polarity == "precision":
        if not (_removed_fp(delta) > 0 and delta["tp_lost"] == 0):
            return _policy_fail(
                "target polarity mismatch: precision claim but no FP removal "
                "or TP was lost",
                delta=delta,
                fired_rate=rate,
                behavior_events=behavior_events,
                behavior_event_kind=behavior_event_kind,
            )
    elif polarity == "boundary":
        # A boundary claim must correct a real decision, not merely fire or
        # decide a case that was previously unresolved. Without this the
        # weakest polarity was satisfiable by calibration alone.
        fixed, broken = _decisive_correction(delta)
        if fixed <= 0 or broken > 0:
            return _policy_fail(
                "target polarity mismatch: boundary claim corrected no decisive "
                f"decision (decisive_fixed={fixed}, decisive_broken={broken})",
                delta=delta,
                fired_rate=rate,
                behavior_events=behavior_events,
                behavior_event_kind=behavior_event_kind,
            )
    else:
        return _policy_fail(f"unknown target polarity {polarity!r}")

    return _policy_pass(
        delta=delta,
        fired_rate=rate,
        behavior_note=behavior_note,
        behavior_events=behavior_events,
        behavior_event_kind=behavior_event_kind,
    )


def candidate_target_kind(candidate: Mapping[str, Any]) -> str:
    """What objective the candidate claims: detection, localization or joint.

    Absent means detection, matching ``validate_intervention``: a registry
    entry written before the field existed is a detection claim.
    """
    return str((candidate.get("claim") or {}).get("target_kind") or "detection")


def localization_behavior_gate(
    candidate: Mapping[str, Any],
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Whether a localization claim actually moved responsible-step output.

    Mirrors ``behavior_gate``'s discipline for the localization objective:
    the claimed direction must show up as a real change in the recorded
    responsible step, on cases the frozen batch can score, without breaking
    a localization the control arm already had right. Firing alone is not
    enough, and a binary-only gain cannot satisfy this gate.

    Sides are rescaled before scoring so the batch can carry both polarities:
    every gold-positive annotated case that the control arm convicted correctly
    but localized wrongly counts against the candidate; every one the control
    localized correctly must be preserved.
    """
    gate_cfg = config.get("gates") or {}
    intervention_id = str(candidate.get("intervention_id") or "")
    delta = paired_localization_delta(
        list(baseline_rows), list(candidate_rows), gold_steps=gold_steps,
    )
    if bool(gate_cfg.get("behavior_require_fired", True)):
        rate = fired_rate(candidate_rows, intervention_id)
        if rate <= 0.0:
            return _policy_fail(
                "claim behavior mismatch: intervention never fired on the "
                "localization batch",
                fired_rate=rate,
                delta=delta,
            )
    if int(delta["localizable_n"]) == 0:
        return _policy_fail(
            "localization batch carries no gold-positive annotated case, so "
            "the claim cannot be scored",
            delta=delta,
            evaluable=False,
            status="not_evaluated",
        )
    if int(delta["joint_improved"]) <= 0:
        return _policy_fail(
            "localization claim but no responsible step was corrected "
            f"(joint_improved={delta['joint_improved']}, "
            f"joint_broken={delta['joint_broken']})",
            delta=delta,
        )
    allowed_broken = int(gate_cfg.get("localization_joint_broken_allowed", 0))
    if int(delta["joint_broken"]) > allowed_broken:
        return _policy_fail(
            "localization claim broke existing correct localizations: "
            f"joint_broken={delta['joint_broken']} > {allowed_broken}",
            delta=delta,
        )
    return _policy_pass(delta=delta)


def meets_localization(
    delta: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    detection_delta: Mapping[str, Any] | None = None,
    target_kind: str = "localization",
) -> tuple[bool, list[str]]:
    """Localization benefit rule over a precomputed joint paired delta.

    The joint success delta is measured against the frozen batch, so a case
    that stops being convicted cannot leave the denominator and a miss cannot
    be laundered into a localization gain. Detection health is protected
    separately rather than folded into one weighted score.
    """
    gate_cfg = config.get("gates") or {}
    failures: list[str] = []
    minimum = max(0, int(gate_cfg.get("localization_min_joint_cases", 1)))
    joint_delta = int(delta.get("joint_delta") or 0)
    detection_delta_value = int(
        (detection_delta or {}).get("marginal_cases") or 0
    )
    if target_kind == "joint":
        # At least one objective improves; neither regresses.
        if joint_delta <= 0 and detection_delta_value <= 0:
            failures.append(
                "joint claim improved neither objective "
                f"(joint_delta={joint_delta}, detection_marginal="
                f"{detection_delta_value})"
            )
    elif joint_delta < minimum:
        failures.append(f"joint_delta={joint_delta} < {minimum}")
    allowed_broken = int(gate_cfg.get("localization_joint_broken_allowed", 0))
    if int(delta.get("joint_broken") or 0) > allowed_broken:
        failures.append(
            f"joint_broken={delta['joint_broken']} > {allowed_broken}"
        )
    allowed_new_errors = int(gate_cfg.get("m_new_errors_allowed", 0))
    if detection_delta is not None:
        if int(detection_delta.get("new_errors") or 0) > allowed_new_errors:
            failures.append(
                f"detection new_errors={detection_delta['new_errors']} > "
                f"{allowed_new_errors}"
            )
        if int(detection_delta.get("unresolved_added") or 0) > 0:
            failures.append(
                f"unresolved_added={detection_delta['unresolved_added']}"
            )
        if int(detection_delta.get("health_regressed") or 0) > 0:
            failures.append(
                f"health_regressed={detection_delta['health_regressed']}"
            )
    if int(delta.get("n") or 0) and not int(delta.get("localizable_n") or 0):
        failures.append("localization batch has no annotated positive case")
    return (not failures), failures


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
        expected_behavior="gate audit: case-id leakage must be rejected by STRUCTURAL",
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
        # stages frozen while still producing a fired BEHAVIOR trace that M must
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
    "INFRASTRUCTURE_STATE_PREFIXES",
    "arm_has_infrastructure_failure",
    "candidate_target_kind",
    "defect_candidates",
    "localization_behavior_gate",
    "meets_localization",
    "meets_m",
    "structural_gate",
    "harmlessness_gate",
    "behavior_gate",
    "requires_fresh_evaluation",
]
