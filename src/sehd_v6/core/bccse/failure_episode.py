"""Causal failure episode: the authoritative online skill-formation record.

A FailureEpisode answers, for one triggered detection: which artifact broke,
who owns it, what the provider emitted versus what the downstream court
consumed, which executable break a legal ActionSpec can repair first, and
what the framework attempted.  It is the single source of the causal slice,
the mechanism signature and the prompt-safe projection; the runner and the
reflection engine consume it instead of rebuilding their own projections.

Ownership boundaries (from the causal-experience contract):

- layer/artifact/reason/owner come only from the final attribution contract;
- the causal slice and mechanism signature are built here and nowhere else;
- legal actions come only from module_charter + ActionSpec registry;
- evaluator/gold fields never enter the prompt projection.

The paired-replay path that once fed the batch evaluator is gone; this
class is the causal episode and the single skill-formation record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .action_spec import (
    CLEAN_TO_ERROR_ACTIONS,
    ERROR_TO_CLEAN_ACTIONS,
    MODULE_OUTPUT_OBJECT,
    MODULE_ROUTE_IDS,
    compile_action_spec,
)
from .module_charter import (
    MODULE_ACTION_OPCODES,
    MODULE_OBSERVABLE_FIELDS,
    MODULE_OPERATION_TARGETS,
)
from .v6_contracts import LAYER_L1, LAYER_L2, FailureAttribution
from ..paca_tcl.audit_contract import (
    DEFENSE_CHECK_FIELDS,
    DEFENSE_RELATIONAL_INVARIANTS,
    ERROR_CHECK_FIELDS,
    ERROR_RELATIONAL_INVARIANTS,
)

SCHEMA_VERSION = "sehd.failure_episode.v1"

# Synthesized-field reason codes (Fix #2): whose executable field is canonical
# for the failure shape rather than carried on the artifact frontier. Each
# entry maps reason_code -> (owner_module, field). The quarantine reason's
# owner is overridden to evidence_obligation when dominant_reason ==
# "evidence_missing" (see ``_compile_repair_frontier``).
_SYNTHESIZED_FIELD_REASONS: dict[str, tuple[str, str]] = {
    "localization_temporal_fallback": (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    "responsible_step_missing": (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    "quarantine_pattern_dominant": (
        "atomic_verifier_instruction", "certificate_id",
    ),
}


# ============================================================
# MechanismRepairPlan (Fix #4): framework-compiled execution semantics
# ============================================================


@dataclass(frozen=True)
class MechanismRepairPlan:
    """Framework-compiled repair plan for one proof-cut edge.

    The framework compiles the execution semantics (owner / route / output
    object / output field / postcondition / legal operations) from the
    selected repair edge + evolution direction. The reflection model only
    needs to choose an edge (repair_edge_id), select an operation from the
    legal_operations enum, and propose a mechanism hypothesis (condition +
    condition_spec + evidence_anchor + feature_description).

    When ``legal_operations`` has exactly one entry, the framework
    auto-selects it (``auto_selected_operation``) and the LLM does not need
    to generate the operation field.
    """

    edge_id: str
    owner_module: str
    route: str
    output_object: str
    output_field: str
    blocking_predicate: str
    observed: str
    required: str
    legal_operations: tuple[str, ...]
    auto_selected_operation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "owner_module": self.owner_module,
            "route": self.route,
            "output_object": self.output_object,
            "output_field": self.output_field,
            "blocking_predicate": self.blocking_predicate,
            "observed": self.observed,
            "required": self.required,
            "legal_operations": list(self.legal_operations),
            "auto_selected_operation": self.auto_selected_operation,
        }


def _direction_legal_operations(
    module: str, evolution_direction: str,
) -> tuple[str, ...]:
    """Return the direction-filtered legal operation set for a module."""
    if str(evolution_direction).endswith("_to_error"):
        actions = CLEAN_TO_ERROR_ACTIONS.get(module, frozenset())
    elif str(evolution_direction).endswith("_to_clean"):
        actions = ERROR_TO_CLEAN_ACTIONS.get(module, frozenset())
    else:
        # No direction filter: all registered opcodes for the module.
        actions = frozenset(
            opcode for opcode, _ in MODULE_ACTION_OPCODES.get(module, ())
        )
    return tuple(sorted(actions))


def compile_mechanism_repair_plans(
    episode: "FailureEpisode",
    allowed_modules: Sequence[str],
    evolution_direction: str,
) -> tuple[MechanismRepairPlan, ...]:
    """Compile a MechanismRepairPlan for each executable repair edge.

    The framework compiles owner / route / output_object / output_field /
    legal_operations from the edge + direction. The reflection model only
    chooses an edge and an operation from the legal enum; it does not
    re-generate the framework-known execution vocabulary.

    Edges whose owner is not in ``allowed_modules`` or whose field is not
    executable in the given direction are skipped (they cannot compile a
    plan).
    """
    plans: list[MechanismRepairPlan] = []
    legal_module_set = set(str(value) for value in allowed_modules)
    for edge in episode.causal_slice.repair_frontier:
        owner = str(edge.owner_module)
        if owner not in legal_module_set:
            continue
        field = str(edge.field)
        legal_ops = _direction_legal_operations(owner, evolution_direction)
        # Filter to operations whose target set includes the edge's field.
        executable_ops = tuple(
            op for op in legal_ops
            if field in MODULE_OPERATION_TARGETS.get(owner, {}).get(op, ())
        )
        if not executable_ops:
            continue
        routes = MODULE_ROUTE_IDS.get(owner, ())
        auto_op = executable_ops[0] if len(executable_ops) == 1 else ""
        plans.append(MechanismRepairPlan(
            edge_id=str(edge.edge_id),
            owner_module=owner,
            route=routes[0] if routes else MODULE_OUTPUT_OBJECT.get(owner, owner),
            output_object=MODULE_OUTPUT_OBJECT.get(owner, owner),
            output_field=field,
            blocking_predicate=str(edge.blocking_predicate),
            observed=str(edge.observed),
            required=str(edge.required),
            legal_operations=executable_ops,
            auto_selected_operation=auto_op,
        ))
    return tuple(plans)


_EVIDENCE_REF_KEYS = (
    "evidence_refs", "support_refs", "boundary_refs", "origin_refs",
    "dependency_refs", "impact_evidence_refs", "consumption_evidence_refs",
    "recovery_evidence_refs", "benchmark_causal_evidence_refs",
)

EVALUATOR_ONLY_KEYS = frozenset({
    "case_id", "source_id", "gold_label", "gold_explanation",
    "is_hallucination", "hallucination_step", "hallucination_reason",
    "hallucination_category", "hallucination_subcategory",
    "outer_feedback", "gold_steps", "true_answer", "agent_answer",
})


class UnaddressableFailure(Exception):
    """A triggered case that cannot be structured into an episode.

    Raised when the authoritative attribution has no actionable layer or
    the owner has no legal executable surface.  Carries the structured
    reason; callers record it and keep the existing no-actionable path.
    """


# ============================================================
# Value types
# ============================================================

@dataclass(frozen=True)
class FailureEdge:
    producer_route: str
    output_object: str
    field: str
    consumer: str
    observed: str
    required: str
    owner_module: str = ""
    source_artifact_id: str = ""
    blocking_predicate: str = ""
    edge_id: str = ""

    def __post_init__(self) -> None:
        if not self.edge_id:
            payload = {
                "producer_route": self.producer_route,
                "output_object": self.output_object,
                "field": self.field,
                "consumer": self.consumer,
                "owner_module": self.owner_module,
                "source_artifact_id": self.source_artifact_id,
                "blocking_predicate": self.blocking_predicate,
            }
            text = json.dumps(
                payload, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"),
            )
            object.__setattr__(
                self, "edge_id",
                "edge_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_route": self.producer_route,
            "output_object": self.output_object,
            "field": self.field,
            "consumer": self.consumer,
            "observed": self.observed,
            "required": self.required,
            "owner_module": self.owner_module,
            "source_artifact_id": self.source_artifact_id,
            "blocking_predicate": self.blocking_predicate,
            "edge_id": self.edge_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEdge":
        return cls(
            producer_route=str(value.get("producer_route") or ""),
            output_object=str(value.get("output_object") or ""),
            field=str(value.get("field") or ""),
            consumer=str(value.get("consumer") or ""),
            observed=str(value.get("observed") or ""),
            required=str(value.get("required") or ""),
            owner_module=str(value.get("owner_module") or ""),
            source_artifact_id=str(value.get("source_artifact_id") or ""),
            blocking_predicate=str(value.get("blocking_predicate") or ""),
            edge_id=str(value.get("edge_id") or ""),
        )


@dataclass(frozen=True)
class CausalSlice:
    candidate_id: str
    candidate_kind: str
    origin_refs: tuple[str, ...]
    dependency_refs: tuple[str, ...]
    required_fields: tuple[str, ...]
    required_evidence_refs: tuple[str, ...]
    present_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    malformed_fields: tuple[str, ...]
    consumer: str
    qualification_state: str
    certificate_state: str
    quarantine_state: str
    coverage_state: str
    court_effect: str
    failure_edge: FailureEdge | None
    evidence_refs: tuple[str, ...]
    completeness: str
    repair_frontier: tuple[FailureEdge, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "origin_refs": list(self.origin_refs),
            "dependency_refs": list(self.dependency_refs),
            "required_fields": list(self.required_fields),
            "required_evidence_refs": list(self.required_evidence_refs),
            "present_fields": list(self.present_fields),
            "missing_fields": list(self.missing_fields),
            "malformed_fields": list(self.malformed_fields),
            "consumer": self.consumer,
            "qualification_state": self.qualification_state,
            "certificate_state": self.certificate_state,
            "quarantine_state": self.quarantine_state,
            "coverage_state": self.coverage_state,
            "court_effect": self.court_effect,
            "failure_edge": (
                self.failure_edge.to_dict()
                if self.failure_edge is not None else None
            ),
            "repair_frontier": [
                item.to_dict() for item in self.repair_frontier
            ],
            "evidence_refs": list(self.evidence_refs),
            "completeness": self.completeness,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CausalSlice":
        edge = value.get("failure_edge")
        raw_frontier = value.get("repair_frontier")
        frontier = tuple(
            FailureEdge.from_dict(dict(item))
            for item in raw_frontier or ()
            if isinstance(item, Mapping)
        )
        legacy_edge = (
            FailureEdge.from_dict(dict(edge))
            if isinstance(edge, Mapping) else None
        )
        if not frontier and legacy_edge is not None:
            frontier = (legacy_edge,)
        return cls(
            candidate_id=str(value.get("candidate_id") or ""),
            candidate_kind=str(value.get("candidate_kind") or ""),
            origin_refs=tuple(str(item) for item in value.get("origin_refs") or ()),
            dependency_refs=tuple(
                str(item) for item in value.get("dependency_refs") or ()
            ),
            required_fields=tuple(
                str(item) for item in value.get("required_fields") or ()
            ),
            required_evidence_refs=tuple(
                str(item) for item in value.get("required_evidence_refs") or ()
            ),
            present_fields=tuple(
                str(item) for item in value.get("present_fields") or ()
            ),
            missing_fields=tuple(
                str(item) for item in value.get("missing_fields") or ()
            ),
            malformed_fields=tuple(
                str(item) for item in value.get("malformed_fields") or ()
            ),
            consumer=str(value.get("consumer") or ""),
            qualification_state=str(value.get("qualification_state") or ""),
            certificate_state=str(value.get("certificate_state") or ""),
            quarantine_state=str(value.get("quarantine_state") or ""),
            coverage_state=str(value.get("coverage_state") or ""),
            court_effect=str(value.get("court_effect") or ""),
            failure_edge=legacy_edge or (frontier[0] if frontier else None),
            evidence_refs=tuple(
                str(item) for item in value.get("evidence_refs") or ()
            ),
            completeness=str(value.get("completeness") or "partial"),
            repair_frontier=frontier,
        )


@dataclass(frozen=True)
class ActionEnvelope:
    legal_modules: tuple[str, ...]
    legal_operations: tuple[str, ...]
    legal_output_fields: tuple[str, ...]
    legal_observables: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "legal_modules": list(self.legal_modules),
            "legal_operations": list(self.legal_operations),
            "legal_output_fields": list(self.legal_output_fields),
            "legal_observables": list(self.legal_observables),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionEnvelope":
        return cls(
            legal_modules=tuple(
                str(item) for item in value.get("legal_modules") or ()
            ),
            legal_operations=tuple(
                str(item) for item in value.get("legal_operations") or ()
            ),
            legal_output_fields=tuple(
                str(item) for item in value.get("legal_output_fields") or ()
            ),
            legal_observables=tuple(
                str(item) for item in value.get("legal_observables") or ()
            ),
        )


@dataclass(frozen=True)
class FailureAttempt:
    attempt_id: str
    proposal_signature: str
    operation: str
    target_module: str
    action_spec: dict[str, Any]
    structural_result: str
    compiled: bool
    injected: bool
    applied: bool
    observable_delta: tuple[str, ...]
    downstream_court_delta: tuple[str, ...]
    no_effect_reason: str
    decision: str
    proposal_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "proposal_signature": self.proposal_signature,
            "operation": self.operation,
            "target_module": self.target_module,
            "action_spec": self.action_spec,
            "structural_result": self.structural_result,
            "compiled": self.compiled,
            "injected": self.injected,
            "applied": self.applied,
            "observable_delta": list(self.observable_delta),
            "downstream_court_delta": list(self.downstream_court_delta),
            "no_effect_reason": self.no_effect_reason,
            "decision": self.decision,
            "proposal_content": self.proposal_content,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureAttempt":
        return cls(
            attempt_id=str(value.get("attempt_id") or ""),
            proposal_signature=str(value.get("proposal_signature") or ""),
            operation=str(value.get("operation") or ""),
            target_module=str(value.get("target_module") or ""),
            action_spec=dict(value.get("action_spec") or {}),
            structural_result=str(value.get("structural_result") or ""),
            compiled=bool(value.get("compiled")),
            injected=bool(value.get("injected")),
            applied=bool(value.get("applied")),
            observable_delta=tuple(
                str(item) for item in value.get("observable_delta") or ()
            ),
            downstream_court_delta=tuple(
                str(item) for item in value.get("downstream_court_delta") or ()
            ),
            no_effect_reason=str(value.get("no_effect_reason") or ""),
            decision=str(value.get("decision") or ""),
            proposal_content=str(value.get("proposal_content") or ""),
        )


@dataclass(frozen=True)
class FailureEpisode:
    schema_version: str
    episode_id: str
    record_key: str
    detection: dict[str, str]
    attribution: dict[str, str]
    causal_slice: CausalSlice
    action_envelope: ActionEnvelope
    attempts: tuple[FailureAttempt, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "record_key": self.record_key,
            "detection": dict(self.detection),
            "attribution": dict(self.attribution),
            "causal_slice": self.causal_slice.to_dict(),
            "action_envelope": self.action_envelope.to_dict(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEpisode":
        return cls(
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            episode_id=str(value.get("episode_id") or ""),
            record_key=str(value.get("record_key") or ""),
            detection={
                str(key): str(item)
                for key, item in dict(value.get("detection") or {}).items()
            },
            attribution={
                str(key): str(item)
                for key, item in dict(value.get("attribution") or {}).items()
            },
            causal_slice=CausalSlice.from_dict(
                dict(value.get("causal_slice") or {})
            ),
            action_envelope=ActionEnvelope.from_dict(
                dict(value.get("action_envelope") or {})
            ),
            attempts=tuple(
                FailureAttempt.from_dict(dict(item))
                for item in value.get("attempts") or ()
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class HistoricalEpisodeReference:
    episode_id: str
    mechanism_signature: tuple[str, ...]
    shared_facts: dict[str, Any]
    current_only_facts: dict[str, Any]
    historical_only_facts: dict[str, Any]
    rule_condition: str
    action_spec: dict[str, Any] | None
    local_outcome: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "mechanism_signature": list(self.mechanism_signature),
            "shared_facts": dict(self.shared_facts),
            "current_only_facts": dict(self.current_only_facts),
            "historical_only_facts": dict(self.historical_only_facts),
            "rule_condition": self.rule_condition,
            "action_spec": dict(self.action_spec or {}),
            "local_outcome": dict(self.local_outcome or {}),
        }


# ============================================================
# Canonical serialization and stable identity
# ============================================================

def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )


def _stable_identity(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sorted_dedup(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def _enum_value(value: Any) -> str:
    """Resolve an enum member to its string value defensively."""
    if value is None:
        return ""
    return getattr(value, "value", str(value))


# ============================================================
# Projections (single owner of artifact/court projections)
# ============================================================

def project_provider_artifacts(
    *,
    detection_result: Mapping[str, Any],
    signal: Any,
    primary_type: Any,
) -> list[dict[str, Any]]:
    """Project every addressable provider-artifact integrity signal into
    the failure classifier's input contract.

    Kinds: ``candidate`` / ``error_certificate`` / ``defense_certificate``,
    each with ``malformed`` and ``missing_fields`` so the classifier
    resolves the owning L1 module deterministically.  Only label-free
    runtime evidence is read; the single gold-informed signal is the
    FN+omission candidate absence, which only a label can expose.
    """
    artifacts: list[dict[str, Any]] = []
    adjudication = detection_result.get("proof_adjudication") or {}
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    health = detection_result.get("evidence_health") or {}
    if not isinstance(health, Mapping):
        health = {}

    def add(
        kind: str,
        *,
        malformed: bool,
        missing: list[str],
        failed_fields: Sequence[str] = (),
        failed_invariants: Sequence[str] = (),
        artifact_id: str = "",
        source: str = "",
    ) -> None:
        artifact = {
            "kind": kind,
            "module_hint": "",
            "malformed": malformed,
            "missing_fields": missing,
        }
        normalized_failed_fields = sorted({
            str(name) for name in failed_fields if str(name)
        })
        normalized_failed_invariants = sorted({
            str(name) for name in failed_invariants if str(name)
        })
        # Additive typed evidence is emitted only when present.  This keeps
        # legacy artifact projections byte-compatible without sacrificing the
        # new distinction for relational failures.
        if normalized_failed_fields:
            artifact["failed_fields"] = normalized_failed_fields
        if normalized_failed_invariants:
            artifact["failed_invariants"] = normalized_failed_invariants
        if artifact_id:
            artifact["artifact_id"] = artifact_id
        if source:
            artifact["source"] = source
        artifacts.append(artifact)

    gold_label = getattr(signal, "gold_label", None)
    state = str(
        getattr(signal, "state", "") or detection_result.get("state") or ""
    )
    type_value = str(getattr(primary_type, "value", primary_type) or "")
    if bool(gold_label) and state == "verified_clean" and type_value == "omission":
        add("candidate", malformed=False, missing=["candidate"])

    try:
        required = int(health.get("required_candidate_count") or 0)
        accepted = int(health.get("construct_accepted_candidate_count") or 0)
        raw = int(health.get("construct_raw_candidate_count") or 0)
    except (TypeError, ValueError):
        required = accepted = raw = 0
    if required > 0 and accepted == 0:
        if raw > 0:
            add("candidate", malformed=True, missing=[])
        else:
            add("candidate", malformed=False, missing=["candidate"])

    authority_reasons = health.get("construct_authority_failure_reasons")
    if isinstance(authority_reasons, list) and authority_reasons:
        add("candidate", malformed=True, missing=[])

    # Quarantine totals are containment telemetry, not an addressable defect.
    # A quarantined object has already been removed from the proof court; its
    # count alone neither identifies the broken field nor proves that the
    # quarantine caused the terminal failure.  Only the per-object audits
    # below carry enough provenance to create an executable L1 episode.
    for audit_key, kind in (
        ("error_audits", "error_certificate"),
        ("defense_audits", "defense_certificate"),
    ):
        audits = adjudication.get(audit_key)
        if not isinstance(audits, list):
            continue
        for audit in audits:
            if not isinstance(audit, Mapping):
                continue
            if str(audit.get("decision") or "") == "pass":
                continue
            failed_fields = audit.get("failed_fields")
            missing_from_audit = audit.get("missing_fields")
            failed_invariants = audit.get("failed_invariants")
            # Single migration boundary for pre-contract cached detections:
            # old audits carried only predicate labels.  Re-project them via
            # the same typed contract rather than reviving the old error of
            # treating check names as JSON fields.
            if not isinstance(failed_fields, list):
                legacy_checks = audit.get("failed_checks")
                if isinstance(legacy_checks, list):
                    check_fields = (
                        ERROR_CHECK_FIELDS
                        if kind == "error_certificate" else DEFENSE_CHECK_FIELDS
                    )
                    relational = (
                        ERROR_RELATIONAL_INVARIANTS
                        if kind == "error_certificate"
                        else DEFENSE_RELATIONAL_INVARIANTS
                    )
                    semantic_fields = sorted({
                        str(field)
                        for check in legacy_checks
                        for field in check_fields.get(str(check), ())
                    })
                    registered_fields = {
                        str(field)
                        for fields in check_fields.values()
                        for field in fields
                    }
                    direct_fields = sorted({
                        str(check) for check in legacy_checks
                        if str(check) in registered_fields
                    })
                    failed_fields = semantic_fields
                    failed_invariants = sorted({
                        str(check) for check in legacy_checks
                        if str(check) in relational
                    })
                    artifact_id = str(
                        audit.get("certificate_id")
                        or audit.get("candidate_id")
                        or ""
                    )
                    row = next(
                        (
                            item for item in _candidate_rows(detection_result, kind)
                            if str(
                                item.get("certificate_id")
                                or item.get("candidate_id")
                                or ""
                            ) == artifact_id
                        ),
                        {},
                    )
                    # Some pre-contract audits used provider field names
                    # directly as checks.  Accept only registered fields, and
                    # call them missing only when the addressed row proves it.
                    missing_from_audit = [
                        field for field in direct_fields
                        if row.get(field) in (None, "", [], {})
                    ]
            if isinstance(failed_fields, list):
                participating_fields = sorted({
                    str(name) for name in failed_fields if str(name)
                })
                missing_fields = (
                    sorted({
                        str(name) for name in missing_from_audit if str(name)
                    })
                    if isinstance(missing_from_audit, list) else []
                )
                invariants = (
                    sorted({str(name) for name in failed_invariants if str(name)})
                    if isinstance(failed_invariants, list) else []
                )
                if missing_fields or participating_fields or invariants:
                    add(
                        kind,
                        malformed=True,
                        missing=missing_fields,
                        failed_fields=participating_fields,
                        failed_invariants=invariants,
                        artifact_id=str(
                            audit.get("certificate_id")
                            or audit.get("candidate_id")
                            or ""
                        ),
                        source=audit_key,
                    )

    if str(health.get("appeal_status") or "") == "provider_error":
        add("error_certificate", malformed=True, missing=[])

    try:
        court_required = int(adjudication.get("required_candidate_count") or 0)
    except (TypeError, ValueError):
        court_required = 0
    try:
        valid_error = int(adjudication.get("valid_error_certificate_count") or 0)
    except (TypeError, ValueError):
        valid_error = 0
    try:
        valid_defense = int(adjudication.get("valid_defense_certificate_count") or 0)
    except (TypeError, ValueError):
        valid_defense = 0
    surviving_ids = adjudication.get("surviving_error_certificate_ids")
    surviving_count = len(surviving_ids) if isinstance(surviving_ids, list) else 0
    prosecution_state = str(
        getattr(signal, "prosecution_coverage_state", "")
        or adjudication.get("prosecution_coverage") or ""
    )
    defense_state = str(
        getattr(signal, "defense_coverage_state", "")
        or adjudication.get("defense_coverage") or ""
    )
    error_type = str(
        getattr(getattr(signal, "error_type", ""), "value", "")
        or getattr(signal, "error_type", "")
        or ""
    )
    hollow_defense = defense_state == "complete" and valid_defense == 0
    hollow_prosecution = prosecution_state == "complete" and valid_error == 0
    polarity_known = error_type in {
        "unresolved_should_error", "unresolved_should_clean",
    }
    requires_error_certificate = (
        error_type == "unresolved_should_error"
        or (not polarity_known and defense_state == "complete")
    )
    requires_defense_certificate = (
        error_type == "unresolved_should_clean"
        or (not polarity_known and prosecution_state == "complete")
    )
    if (
        requires_error_certificate
        and state == "unresolved"
        and prosecution_state == "incomplete"
        and valid_error == 0
        and surviving_count == 0
        and court_required > 0
        and int(getattr(signal, "candidate_count", 0) or 0) > 0
        and not hollow_defense
    ):
        add("error_certificate", malformed=False, missing=["certificate"])

    if (
        requires_defense_certificate
        and state == "unresolved"
        and defense_state == "incomplete"
        and valid_defense == 0
        and court_required > 0
        and int(getattr(signal, "candidate_count", 0) or 0) > 0
        and not hollow_prosecution
    ):
        add("defense_certificate", malformed=False, missing=["certificate"])

    return artifacts


def build_runtime_observation(
    detection_result: Mapping[str, Any],
    signal: Any,
    *,
    allowed_modules: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the bounded structured observation the reflection model sees.

    Derived from detector output only: no labels, no case identity.  It is
    an observation, not a verdict or a correctness claim.
    """
    def rows(name: str) -> list[Mapping[str, Any]]:
        payload = detection_result.get(name) or {}
        if not isinstance(payload, Mapping):
            return []
        value = payload.get("candidates") or payload.get("certificates") or []
        return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    prosecution = rows("combined_prosecution") or rows("prosecution")
    defense = rows("combined_defense") or rows("defense")
    proof = detection_result.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        proof = {}
    health = detection_result.get("evidence_health") or {}
    if not isinstance(health, Mapping):
        health = {}

    def _id_count(key: str) -> int:
        value = proof.get(key)
        return len(value) if isinstance(value, (list, tuple)) else 0

    def _proof_count(key: str, fallback: int) -> int:
        if key not in proof:
            return fallback
        try:
            return max(0, int(proof.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    surviving_ids = proof.get("surviving_error_certificate_ids")
    surviving_id_set = (
        {str(value) for value in surviving_ids}
        if isinstance(surviving_ids, (list, tuple))
        else set()
    )
    surviving_error_certificates = (
        len(surviving_id_set)
        if isinstance(surviving_ids, (list, tuple))
        else max(len(prosecution) - _id_count("quarantined_error_certificate_ids"), 0)
    )

    # Verdict-carrier projection (first-principles fix C): the proof court
    # already names the certificate(s) that actually carry the verdict
    # (``surviving_error_certificate_ids`` for an error verdict; the
    # ``winning_error_certificate_id`` fields for both appeal and construct
    # adjudication). The reflection LLM previously saw only an aggregate
    # count ``surviving_error_certificates=N`` and routinely targeted a
    # non-carrier witness certificate, producing an edit that the verifier
    # faithfully applied but that left the verdict unchanged
    # (``intervention_not_executed`` because another carrier survived).
    # Surfacing the carrier identity forces reflection to target a
    # certificate that, once withheld, actually moves the verdict.
    #
    # Witness certificates identify non-surviving prosecution rows so the
    # LLM understands which evidence the court already disregarded and is
    # therefore inert to withhold.
    def _carrier_descriptor(row: Mapping[str, Any]) -> dict[str, Any]:
        candidate_id = str(row.get("source_candidate_id") or "")
        census_row = next(
            (
                entry for entry in (detection_result.get("census") or [])
                if isinstance(entry, Mapping)
                and str(entry.get("candidate_id") or "") == candidate_id
            ),
            None,
        )
        return {
            "certificate_id": str(row.get("certificate_id") or ""),
            "source_candidate_id": candidate_id,
            "breach_type": str(row.get("breach_type") or ""),
            "claim_kind": str(row.get("claim_kind") or ""),
            "candidate_kind": str(
                (census_row or {}).get("candidate_kind") or ""
            ),
            "origin_event_id": str(row.get("origin_event_id") or ""),
        }

    verdict_state = detection_result.get("state")
    winning_error_certificate_id = str(
        proof.get("winning_error_certificate_id") or ""
    )
    verdict_carriers: list[dict[str, Any]] = []
    witness_certificates: list[dict[str, Any]] = []
    if verdict_state == "verified_error":
        for row in prosecution:
            certificate_id = str(row.get("certificate_id") or "")
            if not certificate_id:
                continue
            descriptor = _carrier_descriptor(row)
            if certificate_id in surviving_id_set:
                verdict_carriers.append(descriptor)
            else:
                witness_certificates.append(descriptor)
        # The court may report a winning certificate id that is not in the
        # combined list (e.g. when only the focused or appeal surface carried
        # it); fall back to a minimal name-only descriptor so the prompt
        # still identifies the carrier.
        if (
            winning_error_certificate_id
            and not any(
                str(carrier.get("certificate_id") or "")
                == winning_error_certificate_id
                for carrier in verdict_carriers
            )
        ):
            verdict_carriers.append({
                "certificate_id": winning_error_certificate_id,
                "source_candidate_id": "",
                "breach_type": "",
                "claim_kind": "",
                "candidate_kind": "",
                "origin_event_id": "",
            })

    court_balance = {
        "surviving_error_certificates": max(
            surviving_error_certificates,
            0,
        ),
        "quarantined_error_certificates": (
            int(health.get("quarantined_error_certificate_count"))
            if isinstance(health.get("quarantined_error_certificate_count"), int)
            else _id_count("quarantined_error_certificate_ids")
        ),
        "defense_certificates": _proof_count(
            "valid_defense_certificate_count",
            max(len(defense) - _id_count("quarantined_defense_certificate_ids"), 0),
        ),
        "candidates_covered_by_clean_defense": _id_count(
            "clean_covered_candidate_ids"
        ),
        "positive_candidates": _id_count("positive_candidate_ids"),
        "consensus_gate_blocked": _id_count("consensus_gate_blocked_ids"),
        "verdict_state": verdict_state,
        "verdict_carriers": verdict_carriers,
        "witness_certificates": witness_certificates,
    }
    raw_terminal_basis = proof.get("terminal_basis")
    terminal_basis = (
        {
            "state": str(raw_terminal_basis.get("state") or ""),
            "decisive_facts": dict(
                raw_terminal_basis.get("decisive_facts") or {}
            ),
            "blocking_facts": [
                dict(item)
                for item in raw_terminal_basis.get("blocking_facts") or ()
                if isinstance(item, Mapping)
            ][:8],
        }
        if isinstance(raw_terminal_basis, Mapping) else {}
    )
    field_presence = {
        "candidate_count": len(detection_result.get("census") or []),
        "prosecution_certificates": len(prosecution),
        "defense_certificates": len(defense),
        "responsible_step": detection_result.get("responsible_step") is not None,
        "prosecution_coverage": proof.get("prosecution_coverage"),
        "defense_coverage": proof.get("defense_coverage"),
        "state": detection_result.get("state"),
        "trigger": _enum_value(signal.trigger_source),
    }
    owner_fields = {
        module: list(MODULE_OBSERVABLE_FIELDS.get(module, ()))
        for module in allowed_modules
    }
    return {
        "authority": {
            "runtime_valid": detection_result.get("runtime_valid") is True,
            "decision_authority": detection_result.get("decision_authority"),
        },
        "observed_fields": field_presence,
        "court_balance": court_balance,
        "terminal_basis": terminal_basis,
        "runtime_condition_features": {
            str(route): [dict(item) for item in rows[:8] if isinstance(item, Mapping)]
            for route, rows in dict(
                detection_result.get("condition_feature_trace") or {}
            ).items()
            if isinstance(rows, list)
        },
        "non_empty_evidence": {
            "census": field_presence["candidate_count"] > 0,
            "prosecution": bool(prosecution),
            "defense": bool(defense),
        },
        "diagnostic_gap": {
            "trigger": _enum_value(signal.trigger_source),
            "error_type": _enum_value(signal.error_type),
            "primary_module": signal.target_module,
            "secondary_module": signal.secondary_module,
            "confidence": signal.attribution_confidence,
        },
        "module_output_ownership": owner_fields,
        "instruction": (
            "Select one observed missing or malformed output field owned by "
            "the target module. terminal_basis is the proof court's exact "
            "decision fact set; select a failure that participates in one of "
            "its blocking facts instead of guessing from aggregate counts. "
            "Do not claim an effect on fields outside its "
            "ownership. diagnostic_gap primary/secondary modules are "
            "attribution hints; the allowed module set is authoritative. "
            "When court_balance.verdict_carriers is non-empty, an "
            "error_to_clean (FP / unresolved_should_clean) edit MUST move "
            "at least one listed carrier; witholding only a witness "
            "certificate in court_balance.witness_certificates has zero "
            "observable verdict effect and is rejected as no-effect."
        ),
    }


# ============================================================
# Mechanism signature
# ============================================================

def build_mechanism_signature(
    *,
    layer: str,
    artifact: str,
    reason_code: str,
    evolution_direction: str,
    candidate_kind: str,
    failure_edge_consumer: str,
    missing_or_malformed_fields: Sequence[str],
) -> tuple[str, ...]:
    """Structural cross-case retrieval key.

    All values come from closed or normalized fields; no case identity,
    natural-language rationale, model name or time enters the key.
    """
    return (
        str(layer or ""),
        str(artifact or ""),
        str(reason_code or ""),
        str(evolution_direction or ""),
        str(candidate_kind or ""),
        str(failure_edge_consumer or ""),
        *tuple(sorted({str(value) for value in missing_or_malformed_fields if str(value)})),
    )


# ============================================================
# Episode builder
# ============================================================

def _candidate_rows(
    detection_result: Mapping[str, Any],
    kind: str,
) -> list[Mapping[str, Any]]:
    if kind == "candidate":
        rows = detection_result.get("census") or []
        return [row for row in rows if isinstance(row, Mapping)]
    if kind == "error_certificate":
        payload = (
            detection_result.get("combined_prosecution")
            or detection_result.get("prosecution")
        )
    else:
        payload = (
            detection_result.get("combined_defense")
            or detection_result.get("defense")
        )
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("candidates") or payload.get("certificates") or []
    return [row for row in rows if isinstance(row, Mapping)]


def _row_refs(row: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _select_candidate(
    *,
    detection_result: Mapping[str, Any],
    attribution: FailureAttribution,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Pick the responsible candidate deterministically.

    Priority: the court/attribution-referenced candidate first, then the
    provider artifact kind the attribution points at, then the earliest
    census candidate.  Returns
    ``(candidate_id, kind, origin_refs, dependency_refs)``.
    """
    court = detection_result.get("proof_adjudication") or {}
    if not isinstance(court, Mapping):
        court = {}
    evidence = attribution.evidence if isinstance(attribution.evidence, Mapping) else {}
    kind = str(evidence.get("kind") or "")
    artifact_id = str(evidence.get("artifact_id") or "")
    if kind and artifact_id:
        for row in _candidate_rows(detection_result, kind):
            row_id = str(row.get("candidate_id") or row.get("certificate_id") or "")
            if row_id == artifact_id:
                return (
                    row_id,
                    kind,
                    _row_refs(row, "origin_refs")
                    or _row_refs(row, "origin_event_id")
                    or _row_refs(row, "target_origin_event_id"),
                    _row_refs(row, "dependency_refs")
                    or _row_refs(row, "dependencies"),
                )

    positive_ids = court.get("positive_candidate_ids") or []
    if isinstance(positive_ids, (set, frozenset)):
        positive_ids = sorted(str(value) for value in positive_ids)
    for candidate_id in positive_ids:
        row = next(
            (
                row
                for row in _candidate_rows(detection_result, "candidate")
                if str(row.get("candidate_id") or "") == str(candidate_id)
            ),
            None,
        )
        if row is not None:
            return (
                str(candidate_id),
                "candidate",
                _row_refs(row, "origin_refs"),
                _row_refs(row, "dependency_refs") or _row_refs(row, "dependencies"),
            )
        continue
    rows = _candidate_rows(detection_result, kind) if kind else []
    if rows:
        row = rows[0]
        row_kind = (
            "candidate"
            if kind == "candidate"
            else (
                "error_certificate"
                if kind == "error_certificate"
                else "defense_certificate"
            )
        )
        return (
            str(row.get("candidate_id") or row.get("certificate_id") or ""),
            row_kind,
            _row_refs(row, "origin_refs"),
            _row_refs(row, "dependency_refs") or _row_refs(row, "dependencies"),
        )
    census = _candidate_rows(detection_result, "candidate")
    if census:
        row = census[0]
        return (
            str(row.get("candidate_id") or ""),
            "candidate",
            _row_refs(row, "origin_refs"),
            _row_refs(row, "dependency_refs") or _row_refs(row, "dependencies"),
        )
    return "", "unknown", (), ()


def _present_fields(row: Mapping[str, Any]) -> tuple[str, ...]:
    skip = frozenset({"candidate_id", "certificate_id", "module_hint"})
    return _sorted_dedup(
        key for key in row if key not in skip and row.get(key) not in (None, "", [])
    )[:8]


def _evidence_refs(
    detection_result: Mapping[str, Any],
    responsible_step: Any,
) -> tuple[str, ...]:
    refs: set[str] = set()
    for row in _candidate_rows(detection_result, "candidate"):
        refs.update(_row_refs(row, "origin_refs"))
    for kind in ("error_certificate", "defense_certificate"):
        for row in _candidate_rows(detection_result, kind):
            for key in _EVIDENCE_REF_KEYS:
                refs.update(_row_refs(row, key))
            if row.get("certificate_id"):
                refs.add(f"certificate:{row['certificate_id']}")
    if responsible_step:
        refs.add(f"step:{responsible_step}")
    return _sorted_dedup(refs)[:12]


def _downstream_consumption(
    *,
    detection_result: Mapping[str, Any],
    layer: str,
) -> dict[str, str]:
    court = detection_result.get("proof_adjudication") or {}
    if not isinstance(court, Mapping):
        court = {}
    health = detection_result.get("evidence_health") or {}
    if not isinstance(health, Mapping):
        health = {}

    def count(key: str) -> int:
        try:
            return max(0, int(court.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    valid_errors = count("valid_error_certificate_count")
    valid_defenses = count("valid_defense_certificate_count")
    surviving = court.get("surviving_error_certificate_ids")
    surviving_count = len(surviving) if isinstance(surviving, (list, tuple)) else 0
    quarantine = {
        "error": (
            int(health.get("quarantined_error_certificate_count") or 0)
            if isinstance(health.get("quarantined_error_certificate_count"), int)
            else 0
        ),
        "defense": (
            int(health.get("quarantined_defense_certificate_count") or 0)
            if isinstance(health.get("quarantined_defense_certificate_count"), int)
            else 0
        ),
    }
    coverage = (
        f"{court.get('prosecution_coverage') or 'none'}/"
        f"{court.get('defense_coverage') or 'none'}"
    )
    terminal = str(detection_result.get("state") or court.get("state") or "")
    if layer == LAYER_L2:
        effect = (
            "policy_fired_moved_terminal"
            if isinstance(court.get("policy_evaluation"), Mapping)
            and court["policy_evaluation"].get("fired")
            else "policy_did_not_fire"
        )
        consumer = "adjudication_policy"
    else:
        if terminal == "unresolved" and (valid_errors > 0 or surviving_count > 0):
            effect = "provider_output_unconsumed"
        else:
            effect = "terminal_unchanged"
        consumer = "proof_court"
    return {
        "consumer": consumer,
        "qualification_state": terminal,
        "certificate_state": (
            f"valid_error:{valid_errors},valid_defense:{valid_defenses}"
        ),
        "quarantine_state": f"error:{quarantine['error']},defense:{quarantine['defense']}",
        "coverage_state": coverage,
        "court_effect": effect,
    }


def _direction_operations(
    *,
    module: str,
    evolution_direction: str,
) -> frozenset[str]:
    if str(evolution_direction).endswith("_to_error"):
        return CLEAN_TO_ERROR_ACTIONS.get(module, frozenset())
    if str(evolution_direction).endswith("_to_clean"):
        return ERROR_TO_CLEAN_ACTIONS.get(module, frozenset())
    return frozenset(MODULE_OPERATION_TARGETS.get(module, {}))


def _field_is_executable(
    *, module: str, field: str, evolution_direction: str,
) -> bool:
    operations = _direction_operations(
        module=module, evolution_direction=evolution_direction,
    )
    return any(
        operation in operations and field in targets
        for operation, targets in MODULE_OPERATION_TARGETS.get(module, {}).items()
    )


def _terminal_blocking_predicates(
    detection_result: Mapping[str, Any],
) -> tuple[str, ...]:
    proof = detection_result.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        proof = detection_result.get("adjudication")
    if not isinstance(proof, Mapping):
        return ()
    terminal_basis = proof.get("terminal_basis")
    if not isinstance(terminal_basis, Mapping):
        return ()
    return tuple(
        str(item.get("predicate") or "")
        for item in terminal_basis.get("blocking_facts") or ()
        if isinstance(item, Mapping) and str(item.get("predicate") or "")
    )


def _artifact_blocking_predicate(
    kind: str, blocking_predicates: Sequence[str],
) -> str:
    preferred = {
        "candidate": "required_candidates_terminal",
        "error_certificate": "prosecution_coverage_complete",
        "defense_certificate": "defense_coverage_complete",
    }.get(kind, "")
    if preferred in blocking_predicates:
        return preferred
    return "provider_artifact_integrity"


def _quarantine_audit_fields(
    detection_result: Mapping[str, Any], owner: str,
) -> list[str]:
    """Extract executable provider fields from the quarantined audit(s) in the
    detection result, mapped through the registered check->field contract.

    Returns the participating provider fields for the first non-pass audit
    whose failed_checks map to registered fields for the given owner's side
    (error vs defense). Empty when no audit is available — the caller then
    falls back to the canonical synthesized field.
    """
    proof = detection_result.get("proof_adjudication")
    if not isinstance(proof, Mapping) or not proof:
        proof = detection_result.get("adjudication")
    if not isinstance(proof, Mapping) or not proof:
        return []
    is_error_side = owner in (
        "atomic_verifier_instruction", "evidence_obligation",
        "candidate_trigger", "candidate_to_verifier_route",
    )
    check_fields = ERROR_CHECK_FIELDS if is_error_side else DEFENSE_CHECK_FIELDS
    for audit_key in ("error_audits", "defense_audits"):
        audits = proof.get(audit_key)
        if not isinstance(audits, list):
            continue
        for audit in audits:
            if not isinstance(audit, Mapping):
                continue
            if str(audit.get("decision") or "") == "pass":
                continue
            failed_checks = audit.get("failed_checks")
            if not isinstance(failed_checks, (list, tuple)):
                continue
            fields: list[str] = []
            for check in failed_checks:
                participating = check_fields.get(str(check), ())
                fields.extend(str(name) for name in participating)
            if fields:
                return sorted(set(fields))
    return []


def _compile_repair_frontier(
    *,
    layer: str,
    artifact: str,
    reason_code: str,
    evidence: Mapping[str, Any],
    detection_result: Mapping[str, Any],
    downstream: Mapping[str, str],
    legal_modules: Sequence[str],
    evolution_direction: str,
) -> tuple[tuple[FailureEdge, ...], tuple[str, ...]]:
    """Compile all executable edges participating in the terminal proof cut.

    The result is independent of provider-artifact list order. Each edge owns
    its producer module and stable id, so a proposal must explicitly choose
    one edge instead of inheriting the classifier's first match.
    """
    consumer = str(downstream.get("consumer") or "proof_court")
    if layer == LAYER_L2:
        edge = FailureEdge(
            producer_route="policy_evaluator",
            output_object=artifact,
            field=reason_code,
            consumer=consumer,
            observed=str(downstream.get("qualification_state") or "unresolved"),
            required="not_blocked",
            blocking_predicate=reason_code,
        )
        return (edge,), ()

    raw_frontier = evidence.get("artifact_frontier")
    rows = (
        [dict(item) for item in raw_frontier if isinstance(item, Mapping)]
        if isinstance(raw_frontier, list) else []
    )
    if not rows:
        rows = [dict(evidence)]
    blocking_predicates = _terminal_blocking_predicates(detection_result)
    rejected_fields: list[str] = []
    edges: list[FailureEdge] = []
    legal_module_set = set(str(value) for value in legal_modules)
    for row in rows:
        owner = str(
            row.get("owner_module") or row.get("module_hint") or artifact
        )
        if owner not in legal_module_set:
            continue
        kind = str(row.get("kind") or evidence.get("kind") or "")
        field_values: list[str] = []
        if reason_code == "provider_artifact_missing":
            raw_fields = row.get("missing_fields")
            if isinstance(raw_fields, (list, tuple, set, frozenset)):
                field_values.extend(str(value) for value in raw_fields)
        elif reason_code == "provider_artifact_malformed":
            for key in ("failed_invariants", "failed_fields", "missing_fields"):
                raw_fields = row.get(key)
                if isinstance(raw_fields, (list, tuple, set, frozenset)):
                    field_values.extend(str(value) for value in raw_fields)
        elif reason_code in _SYNTHESIZED_FIELD_REASONS:
            # Synthesized-field reasons (localization fallback, responsible
            # step missing, quarantine pattern dominant): the executable field
            # is canonical for the failure shape, not carried on the artifact
            # frontier.  The owner may differ from the artifact owner when the
            # classifier routed by failure shape (e.g. quarantine evidence
            # missing -> evidence_obligation).
            synth_owner, synth_field = _SYNTHESIZED_FIELD_REASONS[reason_code]
            if reason_code == "quarantine_pattern_dominant":
                dominant = str(evidence.get("dominant_reason") or "")
                if dominant == "evidence_missing":
                    synth_owner = "evidence_obligation"
                # Prefer the actual failed-check field from the quarantined
                # audit over the canonical fallback (certificate_id), so the
                # edge is semantically bound to the violated check.
                audit_fields = _quarantine_audit_fields(
                    detection_result, synth_owner,
                )
                if audit_fields:
                    field_values.extend(audit_fields)
                    synth_field = None
            # Use the synthesized owner if the row did not carry one; this
            # lets the frontier compile even when evidence has no
            # artifact_frontier (the gold-unresolved rewrap path).
            if owner not in legal_module_set and synth_owner in legal_module_set:
                owner = synth_owner
            if synth_field is not None:
                field_values.append(synth_field)
        if not field_values and str(downstream.get("court_effect") or "") == "provider_output_unconsumed":
            field_values.append(MODULE_OUTPUT_OBJECT.get(owner, owner))
        for raw_field in field_values:
            field = "certificate_id" if raw_field == "certificate" else raw_field
            if not _field_is_executable(
                module=owner, field=field,
                evolution_direction=evolution_direction,
            ):
                rejected_fields.append(f"{owner}:{field}")
                continue
            routes = MODULE_ROUTE_IDS.get(owner, ())
            edges.append(FailureEdge(
                producer_route=routes[0] if routes else MODULE_OUTPUT_OBJECT.get(owner, owner),
                output_object=MODULE_OUTPUT_OBJECT.get(owner, owner),
                field=field,
                consumer=consumer,
                observed=(
                    "absent" if reason_code in (
                        "provider_artifact_missing",
                        "localization_temporal_fallback",
                        "responsible_step_missing",
                    )
                    else "malformed"
                ),
                required=(
                    "present" if reason_code in (
                        "provider_artifact_missing",
                        "localization_temporal_fallback",
                        "responsible_step_missing",
                    )
                    else "well_formed"
                ),
                owner_module=owner,
                source_artifact_id=str(row.get("artifact_id") or ""),
                blocking_predicate=_artifact_blocking_predicate(
                    kind, blocking_predicates,
                ),
            ))
    dedup = {edge.edge_id: edge for edge in edges}
    frontier = tuple(
        dedup[key] for key in sorted(dedup)
    )
    return frontier, tuple(sorted(set(rejected_fields)))


def build_failure_episode(
    *,
    detection_result: Mapping[str, Any],
    signal: Any,
    attribution: FailureAttribution,
    evolution_direction: str,
    record_key: str,
    allowed_modules: Sequence[str] = (),
    attempts: Sequence[FailureAttempt] = (),
) -> FailureEpisode:
    """Build the canonical causal episode for one triggered detection.

    Raises ``UnaddressableFailure`` when the authoritative attribution is
    not actionable or the owner has no legal executable surface.
    """
    detection_result = detection_result or {}
    layer = attribution.layer
    if layer not in (LAYER_L1, LAYER_L2):
        raise UnaddressableFailure(
            f"attribution_not_actionable:{attribution.reason_code}"
        )
    artifact = attribution.artifact
    if layer == LAYER_L1 and artifact not in MODULE_ACTION_OPCODES:
        raise UnaddressableFailure(f"owner_no_legal_surface:{artifact}")

    evidence = attribution.evidence if isinstance(attribution.evidence, Mapping) else {}
    candidate_id, candidate_kind, origin_refs, dependency_refs = _select_candidate(
        detection_result=detection_result,
        attribution=attribution,
    )

    frontier_evidence = (
        [item for item in evidence.get("artifact_frontier") or () if isinstance(item, Mapping)]
        if isinstance(evidence.get("artifact_frontier"), list) else []
    )
    raw_missing = [
        value
        for row in ([evidence, *frontier_evidence])
        for value in (
            row.get("missing_fields")
            if isinstance(row.get("missing_fields"), (list, tuple, set, frozenset))
            else ()
        )
    ]
    if not isinstance(raw_missing, (list, tuple, set, frozenset)):
        # A string value would be expanded into its characters by
        # ``_sorted_dedup``; normalize non-sequence evidence defensively.
        raw_missing = ()
    # Keep the detector's original diagnostic vocabulary in the causal slice
    # so persisted episodes remain lossless and backward-compatible.  Legacy
    # aliases are normalized only at the executable edge, where the field must
    # belong to the registered ActionSpec vocabulary.
    missing_fields = _sorted_dedup(raw_missing)
    executable_missing_fields = _sorted_dedup(
        "certificate_id" if str(value) == "certificate" else value
        for value in missing_fields
    )
    raw_invariants = [
        value
        for row in ([evidence, *frontier_evidence])
        for value in (
            row.get("failed_invariants")
            if isinstance(row.get("failed_invariants"), (list, tuple, set, frozenset))
            else ()
        )
    ]
    failed_invariants = _sorted_dedup(raw_invariants)
    raw_failed_fields = [
        value
        for row in ([evidence, *frontier_evidence])
        for value in (
            row.get("failed_fields")
            if isinstance(row.get("failed_fields"), (list, tuple, set, frozenset))
            else ()
        )
    ]
    if not raw_failed_fields:
        raw_failed_fields = (
            executable_missing_fields
            if attribution.reason_code == "provider_artifact_malformed" else ()
        )
    failed_fields = _sorted_dedup(raw_failed_fields)
    malformed_fields = (
        failed_fields
        if attribution.reason_code == "provider_artifact_malformed" else ()
    )

    downstream = _downstream_consumption(
        detection_result=detection_result,
        layer=layer,
    )

    legal_modules = tuple(dict.fromkeys(str(value) for value in allowed_modules))
    legal_operations = _sorted_dedup(
        opcode
        for module in legal_modules
        for opcode, _template in MODULE_ACTION_OPCODES.get(module, ())
    )
    legal_output_fields = _sorted_dedup(
        target
        for module in legal_modules
        for targets in MODULE_OPERATION_TARGETS.get(module, {}).values()
        for target in targets
    )
    legal_observables = _sorted_dedup(
        field
        for module in legal_modules
        for field in MODULE_OBSERVABLE_FIELDS.get(module, ())
    )
    envelope = ActionEnvelope(
        legal_modules=legal_modules,
        legal_operations=legal_operations,
        legal_output_fields=legal_output_fields,
        legal_observables=legal_observables,
    )
    repair_frontier, rejected_frontier_fields = _compile_repair_frontier(
        layer=layer,
        artifact=artifact,
        reason_code=attribution.reason_code,
        evidence=evidence,
        detection_result=detection_result,
        downstream=downstream,
        legal_modules=legal_modules,
        evolution_direction=evolution_direction,
    )
    edge = repair_frontier[0] if repair_frontier else None
    if layer == LAYER_L1 and not repair_frontier:
        if rejected_frontier_fields:
            raise UnaddressableFailure(
                "failure_edge_outside_action_vocabulary:"
                + ",".join(rejected_frontier_fields)
            )
        raise UnaddressableFailure("repair_frontier_empty")

    detection_snapshot = {
        "state": str(getattr(signal, "state", "") or detection_result.get("state") or ""),
        "evolution_direction": str(evolution_direction or ""),
        "trigger_source": str(getattr(signal.trigger_source, "value", signal.trigger_source)),
    }
    attribution_snapshot = {
        "layer": layer,
        "artifact": artifact,
        "reason_code": attribution.reason_code,
        "owner_module": artifact if layer == LAYER_L1 else "",
        "fallback_owner_module": (
            str(getattr(signal, "target_module", ""))
            if str(getattr(signal, "target_module", "")) != artifact
            else ""
        ),
    }

    candidate_rows = _candidate_rows(detection_result, candidate_kind) if candidate_kind != "unknown" else []
    present_fields = _present_fields(candidate_rows[0]) if candidate_rows else ()
    evidence_refs = _evidence_refs(
        detection_result, detection_result.get("responsible_step")
    )
    required_evidence_refs = _sorted_dedup(
        [*origin_refs, *evidence_refs]
    )[:8]

    completeness = "complete"
    if not candidate_id or edge is None:
        completeness = "partial"

    causal_slice = CausalSlice(
        candidate_id=str(candidate_id),
        candidate_kind=str(candidate_kind),
        origin_refs=origin_refs,
        dependency_refs=dependency_refs,
        required_fields=missing_fields,
        required_evidence_refs=required_evidence_refs,
        present_fields=present_fields,
        missing_fields=missing_fields,
        malformed_fields=malformed_fields,
        consumer=str(downstream.get("consumer") or ""),
        qualification_state=str(downstream.get("qualification_state") or ""),
        certificate_state=str(downstream.get("certificate_state") or ""),
        quarantine_state=str(downstream.get("quarantine_state") or ""),
        coverage_state=str(downstream.get("coverage_state") or ""),
        court_effect=str(downstream.get("court_effect") or ""),
        failure_edge=edge,
        evidence_refs=evidence_refs,
        completeness=completeness,
        repair_frontier=repair_frontier,
    )

    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "record_key": record_key,
        "detection": detection_snapshot,
        "attribution": attribution_snapshot,
        "causal_slice": causal_slice.to_dict(),
        "action_envelope": envelope.to_dict(),
    }
    episode_id = _stable_identity(identity_payload)
    return FailureEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id=episode_id,
        record_key=record_key,
        detection=detection_snapshot,
        attribution=attribution_snapshot,
        causal_slice=causal_slice,
        action_envelope=envelope,
        attempts=tuple(attempts),
    )


# ============================================================
# Prompt-safe projection
# ============================================================

def project_failure_episode_for_prompt(
    episode: FailureEpisode,
    historical_refs: Sequence[HistoricalEpisodeReference] = (),
) -> dict[str, Any]:
    """Project the episode into the bounded reflection context.

    Excludes record key, case identity, attempts, gold and evaluator data,
    and any unbounded trajectory text.
    """
    slice_payload = episode.causal_slice
    projection: dict[str, Any] = {
        "schema_version": episode.schema_version,
        "detection": dict(episode.detection),
        "attribution": dict(episode.attribution),
        "candidate": {
            "candidate_id": slice_payload.candidate_id,
            "candidate_kind": slice_payload.candidate_kind,
            "origin_refs": list(slice_payload.origin_refs)[:8],
        },
        "obligation_vs_observed": {
            "required_fields": list(slice_payload.required_fields),
            "required_evidence_refs": list(slice_payload.required_evidence_refs),
            "present_fields": list(slice_payload.present_fields),
            "missing_fields": list(slice_payload.missing_fields),
            "malformed_fields": list(slice_payload.malformed_fields),
        },
        "downstream_consumption": {
            "consumer": slice_payload.consumer,
            "qualification_state": slice_payload.qualification_state,
            "certificate_state": slice_payload.certificate_state,
            "quarantine_state": slice_payload.quarantine_state,
            "coverage_state": slice_payload.coverage_state,
            "court_effect": slice_payload.court_effect,
        },
        "repair_frontier": [
            edge.to_dict() for edge in slice_payload.repair_frontier
        ],
        "evidence_refs": list(slice_payload.evidence_refs),
        "completeness": slice_payload.completeness,
        "action_envelope": episode.action_envelope.to_dict(),
    }
    if historical_refs:
        projection["historical_references"] = [
            reference.to_dict() for reference in historical_refs
        ]
    return projection


# ============================================================
# Validation
# ============================================================

def validate_failure_episode(
    episode: FailureEpisode,
) -> tuple[bool, str]:
    """Validate the episode invariants.

    Returns ``(valid, reason)``; the episode is valid only when its
    schema, stable identity, sorted/deduped field lists and prompt
    firewall all hold.
    """
    if episode.schema_version != SCHEMA_VERSION:
        return False, f"unsupported_schema:{episode.schema_version}"
    slice_payload = episode.causal_slice
    if list(slice_payload.missing_fields) != sorted(set(slice_payload.missing_fields)):
        return False, "missing_fields_not_normalized"
    if list(slice_payload.malformed_fields) != sorted(set(slice_payload.malformed_fields)):
        return False, "malformed_fields_not_normalized"
    frontier_ids = [edge.edge_id for edge in slice_payload.repair_frontier]
    if len(frontier_ids) != len(set(frontier_ids)):
        return False, "repair_frontier_duplicate_edges"
    if (
        slice_payload.failure_edge is not None
        and slice_payload.repair_frontier
        and slice_payload.failure_edge.edge_id
        != slice_payload.repair_frontier[0].edge_id
    ):
        return False, "failure_edge_not_frontier_primary"
    identity_payload = {
        "schema_version": episode.schema_version,
        "record_key": episode.record_key,
        "detection": dict(episode.detection),
        "attribution": dict(episode.attribution),
        "causal_slice": slice_payload.to_dict(),
        "action_envelope": episode.action_envelope.to_dict(),
    }
    if episode.episode_id != _stable_identity(identity_payload):
        return False, "episode_id_mismatch"
    projection = project_failure_episode_for_prompt(episode)
    if "record_key" in _canonical_json(projection):
        return False, "record_key_leaks_into_prompt"
    return True, ""


def attempt_id_for(
    episode_id: str,
    proposal_signature: str,
    logical_attempt_index: int,
) -> str:
    """Stable attempt identity: episode + proposal + logical index."""
    return _stable_identity({
        "episode_id": episode_id,
        "proposal_signature": proposal_signature,
        "logical_attempt_index": int(logical_attempt_index),
    })


def proposal_signature_for(
    *,
    operation: str,
    target_module: str,
    action_spec: Mapping[str, Any] | None = None,
    proposal_content: str = "",
) -> str:
    """Stable identity of one proposal within an episode."""
    return _stable_identity({
        "operation": str(operation or ""),
        "target_module": str(target_module or ""),
        "action_spec": dict(action_spec or {}),
        "proposal_content": " ".join(str(proposal_content or "").split()),
    })
