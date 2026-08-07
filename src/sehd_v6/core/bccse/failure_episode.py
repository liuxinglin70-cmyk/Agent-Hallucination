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

The paired-replay input type that used to be called ``FailureEpisode`` is
``ReplayEpisode`` (see types.py); this class is the causal episode and has
no overlap with it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .action_spec import MODULE_OUTPUT_OBJECT, MODULE_ROUTE_IDS
from .module_charter import (
    MODULE_ACTION_OPCODES,
    MODULE_OBSERVABLE_FIELDS,
    MODULE_OPERATION_TARGETS,
)
from .v6_contracts import LAYER_L1, LAYER_L2, FailureAttribution

SCHEMA_VERSION = "sehd.failure_episode.v1"

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

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_route": self.producer_route,
            "output_object": self.output_object,
            "field": self.field,
            "consumer": self.consumer,
            "observed": self.observed,
            "required": self.required,
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
            "evidence_refs": list(self.evidence_refs),
            "completeness": self.completeness,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CausalSlice":
        edge = value.get("failure_edge")
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
            failure_edge=(
                FailureEdge.from_dict(dict(edge))
                if isinstance(edge, Mapping) else None
            ),
            evidence_refs=tuple(
                str(item) for item in value.get("evidence_refs") or ()
            ),
            completeness=str(value.get("completeness") or "partial"),
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
    action_spec: dict[str, Any] | None
    local_outcome: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "mechanism_signature": list(self.mechanism_signature),
            "shared_facts": dict(self.shared_facts),
            "current_only_facts": dict(self.current_only_facts),
            "historical_only_facts": dict(self.historical_only_facts),
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
        artifact_id: str = "",
        source: str = "",
    ) -> None:
        artifact = {
            "kind": kind,
            "module_hint": "",
            "malformed": malformed,
            "missing_fields": missing,
        }
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
            failed = audit.get("failed_checks")
            if isinstance(failed, list):
                missing_fields = sorted(set(
                    str(name) for name in failed
                    if name != "required_fields"
                ))
                if missing_fields:
                    add(
                        kind,
                        malformed=True,
                        missing=missing_fields,
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
    surviving_error_certificates = (
        len(surviving_ids)
        if isinstance(surviving_ids, (list, tuple))
        else max(len(prosecution) - _id_count("quarantined_error_certificate_ids"), 0)
    )

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
        "verdict_state": detection_result.get("state"),
    }
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
            "Select one observed missing or malformed output field owned by the "
            "target module. Do not claim an effect on fields outside its ownership. "
            "diagnostic_gap primary/secondary modules are attribution hints; the "
            "allowed module set is authoritative."
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


def _first_repairable_edge(
    *,
    layer: str,
    artifact: str,
    reason_code: str,
    candidate_kind: str,
    missing_fields: Sequence[str],
    malformed_fields: Sequence[str],
    downstream: Mapping[str, str],
) -> FailureEdge | None:
    routes = MODULE_ROUTE_IDS.get(artifact, ())
    output_object = (
        MODULE_OUTPUT_OBJECT.get(artifact, artifact)
        if layer == LAYER_L1 else artifact
    )
    producer = routes[0] if routes else output_object
    consumer = str(downstream.get("consumer") or "proof_court")
    if layer == LAYER_L2:
        return FailureEdge(
            producer_route="policy_evaluator",
            output_object=artifact,
            field=reason_code,
            consumer=consumer,
            observed=(
                str(downstream.get("qualification_state") or "unresolved")
            ),
            required="not_blocked",
        )
    if reason_code == "provider_artifact_missing":
        return FailureEdge(
            producer_route=producer,
            output_object=output_object,
            field=missing_fields[0] if missing_fields else output_object,
            consumer=consumer,
            observed="absent",
            required="present",
        )
    if reason_code == "provider_artifact_malformed":
        return FailureEdge(
            producer_route=producer,
            output_object=output_object,
            field=(
                malformed_fields[0]
                if malformed_fields
                else (missing_fields[0] if missing_fields else "well_formedness")
            ),
            consumer=consumer,
            observed="malformed",
            required="well_formed",
        )
    if str(downstream.get("court_effect") or "") == "provider_output_unconsumed":
        return FailureEdge(
            producer_route=producer,
            output_object=output_object,
            field=output_object,
            consumer=consumer,
            observed="emitted_but_unconsumed",
            required="moves_court_edge",
        )
    return None


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

    raw_missing = evidence.get("missing_fields")
    if not isinstance(raw_missing, (list, tuple, set, frozenset)):
        # A string value would be expanded into its characters by
        # ``_sorted_dedup``; normalize non-sequence evidence defensively.
        raw_missing = ()
    missing_fields = _sorted_dedup(raw_missing)
    malformed_fields = missing_fields if attribution.reason_code == "provider_artifact_malformed" else ()

    downstream = _downstream_consumption(
        detection_result=detection_result,
        layer=layer,
    )

    edge = _first_repairable_edge(
        layer=layer,
        artifact=artifact,
        reason_code=attribution.reason_code,
        candidate_kind=candidate_kind,
        missing_fields=missing_fields,
        malformed_fields=malformed_fields,
        downstream=downstream,
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
        "failure_edge": (
            slice_payload.failure_edge.to_dict()
            if slice_payload.failure_edge is not None else None
        ),
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
