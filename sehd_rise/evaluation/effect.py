"""Causal effect evidence: binding replay improvement to the intervention that ran.

Three independent evidence channels decide whether an edit caused its observed
replay movement (plan §2.12 causal credit chain):

1. edge-level counterfactual — the selected repair edge's transition actually
   occurred and its downstream blocking predicate cleared;
2. blocking-predicate closure — the predicate the edge claimed to clear is no
   longer in the replay's terminal-basis blocking facts;
3. intervention fitness — the compiled ActionSpec postcondition held on the
   replay result, and the injected rule's id appears in the applied/effect
   trace.

``CausalEffectRecord`` carries the three as one auditable record. The no-effect
taxonomy (``_classify_replay_no_effect`` / ``_no_effect_reason_for``) stays
unchanged: a no-effect replay is split into the deterministic cause class
(condition not matched, provider ignored the action, output changed then
quarantined).

C10 reconnection (§2.12): the old ``AttributionSignal.should_evolve`` trigger is
absorbed into the Repairability Authority. The expected-effect audit's only
``should_evolve`` dependency was the ``trigger:closed/opened`` delta token,
produced by ``observable_process_delta``; that token now comes from the
``before_trigger`` / ``after_trigger`` repairability verdicts threaded through
the audit. ``_attribute_intervention_fitness`` never read ``should_evolve`` —
it credits purely from the postcondition and edge counterfactual — so it is
ported verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import DetectionSignal
from sehd_rise.evaluation.fitness import (
    CONTRACT_DIRECTION_DELTA_TOKENS,
    FitnessAssessment,
    observable_process_delta,
)
from sehd_rise.execution.action import (
    certificate_carrier_movement,
    classify_execution_evidence,
    evaluate_action_spec_postcondition,
)
from sehd_rise.skills.action import ActionSpec, sanitize_action_spec
from sehd_rise.skills.genome import (
    SkillEdit,
    SkillOp,
    sanitize_expected_effect_contract,
    semantic_overlap,
)
from sehd_rise.skills.rule import sanitize_condition_spec


@dataclass(frozen=True)
class CausalEffectRecord:
    """Three-channel causal-effect evidence for one intervention.

    - ``edge_counterfactual``: the edge-level counterfactual verdict (the
      repair edge's blocking predicate cleared and its source artifact
      transitioned).
    - ``intervention_fitness``: the intervention-fitness audit (postcondition
      met, rule id in the applied/effect trace, four-event distinction).
    - ``no_effect_class``: the deterministic no-effect cause class when the
      replay moved nothing (``""`` when the replay did move).

    The record is the single audit object Phase 9's outcome funnel reads; it
    never re-walks the raw result for a fact its channels already carry.
    """

    edge_counterfactual: Mapping[str, Any]
    intervention_fitness: Mapping[str, Any]
    no_effect_class: str
    expected_effect_audit: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_counterfactual": dict(self.edge_counterfactual),
            "intervention_fitness": dict(self.intervention_fitness),
            "no_effect_class": self.no_effect_class,
            "expected_effect_audit": dict(self.expected_effect_audit),
        }


def _experience_intervention_trace(
    result: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Reduce the deterministic executor record to a reusable execution trace.

    Plan B: the provider no longer self-reports applied rules or output
    fields. The trace is derived from the executor's ``l1_execution`` fired
    rows — rule ids and operations that actually fired on the court result.
    """
    execution = result.get("l1_execution")
    if not isinstance(execution, Mapping):
        return {"applied_rule_ids": [], "output_fields": [], "routes": []}
    applied_ids: list[str] = []
    output_fields: list[str] = []
    for row in execution.get("fired") or ():
        if not isinstance(row, Mapping):
            continue
        rule_id = str(row.get("rule_id") or "")
        operation = str(row.get("operation") or "")
        if rule_id:
            applied_ids.append(rule_id)
        if operation:
            output_fields.append(operation)
    return {
        "applied_rule_ids": applied_ids,
        "output_fields": output_fields,
        "routes": [],
    }


def _baseline_surviving_error_certificate_ids(
    result: Mapping[str, Any] | None,
) -> tuple[str, ...] | None:
    """Read the source-baseline ``surviving_error_certificate_ids``.

    Returned to ``_attribute_intervention_fitness`` so the fingerprint-targeted
    ``withhold_certificate`` postcondition can detect when a targeted
    certificate was actually withheld from the court's surviving set, instead
    of demanding the whole surface go to zero. Returns ``None`` when the
    baseline never reported a surviving set (older stored artifacts); the
    postcondition then degrades to the strict whole-surface form.
    """
    if not isinstance(result, Mapping):
        return None
    proof = result.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        return None
    surviving = proof.get("surviving_error_certificate_ids")
    if not isinstance(surviving, list):
        return None
    return tuple(str(value) for value in surviving if str(value))


def _verify_edge_level_counterfactual(
    repair_edge: Any,
    baseline_result: Mapping[str, Any] | None,
    replay_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the selected repair edge's transition occurred and its downstream
    blocking predicate disappeared.

    The module/operation-level postcondition checks an aggregate transition
    (e.g. ``surviving_certificates:decrease``). An unrelated certificate
    withhold satisfies the same aggregate. This edge-level check binds the
    verification to the specific repair edge:

    1. ``blocking_predicate`` from the edge must NOT be present in the replay's
       ``terminal_basis.blocking_facts``. If it is still blocking, the edge's
       downstream predicate did not clear — no causal credit.
    2. When ``source_artifact_id`` is present, the artifact must exist in the
       replay (it was the target of the repair, not an unrelated object).

    When no ``repair_edge`` is present (e.g. remove_rule or pre-contract edits),
    the check passes vacuously — the edge-level constraint only applies to
    add/modify candidates that selected a repair edge.
    """
    edge = (
        repair_edge if isinstance(repair_edge, Mapping)
        else {}
    )
    if not edge:
        return {
            "met": True,
            "reason": "no_repair_edge_selected",
            "blocking_predicate": "",
        }
    blocking_predicate = str(edge.get("blocking_predicate") or "")
    # 1. Downstream blocking predicate must have cleared in the replay.
    if blocking_predicate:
        replay_proof = replay_result.get("proof_adjudication") or {}
        if not isinstance(replay_proof, Mapping):
            replay_proof = replay_result.get("adjudication") or {}
        if isinstance(replay_proof, Mapping) and replay_proof:
            terminal_basis = replay_proof.get("terminal_basis") or {}
            if isinstance(terminal_basis, Mapping) and terminal_basis:
                replay_blocking = terminal_basis.get("blocking_facts") or []
                if isinstance(replay_blocking, list):
                    replay_predicates = {
                        str(item.get("predicate") or "")
                        for item in replay_blocking
                        if isinstance(item, Mapping)
                    }
                    if blocking_predicate in replay_predicates:
                        return {
                            "met": False,
                            "reason": (
                                f"blocking_predicate_still_present:"
                                f"{blocking_predicate}"
                            ),
                            "blocking_predicate": blocking_predicate,
                        }
    # 2. source_artifact_id: when present and the artifact ID is findable in the
    # baseline result, the targeted artifact should still be present in the
    # replay (it was repaired, not deleted). When the ID is not findable in
    # either result (the detection summary does not carry individual artifact
    # IDs), this check is skipped — the blocking_predicate check is the primary
    # edge-level constraint.
    source_artifact_id = str(edge.get("source_artifact_id") or "")
    if source_artifact_id and isinstance(baseline_result, Mapping):
        baseline_text = json.dumps(baseline_result, default=str)
        replay_text = json.dumps(replay_result, default=str) if isinstance(
            replay_result, Mapping
        ) else ""
        if source_artifact_id in baseline_text and source_artifact_id not in replay_text:
            return {
                "met": False,
                "reason": f"source_artifact_not_in_replay:{source_artifact_id}",
                "blocking_predicate": blocking_predicate,
            }
    return {
        "met": True,
        "reason": "edge_counterfactual_verified",
        "blocking_predicate": blocking_predicate,
    }


def _attribute_intervention_fitness(
    assessment: FitnessAssessment,
    *,
    edit: SkillEdit,
    result: Mapping[str, Any],
    baseline_surviving_ids: Sequence[str] | None = None,
    baseline_result: Mapping[str, Any] | None = None,
) -> tuple[FitnessAssessment, dict[str, Any]]:
    """Bind a replay improvement to the compiled intervention that ran.

    Aggregate detector movement is not causal evidence by itself because a
    provider replay may vary even when the intervention's transform did not
    fire.

    The compiled ActionSpec is checked deterministically as a baseline-to-replay
    transition, and the executor's ``l1_execution`` record tells whether the
    transform actually fired. There is no provider self-report to consult: the
    frozen detector runs once, then the transform fires or does not — causal
    credit requires both a fired transform and a satisfied postcondition.

    ``baseline_result`` is the full source-baseline detection result (before the
    candidate genome ran). It lets the ``withhold_certificate`` postcondition
    compare CLAIM-LEVEL carrier fingerprints (rename-invariant) instead of
    positional certificate ids, which the provider re-mints on every re-sampling
    (appeal_error_* <-> initial_error_*). ``baseline_surviving_ids`` is the
    fallback id list retained for callers without a baseline result; without any
    baseline the postcondition degrades to a strict whole-surface form that no
    single-certificate rule can satisfy in a multi-carrier case.
    """
    action_spec = sanitize_action_spec(edit.action_spec)
    operation = (
        edit.operation.value
        if isinstance(edit.operation, SkillOp)
        else str(edit.operation)
    )
    if not action_spec or operation == SkillOp.REMOVE_RULE.value:
        return assessment, {"status": "not_instrumented"}

    execution = result.get("l1_execution")
    if not isinstance(execution, Mapping):
        return assessment, {"status": "not_instrumented"}
    target_module = str(edit.target_module or "")
    target_operation = str(action_spec.get("operation") or "")
    fired_rows = [
        row for row in execution.get("fired") or ()
        if isinstance(row, Mapping)
    ]
    skipped_rows = [
        row for row in execution.get("skipped") or ()
        if isinstance(row, Mapping)
    ]

    def _is_edit(row: Mapping[str, Any]) -> bool:
        return (
            str(row.get("module_id") or "") == target_module
            and str(row.get("operation") or "") == target_operation
        )

    # Plan B: the transform fires deterministically, so "applied" is read from
    # the executor's fired record, not provider self-report. ``condition_matched``
    # is fired OR a skipped row whose condition matched but had no reachable
    # target — the distinction the old injected/applied/condition trace encoded.
    transform_fired = any(_is_edit(row) for row in fired_rows)
    condition_matched = transform_fired or any(
        _is_edit(row) and bool(row.get("matched")) for row in skipped_rows
    )
    binding_status = next((
        str(row.get("binding_status") or "")
        for row in skipped_rows if _is_edit(row) and row.get("binding_status")
    ), "BOUND" if transform_fired else "")

    postcondition_met, postcondition_detail = (
        evaluate_action_spec_postcondition(
            ActionSpec.from_dict(action_spec), result,
            baseline_surviving_ids=baseline_surviving_ids,
            baseline_result=baseline_result,
        )
    )
    edge_counterfactual = _verify_edge_level_counterfactual(
        edit.repair_edge, baseline_result, result,
    )
    edge_counterfactual_met = edge_counterfactual["met"]
    executed = transform_fired and postcondition_met and edge_counterfactual_met
    carrier_movement = (
        certificate_carrier_movement(baseline_result, result)
        if baseline_result is not None
        else None
    )

    if executed:
        execution_status = "executed"
    elif binding_status in {
        "BINDING_NOT_FOUND", "BINDING_AMBIGUOUS", "BINDING_DOMAIN_MISMATCH",
    }:
        execution_status = binding_status.lower()
    elif not sanitize_condition_spec(edit.condition_spec):
        execution_status = "not_executed"
    elif not condition_matched:
        execution_status = "condition_not_matched"
    elif not transform_fired:
        execution_status = "action_not_fired"
    else:
        execution_status = "target_edge_unchanged"

    baseline_terminal = (
        str(baseline_result.get("state") or "")
        if isinstance(baseline_result, Mapping) else ""
    )
    replay_terminal = str(result.get("state") or "") if isinstance(result, Mapping) else ""
    terminal_changed = bool(
        baseline_terminal
        and replay_terminal
        and baseline_terminal != replay_terminal
    )
    audit = {
        "status": execution_status,
        "rule_compiled": True,
        "transform_fired": transform_fired,
        "condition_matched": condition_matched,
        "binding_status": binding_status,
        "target_edge_changed": postcondition_met,
        "terminal_changed": terminal_changed,
        "baseline_terminal": baseline_terminal,
        "replay_terminal": replay_terminal,
        "target_module": target_module,
        "operation": target_operation,
        "postcondition_satisfied": postcondition_met,
        "postcondition_detail": postcondition_detail,
        "edge_counterfactual_met": edge_counterfactual_met,
        "edge_counterfactual_reason": edge_counterfactual["reason"],
        "edge_counterfactual_predicate": edge_counterfactual["blocking_predicate"],
        "baseline_surviving_ids": (
            list(baseline_surviving_ids)
            if baseline_surviving_ids is not None else None
        ),
        # Claim-level carrier movement (rename-invariant): counts only, so the
        # audit stays case-agnostic.
        "carrier_movement": (
            {
                "resolved": bool(carrier_movement.get("resolved")),
                "withheld": len(carrier_movement["withheld"]),
                "emitted": len(carrier_movement["emitted"]),
                "before": len(carrier_movement["before"]),
                "after": len(carrier_movement["after"]),
            }
            if carrier_movement is not None else None
        ),
    }
    if executed or not (assessment.passed or assessment.evidence_only):
        return assessment, audit
    # The L1 transform is deterministic, but a gold-correct terminal outcome can
    # still occur without the rule firing (a no-op transform). Causal credit
    # therefore requires the operation's postcondition AND a fired transform,
    # regardless of aggregate task movement. An unobservable postcondition is an
    # instrumentation defect to repair, not evidence of execution.
    return FitnessAssessment(
        "no_effect",
        ("intervention_not_executed",),
        assessment.task_score_before,
        assessment.task_score_after,
        assessment.local_score_before,
        assessment.local_score_after,
    ), audit



def _expected_effect_audit(
    edit: SkillEdit,
    assessment: FitnessAssessment,
    *,
    before: DetectionSignal | None = None,
    after: DetectionSignal | None = None,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    before_trigger: bool = False,
    after_trigger: bool = False,
) -> dict[str, Any]:
    """Compare the mutator's counterfactual prediction with source replay.

    When ``edit.expected_effect_contract`` is populated, the audit is driven by
    the structured contract: the predicted observable_field and direction are
    matched token-by-token against the ``observable_process_delta`` output. The
    free-text ``expected_effect`` substring matching is retained only as a
    fallback for edits produced before the contract was introduced.

    ``before_trigger`` / ``after_trigger`` are the failure-existence verdicts of
    the baseline and replay episodes (C10): they supply the
    ``trigger:closed/opened`` delta token now that ``should_evolve`` no longer
    lives on the detection signal. The trigger closes only when the cut finds
    no active failure shape, so a repairability demotion to a capability gap
    never reads as a closure.
    """
    expected = " ".join(str(edit.expected_effect or "").split())[:240]
    lowered_expected = expected.lower()
    effect_markers: dict[str, tuple[str, ...]] = {
        "candidate_trigger": ("candidate", "候选"),
        "evidence_obligation": (
            "prosecution", "evidence", "certificate", "causal field",
            "控方", "证据", "证书", "因果字段",
        ),
        "candidate_to_verifier_route": (
            "prosecution", "certificate", "route", "控方", "证书", "路由",
        ),
        "atomic_verifier_instruction": (
            "certificate", "quarantine", "violation", "actor", "boundary",
            "证书", "隔离", "违规", "责任", "边界",
        ),
        "causal_link_test": (
            "causal", "propagation", "impact", "recovery", "因果", "传播", "影响", "恢复",
        ),
        "earliest_origin_tiebreak": (
            "responsible", "origin", "earliest", "责任", "根源", "最早",
        ),
    }
    markers = effect_markers.get(edit.target_module, ())
    target_aligned = (
        None
        if not expected
        else any(marker in lowered_expected for marker in markers)
    )
    observable_deltas = (
        observable_process_delta(
            before,
            after,
            target_module=edit.target_module,
            before_result=before_result,
            after_result=after_result,
            before_trigger=before_trigger,
            after_trigger=after_trigger,
        )
        if before is not None and after is not None
        else ()
    )
    observable_delta_tokens = list(observable_deltas)
    action_spec = getattr(edit, "action_spec", None)
    spec = (
        ActionSpec.from_dict(action_spec)
        if isinstance(action_spec, Mapping) and action_spec
        else None
    )

    # ---- structured contract path ----
    contract = sanitize_expected_effect_contract(edit.expected_effect_contract)
    contract_status: str | None = None
    contract_field_observed = False
    contract_direction_observed = False
    if contract:
        cfield = str(contract.get("observable_field") or "")
        cdir = str(contract.get("expected_direction") or "")
        # Map contract field -> the prefixes observable_process_delta emits for
        # that field. This is the source-level provenance link.
        field_prefixes: dict[str, tuple[str, ...]] = {
            "candidate_count": ("candidate_count:",),
            "prosecution_coverage": ("prosecution_coverage:",),
            "defense_coverage": ("defense_coverage:",),
            "prosecution_certificates": ("prosecution_certificates:",),
            "defense_certificates": ("defense_certificates:",),
            "surviving_certificates": ("surviving_certificates:",),
            "quarantined_certificates": ("quarantined_certificates:",),
            "causal_field_coverage": ("causal_field_coverage:",),
            "responsible_step": ("responsible_step:",),
            "localization_status": ("localization_status:",),
            "target_local_score": ("target_local_score:",),
            "state": ("state:",),
            "trigger": ("trigger:",),
        }
        # Declared contract direction -> the movement tokens the delta emits.
        # The mapping is the single shared table owned by the delta emitter
        # (fitness.py), so the registry vocabulary and the delta vocabulary
        # cannot drift apart again.
        direction_tokens = CONTRACT_DIRECTION_DELTA_TOKENS
        prefixes = field_prefixes.get(cfield, ())
        dir_tokens = direction_tokens.get(cdir, ())
        for token in observable_delta_tokens:
            lowered_token = token.lower()
            # Direction is only meaningful on the contracted field's own tokens;
            # a token of another field must not satisfy it (the field may have
            # moved the opposite way).
            if not prefixes or not any(
                token.startswith(p) for p in prefixes
            ):
                continue
            contract_field_observed = True
            if dir_tokens and any(d in lowered_token for d in dir_tokens):
                contract_direction_observed = True
        # sanitize_expected_effect_contract only admits observable_field values
        # from OBSERVABLE_FIELDS, which is exactly the field_prefixes key set, so
        # ``prefixes`` is always non-empty here.
        if assessment.worsened:
            contract_status = "contradicted"
        elif not observable_delta_tokens:
            contract_status = "not_observed"
        elif contract_field_observed and contract_direction_observed:
            contract_status = "observed"
        elif contract_field_observed:
            contract_status = "field_observed_direction_mismatch"
        else:
            contract_status = "not_observed"

    # ---- free-text fallback path ----
    if not expected:
        free_text_status = "unspecified"
    elif target_aligned is False:
        free_text_status = "misaligned"
    elif assessment.worsened:
        free_text_status = "contradicted"
    elif assessment.decision == "no_effect":
        free_text_status = "not_observed"
    else:
        free_text_status = "observed"

    status = contract_status if contract else free_text_status
    # D1: deterministic execution evidence. The rule's compiled ActionSpec
    # carries an output obligation; verify it against the replay result instead
    # of relying on provider self-report. L2 edits carry no ActionSpec and land
    # in the action_spec_absent class.
    expected_output_moved = (
        contract_field_observed and contract_direction_observed
        if contract
        else bool(observable_delta_tokens)
    )
    execution_evidence = classify_execution_evidence(
        spec,
        after_result or {},
        expected_output_moved,
        baseline_result=before_result,
    )
    return {
        "expected": expected,
        "expected_effect_contract": contract,
        "status": status,
        "contract_status": contract_status,
        "free_text_status": free_text_status,
        "contract_field_observed": contract_field_observed,
        "contract_direction_observed": contract_direction_observed,
        "target_aligned": target_aligned,
        "observed_decision": assessment.decision,
        "observed_reasons": list(assessment.reasons),
        "observable_deltas": observable_delta_tokens,
        "target_output_changed": expected_output_moved,
        "local_score_before": assessment.local_score_before,
        "local_score_after": assessment.local_score_after,
        "execution_evidence": execution_evidence,
    }


def _enforce_expected_effect_observation(
    assessment: FitnessAssessment,
    effect_audit: Mapping[str, Any],
    causal_execution_status: str,
) -> FitnessAssessment:
    """Use expected-effect telemetry only as an auxiliary gradient.

    ``_attribute_intervention_fitness`` is the sole owner of causal credit: it
    requires a deterministic ActionSpec postcondition or a bound apply/effect
    trace. A generic observable contract may corroborate that decision and, in
    self-supervised mode, upgrade a genuinely executed no-local-score edit. It
    may neither veto an already executed passing edit through a proxy metric
    nor rescue an unexecuted edit whose stochastic replay happened to change
    other outputs.
    """
    gold_active = assessment.task_score_before is not None

    if gold_active:
        return assessment

    if (
        assessment.decision == "no_effect"
        and str(causal_execution_status or "") == "executed"
        and str(effect_audit.get("status") or "") == "observed"
        and bool(effect_audit.get("target_output_changed"))
    ):
        return FitnessAssessment(
            "directional_evidence",
            ("expected_effect_observed_despite_no_local_score_change",),
            assessment.task_score_before,
            assessment.task_score_after,
            assessment.local_score_before,
            assessment.local_score_after,
        )
    return assessment


def _classify_replay_no_effect(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> str:
    """Split a no-effect source replay into a deterministic cause class.

    - ``output_changed_then_quarantined``: provider output moved but the court
      quarantined it (raw/valid counts diverged).
    - ``provider_output_changed_but_court_invariant``: provider-visible counts
      moved but the court's valid bookkeeping and terminal did not.
    - ``no_observable_change_any_layer``: nothing addressable moved, i.e. the
      rule condition never matched or the provider ignored the rule.

    Only health/court-addressable counts are compared; the semantic split
    (condition vs provider obedience) stays with the reflection loop.
    """
    def health_counts(value: Mapping[str, Any] | None) -> dict[str, int]:
        value = value or {}
        health = value.get("evidence_health")
        if not isinstance(health, Mapping):
            return {}
        out: dict[str, int] = {}
        for key in (
            "construct_raw_candidate_count",
            "construct_accepted_candidate_count",
            "required_candidate_count",
            "resolved_candidate_count",
            "quarantined_error_certificate_count",
            "quarantined_defense_certificate_count",
            "focused_prosecution_certificate_count",
            "appeal_candidate_count",
        ):
            try:
                out[key] = int(health.get(key) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        return out

    def valid_counts(value: Mapping[str, Any] | None) -> dict[str, int]:
        value = value or {}
        adjudication = value.get("proof_adjudication")
        if not isinstance(adjudication, Mapping):
            return {}
        out: dict[str, int] = {}
        for key in (
            "valid_error_certificate_count",
            "valid_defense_certificate_count",
        ):
            try:
                out[key] = int(adjudication.get(key) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        return out

    before = before or {}
    after = after or {}
    health_moved = health_counts(after) != health_counts(before)
    valid_moved = valid_counts(after) != valid_counts(before)
    if not health_moved and not valid_moved:
        return "no_observable_change_any_layer"
    if health_moved and not valid_moved:
        return "provider_output_changed_but_court_invariant"
    return "output_changed_then_quarantined"


def _no_effect_reason_for(
    classification: str,
    injected: bool,
    applied: bool,
    execution_status: str = "",
) -> str:
    """Map the replay evidence to the closed no-effect vocabulary.

    The semantic split (condition vs provider obedience) is only decided when
    the execution traces show where the rule stopped; otherwise the reason
    stays ``unresolved_no_effect``.
    """
    if classification == "output_changed_then_quarantined":
        return "output_changed_then_removed_or_quarantined"
    if classification == "provider_output_changed_but_court_invariant":
        return "provider_output_changed_but_downstream_invariant"
    if execution_status == "condition_not_matched":
        return "condition_not_matched"
    if execution_status == "model_ignored_or_action_failed":
        return "provider_did_not_apply_action"
    if execution_status in {"route_not_reached", "candidate_not_injected"}:
        return execution_status
    if not injected:
        return "condition_not_matched"
    if not applied:
        return "provider_did_not_apply_action"
    return "unresolved_no_effect"


def _cross_case_no_effect_duplicate(
    *,
    target_module: str,
    new_content: str,
    entries: Sequence[Any],
    exclude_episode_id: str,
) -> bool:
    """Whether the same mechanism already confirmed this proposal no-effect.

    Reuses the existing semantic-overlap duplicate check across the causal
    memory's no-effect representatives, so a later same-mechanism case does not
    replay a proposal the framework already executed without effect. Only
    representatives of the same owning module are consulted: the mechanism
    signature's artifact slot is the L1 module id.
    """
    if not new_content or not entries:
        return False
    for entry in entries:
        if len(entry.signature) < 2 or entry.signature[1] != target_module:
            continue
        representative = entry.representatives.get("no_effect")
        if representative is None:
            continue
        if representative.episode_id == exclude_episode_id:
            continue
        if str(
            (representative.outcome or {}).get("decision") or ""
        ).startswith("accept"):
            continue
        prior_text = str(representative.rule_text or "")
        if not prior_text:
            continue
        try:
            if semantic_overlap(prior_text, new_content) >= 0.85:
                return True
        except Exception:
            continue
    return False


def build_causal_effect_record(
    *,
    edit: SkillEdit,
    assessment: FitnessAssessment,
    result: Mapping[str, Any],
    before: DetectionSignal | None = None,
    after: DetectionSignal | None = None,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    before_trigger: bool = False,
    after_trigger: bool = False,
    baseline_surviving_ids: Sequence[str] | None = None,
    baseline_result: Mapping[str, Any] | None = None,
) -> tuple[CausalEffectRecord, FitnessAssessment, dict[str, Any]]:
    """Assemble the three-channel causal-effect record for one intervention.

    Returns ``(record, enforced_assessment, intervention_audit)``. The
    intervention audit is ``_attribute_intervention_fitness``'s second element;
    the enforced assessment applies ``_enforce_expected_effect_observation`` on
    top of the intervention-adjusted assessment, so the expected-effect
    contract can upgrade a genuinely executed no-local-score edit in
    self-supervised mode without ever vetoing causal credit.
    """
    intervention_assessment, intervention_audit = _attribute_intervention_fitness(
        assessment,
        edit=edit,
        result=result,
        baseline_surviving_ids=baseline_surviving_ids,
        baseline_result=baseline_result,
    )
    effect_audit = _expected_effect_audit(
        edit,
        intervention_assessment,
        before=before,
        after=after,
        before_result=before_result,
        after_result=after_result,
        before_trigger=before_trigger,
        after_trigger=after_trigger,
    )
    enforced_assessment = _enforce_expected_effect_observation(
        intervention_assessment,
        effect_audit,
        str(intervention_audit.get("status") or ""),
    )
    no_effect_class = ""
    if enforced_assessment.decision == "no_effect":
        no_effect_class = _classify_replay_no_effect(before_result, after_result)
    record = CausalEffectRecord(
        edge_counterfactual={
            "met": intervention_audit.get("edge_counterfactual_met"),
            "reason": intervention_audit.get("edge_counterfactual_reason"),
            "blocking_predicate": intervention_audit.get(
                "edge_counterfactual_predicate"
            ),
        },
        intervention_fitness=intervention_audit,
        no_effect_class=no_effect_class,
        expected_effect_audit=effect_audit,
    )
    return record, enforced_assessment, intervention_audit


__all__ = [
    "CausalEffectRecord",
    "_attribute_intervention_fitness",
    "_baseline_surviving_error_certificate_ids",
    "_classify_replay_no_effect",
    "_cross_case_no_effect_duplicate",
    "_enforce_expected_effect_observation",
    "_expected_effect_audit",
    "_experience_intervention_trace",
    "_no_effect_reason_for",
    "_verify_edge_level_counterfactual",
    "build_causal_effect_record",
]
