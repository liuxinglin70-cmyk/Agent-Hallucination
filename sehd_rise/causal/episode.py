"""CausalFailureEpisode: the single online skill-formation record.

Plan §2.3 establishes one causal representation for every downstream
module. The old package spread the failure record across
``step_attributor.AttributionSignal`` (trigger + module), the
``failure_classifier.FailureAttribution`` (layer + artifact), and the
``failure_episode.FailureEpisode`` (causal slice + action envelope + a third
pass of owner refinement). The new ``CausalFailureEpisode`` is the single
record carrying every §2.3 field:

- terminal state        (signal.state)
- terminal basis        (the proof court's ``terminal_basis`` block)
- blocking predicate    (the predicate the cut would lift)
- causal failure edge   (the primary ``FailureEdge`` -- one entry of the
                        repair_frontier; an executable field-level locus)
- failure owner         (cut.owner: an L1 module id, an L2 artifact name,
                        or a boundary repair owner label)
- skill layer           (L1 / L2 / DETECTOR / NONE)
- repairable surface    (``RepairableSurface``: legal modules / operations /
                        output fields / observables across the allowed set)
- legal repair operations (the primary edge's legal opcodes, after the
                          direction filter in Phase 6 narrows them)
- observable effect     (``observable_effect``: downstream_consumption +
                        court_effect)
- repairability status  (filled by ``repairability.analyze``: NON_EVOLVABLE,
                        REPAIRABLE, UNADDRESSABLE_FRAMEWORK_DEFECT)

The episode is built by ``build_causal_episode`` from a ``DetectionSignal``
and a ``CausalCut``; it never re-derives the owner from artifacts. The
verdict on whether the case enters evolution is then a separate step
(``repairability.analyze``), so the trigger/repairability split (plan §2.1)
is enforced as two distinct modules instead of an attribute on the signal.

The old ``UnaddressableFailure`` exception is retired (plan §2.2): instead
of throwing and being silently swallowed by the runner, the
repairability authority returns ``UNADDRESSABLE_FRAMEWORK_DEFECT`` which is
recorded as a framework invariant violation.

The old ``MechanismRepairPlan`` dataclass is replaced by
``causal.plan.RepairPlan``, the canonical protocol shared by L1 and L2.
``compile_mechanism_repair_plans`` is replaced by ``plan.build_repair_plan``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from sehd_rise.causal.cut import (
    REASON_GOLD_FALSE_NEGATIVE_CLEAN,
    REASON_GOLD_FALSE_POSITIVE,
    REASON_LOCALIZATION_FALLBACK,
    REASON_PROVIDER_MALFORMED,
    REASON_PROVIDER_MISSING,
    REASON_QUARANTINE_PATTERN,
    REASON_RESPONSIBLE_STEP_MISSING,
    CausalCut,
)
from sehd_rise.causal.signals import DetectionSignal
from sehd_rise.skills.action import (
    CLEAN_TO_ERROR_ACTIONS,
    ERROR_TO_CLEAN_ACTIONS,
    MODULE_OUTPUT_OBJECT,
    MODULE_ROUTE_IDS,
)
from sehd_rise.skills.vocabulary.charters import (
    MODULE_ACTION_OPCODES,
    MODULE_OBSERVABLE_FIELDS,
    MODULE_OPERATION_TARGETS,
)
from sehd_rise.skills.vocabulary.contracts import (
    LAYER_L1,
    LAYER_L2,
)


SCHEMA_VERSION = "sehd_rise.causal_episode.v1"

# Synthesized-field reason codes used by ``_compile_failure_edges`` to decide
# whose executable field is canonical for a failure shape that doesn't carry
# its field on the artifact frontier (e.g. localization fallback). Mirrors
# the cut's same vocabulary so the episode stays in lock-step with the cut.
_SYNTHESIZED_FIELD_REASONS: dict[str, tuple[str, str]] = {
    REASON_LOCALIZATION_FALLBACK: (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    REASON_RESPONSIBLE_STEP_MISSING: (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    REASON_QUARANTINE_PATTERN: (
        "atomic_verifier_instruction", "certificate_id",
    ),
    REASON_GOLD_FALSE_POSITIVE: (
        "evidence_obligation", "coverage_state",
    ),
    # FN-clean (a hallucinated case wrongly adjudicated clean) is a missing-
    # evidence failure: the only deterministic clean_to_error opcode left on
    # ``evidence_obligation`` is ``require_evidence_field``, so the canonical
    # executable field is a norm-evidence citation, not a certificate id.
    REASON_GOLD_FALSE_NEGATIVE_CLEAN: (
        "evidence_obligation", "norm_evidence_refs",
    ),
}

_EVIDENCE_REF_KEYS = (
    "evidence_refs", "support_refs", "boundary_refs", "origin_refs",
    "dependency_refs", "impact_evidence_refs", "consumption_evidence_refs",
    "recovery_evidence_refs", "benchmark_causal_evidence_refs",
)

# Evaluator-only keys that must never enter a prompt projection. Kept from
# the old FailureEpisode contract so a projected episode preserves the same
# prompt-firewall guarantee.
EVALUATOR_ONLY_KEYS = frozenset({
    "case_id", "source_id", "gold_label", "gold_explanation",
    "is_hallucination", "hallucination_step", "hallucination_reason",
    "hallucination_category", "hallucination_subcategory",
    "outer_feedback", "gold_steps", "true_answer", "agent_answer",
})


# ============================================================
# Value types
# ============================================================

@dataclass(frozen=True)
class FailureEdge:
    """One executable evidence locus the cut identified.

    A repair plan chooses one edge and one of its legal operations; the
    framework applies it to the producer route + output field. ``edge_id``
    is a stable content hash so proposals identify an edge explicitly
    rather than inheriting the cut's first match.
    """

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
class RepairableSurface:
    """Set of legal L1/L2 surfaces the episode may propose against.

    Plan §2.3 names this field; ``RepairableSurface`` is its single
    embodiment. Plan §2.7 splits it per RepairPlan via
    ``legal_operations``; the envelope here stays broad so the prompt
    projection can present the full closed vocabulary to the reflection
    model without leaking forbidden fields.
    """

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
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairableSurface":
        return cls(
            legal_modules=tuple(str(item) for item in value.get("legal_modules") or ()),
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
class CausalSlice:
    """Evidence bundle the reflection prompt consumes.

    The slice is the prompt-safe subobject of the episode: it carries the
    candidate identifier + kind, the origin / dependency evidence refs,
    the field obligation (missing / malformed / present), the downstream
    consumption projection, and the executable repair frontier. It does
    NOT carry the gold label, attempt history, or evaluator-only fields.
    """

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
    evidence_refs: tuple[str, ...]
    completeness: str
    failure_edge: FailureEdge | None
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
            "evidence_refs": list(self.evidence_refs),
            "completeness": self.completeness,
            "failure_edge": (
                self.failure_edge.to_dict()
                if self.failure_edge is not None else None
            ),
            "repair_frontier": [edge.to_dict() for edge in self.repair_frontier],
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
            evidence_refs=tuple(
                str(item) for item in value.get("evidence_refs") or ()
            ),
            completeness=str(value.get("completeness") or "partial"),
            failure_edge=legacy_edge or (frontier[0] if frontier else None),
            repair_frontier=frontier,
        )


@dataclass(frozen=True)
class FailureAttempt:
    """One executed repair attempt against the episode.

    Phase 4 declares the data shape; Phase 7's execution layer fills the
    runtime fields (compiled / injected / applied / observable_delta /
    downstream_court_delta / decision). The episode stores attempts as a
    tuple so an audited replay order is recoverable.
    """

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
            "action_spec": dict(self.action_spec),
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
class HistoricalEpisodeReference:
    """Reference to a past episode used for retrieval-guided reflection.

    Phase 6 (planning/retrieval) loads historical references from the
    experience memory and projects them alongside the current episode. The
    fields are the prompt-safe projection of one historical reference, so
    no record key or case identity leaks.
    """

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
# Episode record (§2.3 fields)
# ============================================================

@dataclass(frozen=True)
class CausalFailureEpisode:
    """Single causal representation for one triggered detection.

    Carries every §2.3 field as a first-class dataclass attribute. The
    repairability status / reason are empty until
    ``repairability.analyze`` fills them via ``with_repairability``.

    ``causal_failure_edge_frontier`` is a derived view over the causal
    slice's repair frontier; the primary ``causal_failure_edge`` is
    ``causal_slice.repair_frontier[0]`` when the frontier is non-empty.
    """

    schema_version: str
    episode_id: str
    record_key: str
    terminal_state: str
    terminal_basis: Mapping[str, Any]
    blocking_predicate: str
    causal_failure_edge: FailureEdge | None
    failure_owner: str
    skill_layer: str
    repairable_surface: RepairableSurface
    legal_repair_operations: tuple[str, ...]
    observable_effect: Mapping[str, Any]
    repairability_status: str
    repairability_reason: str
    # Supporting context
    failure_origin: str
    secondary_owner: str
    reason_code: str
    evidence: Mapping[str, Any]
    detection_snapshot: Mapping[str, Any]
    attribution_snapshot: Mapping[str, Any]
    causal_slice: CausalSlice
    attempts: tuple[FailureAttempt, ...] = ()

    @property
    def causal_failure_edge_frontier(self) -> tuple[FailureEdge, ...]:
        return self.causal_slice.repair_frontier

    @property
    def actionable(self) -> bool:
        """Whether the episode landed on an evolvable surface."""
        return self.skill_layer in (LAYER_L1, LAYER_L2)

    def with_repairability(
        self, *, status: str, reason: str
    ) -> "CausalFailureEpisode":
        """Return a copy with the repairability verdict applied."""
        return replace(
            self, repairability_status=str(status), repairability_reason=str(reason)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "record_key": self.record_key,
            "terminal_state": self.terminal_state,
            "terminal_basis": dict(self.terminal_basis),
            "blocking_predicate": self.blocking_predicate,
            "causal_failure_edge": (
                self.causal_failure_edge.to_dict()
                if self.causal_failure_edge is not None else None
            ),
            "failure_owner": self.failure_owner,
            "skill_layer": self.skill_layer,
            "repairable_surface": self.repairable_surface.to_dict(),
            "legal_repair_operations": list(self.legal_repair_operations),
            "observable_effect": dict(self.observable_effect),
            "repairability_status": self.repairability_status,
            "repairability_reason": self.repairability_reason,
            "failure_origin": self.failure_origin,
            "secondary_owner": self.secondary_owner,
            "reason_code": self.reason_code,
            "evidence": dict(self.evidence),
            "detection_snapshot": dict(self.detection_snapshot),
            "attribution_snapshot": dict(self.attribution_snapshot),
            "causal_slice": self.causal_slice.to_dict(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CausalFailureEpisode":
        edge = value.get("causal_failure_edge")
        causal_failure_edge = (
            FailureEdge.from_dict(dict(edge))
            if isinstance(edge, Mapping) else None
        )
        return cls(
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            episode_id=str(value.get("episode_id") or ""),
            record_key=str(value.get("record_key") or ""),
            terminal_state=str(value.get("terminal_state") or ""),
            terminal_basis=dict(value.get("terminal_basis") or {}),
            blocking_predicate=str(value.get("blocking_predicate") or ""),
            causal_failure_edge=causal_failure_edge,
            failure_owner=str(value.get("failure_owner") or ""),
            skill_layer=str(value.get("skill_layer") or ""),
            repairable_surface=RepairableSurface.from_dict(
                dict(value.get("repairable_surface") or {})
            ),
            legal_repair_operations=tuple(
                str(item)
                for item in value.get("legal_repair_operations") or ()
                if str(item)
            ),
            observable_effect=dict(value.get("observable_effect") or {}),
            repairability_status=str(value.get("repairability_status") or ""),
            repairability_reason=str(value.get("repairability_reason") or ""),
            failure_origin=str(value.get("failure_origin") or ""),
            secondary_owner=str(value.get("secondary_owner") or ""),
            reason_code=str(value.get("reason_code") or ""),
            evidence=dict(value.get("evidence") or {}),
            detection_snapshot=dict(value.get("detection_snapshot") or {}),
            attribution_snapshot=dict(value.get("attribution_snapshot") or {}),
            causal_slice=CausalSlice.from_dict(
                dict(value.get("causal_slice") or {})
            ),
            attempts=tuple(
                FailureAttempt.from_dict(dict(item))
                for item in value.get("attempts") or ()
                if isinstance(item, Mapping)
            ),
        )


# ============================================================
# Mechanism signature (preserved from old failure_episode)
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
# Helpers for episode build
# ============================================================

def _candidate_rows(
    signal: DetectionSignal, kind: str
) -> list[Mapping[str, Any]]:
    """Read candidate/certificate rows of one kind from the signal."""
    if kind == "candidate":
        return [row for row in signal.census if isinstance(row, Mapping)]
    if kind == "error_certificate":
        payload = signal.prosecution
    else:
        payload = signal.defense
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
    signal: DetectionSignal,
    cut: CausalCut,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Pick the responsible candidate deterministically.

    Priority: the attribution-referenced candidate first, then the artifact
    kind the cut points at, then the earliest census candidate. Returns
    ``(candidate_id, kind, origin_refs, dependency_refs)``.
    """
    evidence = (
        cut.evidence if isinstance(cut.evidence, Mapping) else {}
    )
    kind = str(evidence.get("kind") or "")
    artifact_id = str(evidence.get("artifact_id") or "")
    if kind and artifact_id:
        for row in _candidate_rows(signal, kind):
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

    proof = signal.proof_adjudication or {}
    positive_ids = (
        proof.get("positive_candidate_ids") or []
        if isinstance(proof, Mapping) else []
    )
    if isinstance(positive_ids, (set, frozenset)):
        positive_ids = sorted(str(value) for value in positive_ids)
    for candidate_id in positive_ids:
        row = next(
            (
                row
                for row in _candidate_rows(signal, "candidate")
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
    rows = _candidate_rows(signal, kind) if kind else []
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
    census = _candidate_rows(signal, "candidate")
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
    signal: DetectionSignal, responsible_step: Any
) -> tuple[str, ...]:
    refs: set[str] = set()
    for row in _candidate_rows(signal, "candidate"):
        refs.update(_row_refs(row, "origin_refs"))
    for kind in ("error_certificate", "defense_certificate"):
        for row in _candidate_rows(signal, kind):
            for key in _EVIDENCE_REF_KEYS:
                refs.update(_row_refs(row, key))
            if row.get("certificate_id"):
                refs.add(f"certificate:{row['certificate_id']}")
    if responsible_step:
        refs.add(f"step:{responsible_step}")
    return _sorted_dedup(refs)[:12]


def _downstream_consumption(
    *, signal: DetectionSignal, skill_layer: str
) -> dict[str, str]:
    """Court-side projection of what consumed the cut's owner output."""
    court = signal.proof_adjudication or {}
    if not isinstance(court, Mapping):
        court = {}
    health = signal.diagnostics if isinstance(signal.diagnostics, Mapping) else {}

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
            else len(court.get("quarantined_error_certificate_ids") or ())
        ),
        "defense": (
            int(health.get("quarantined_defense_certificate_count") or 0)
            if isinstance(health.get("quarantined_defense_certificate_count"), int)
            else len(court.get("quarantined_defense_certificate_ids") or ())
        ),
    }
    coverage = (
        f"{court.get('prosecution_coverage') or 'none'}/"
        f"{court.get('defense_coverage') or 'none'}"
    )
    terminal = signal.state or str(court.get("state") or "")
    if skill_layer == LAYER_L2:
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
    *, module: str, evolution_direction: str
) -> frozenset[str]:
    if str(evolution_direction).endswith("_to_error"):
        return CLEAN_TO_ERROR_ACTIONS.get(module, frozenset())
    if str(evolution_direction).endswith("_to_clean"):
        return ERROR_TO_CLEAN_ACTIONS.get(module, frozenset())
    return frozenset(MODULE_OPERATION_TARGETS.get(module, {}))


def _field_is_executable(
    *, module: str, field: str, evolution_direction: str
) -> bool:
    operations = _direction_operations(
        module=module, evolution_direction=evolution_direction
    )
    return any(
        operation in operations and field in targets
        for operation, targets in MODULE_OPERATION_TARGETS.get(module, {}).items()
    )


def _terminal_blocking_predicates(
    signal: DetectionSignal,
) -> tuple[str, ...]:
    proof = signal.proof_adjudication
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
    kind: str, blocking_predicates: Sequence[str]
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
    signal: DetectionSignal, owner: str
) -> list[str]:
    """Extract executable provider fields from the quarantined audits.

    Returns the participating provider fields for the first non-pass audit
    whose failed_checks map to registered fields for the given owner's side
    (error vs defense). Empty when no audit is available -- the caller then
    falls back to the canonical synthesized field.
    """
    from sehd_rise.skills.vocabulary.contracts import (
        DEFENSE_CHECK_FIELDS,
        ERROR_CHECK_FIELDS,
    )
    proof = signal.proof_adjudication
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


def _compile_failure_edges(
    *,
    cut: CausalCut,
    signal: DetectionSignal,
    downstream: Mapping[str, str],
    legal_modules: Sequence[str],
    evolution_direction: str,
) -> tuple[tuple[FailureEdge, ...], tuple[str, ...]]:
    """Compile all executable edges participating in the terminal proof cut.

    Result is independent of provider-artifact list order; each edge owns its
    producer module and a stable id so a proposal must explicitly choose an
    edge instead of inheriting the cut's first match.
    """
    consumer = str(downstream.get("consumer") or "proof_court")
    if cut.skill_layer == LAYER_L2:
        edge = FailureEdge(
            producer_route="policy_evaluator",
            output_object=cut.owner,
            field=cut.reason_code,
            consumer=consumer,
            observed=str(downstream.get("qualification_state") or "unresolved"),
            required="not_blocked",
            blocking_predicate=cut.blocking_predicate,
        )
        return (edge,), ()

    evidence = cut.evidence if isinstance(cut.evidence, Mapping) else {}
    raw_frontier = evidence.get("artifact_frontier")
    rows = (
        [dict(item) for item in raw_frontier if isinstance(item, Mapping)]
        if isinstance(raw_frontier, list) else []
    )
    if not rows:
        rows = [dict(evidence)]
    blocking_predicates = _terminal_blocking_predicates(signal)
    rejected_fields: list[str] = []
    edges: list[FailureEdge] = []
    legal_module_set = set(str(value) for value in legal_modules)
    for row in rows:
        owner = str(
            row.get("owner_module") or row.get("module_hint") or cut.owner
        )
        if owner not in legal_module_set:
            continue
        kind = str(row.get("kind") or evidence.get("kind") or "")
        field_values: list[str] = []
        if cut.reason_code == REASON_PROVIDER_MISSING:
            raw_fields = row.get("missing_fields")
            if isinstance(raw_fields, (list, tuple, set, frozenset)):
                field_values.extend(str(value) for value in raw_fields)
        elif cut.reason_code == REASON_PROVIDER_MALFORMED:
            for key in ("failed_invariants", "failed_fields", "missing_fields"):
                raw_fields = row.get(key)
                if isinstance(raw_fields, (list, tuple, set, frozenset)):
                    field_values.extend(str(value) for value in raw_fields)
        elif cut.reason_code in _SYNTHESIZED_FIELD_REASONS:
            synth_owner, synth_field = _SYNTHESIZED_FIELD_REASONS[cut.reason_code]
            if cut.reason_code == REASON_QUARANTINE_PATTERN:
                dominant = str(evidence.get("dominant_reason") or "")
                if dominant == "evidence_missing":
                    synth_owner = "evidence_obligation"
                audit_fields = _quarantine_audit_fields(signal, synth_owner)
                if audit_fields:
                    field_values.extend(audit_fields)
                    synth_field = None
            if owner not in legal_module_set and synth_owner in legal_module_set:
                owner = synth_owner
            if synth_field is not None:
                field_values.append(synth_field)
        if (
            not field_values
            and str(downstream.get("court_effect") or "") == "provider_output_unconsumed"
        ):
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
            if cut.reason_code == REASON_GOLD_FALSE_POSITIVE:
                observed, required = "complete", "incomplete"
            elif cut.reason_code == REASON_GOLD_FALSE_NEGATIVE_CLEAN:
                observed, required = "absent", "present"
            elif cut.reason_code in (
                REASON_PROVIDER_MISSING,
                REASON_LOCALIZATION_FALLBACK,
                REASON_RESPONSIBLE_STEP_MISSING,
            ):
                observed, required = "absent", "present"
            else:
                observed, required = "malformed", "well_formed"
            edges.append(FailureEdge(
                producer_route=routes[0] if routes else MODULE_OUTPUT_OBJECT.get(owner, owner),
                output_object=MODULE_OUTPUT_OBJECT.get(owner, owner),
                field=field,
                consumer=consumer,
                observed=observed,
                required=required,
                owner_module=owner,
                source_artifact_id=str(row.get("artifact_id") or ""),
                blocking_predicate=(
                    "gold_polarity_mismatch"
                    if cut.reason_code in (
                        REASON_GOLD_FALSE_POSITIVE,
                        REASON_GOLD_FALSE_NEGATIVE_CLEAN,
                    )
                    else _artifact_blocking_predicate(kind, blocking_predicates)
                ),
            ))
    dedup = {edge.edge_id: edge for edge in edges}
    frontier = tuple(dedup[key] for key in sorted(dedup))
    return frontier, tuple(sorted(set(rejected_fields)))


def _legal_repair_operations(
    *, cut: CausalCut, frontier: Sequence[FailureEdge]
) -> tuple[str, ...]:
    """Operations available against the primary edge in the frontier."""
    if not frontier or cut.skill_layer not in (LAYER_L1, LAYER_L2):
        return ()
    if cut.skill_layer == LAYER_L1:
        owner = frontier[0].owner_module
        return tuple(sorted(opcode for opcode, _ in MODULE_ACTION_OPCODES.get(owner, ())))
    # L2: closed action set is owned by the L2 vocabulary registry; Phase 6
    # narrows by the candidate's ``params`` whitelist.
    from sehd_rise.skills.vocabulary.contracts import COURT_LOCAL_ACTIONS
    return tuple(sorted(COURT_LOCAL_ACTIONS))


def _terminal_basis_payload(signal: DetectionSignal) -> dict[str, Any]:
    """Read the proof court's terminal basis block (the §2.3 terminal_basis)."""
    proof = signal.proof_adjudication or {}
    raw = proof.get("terminal_basis") if isinstance(proof, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        "state": str(raw.get("state") or ""),
        "decisive_facts": dict(raw.get("decisive_facts") or {}),
        "blocking_facts": [
            dict(item) for item in raw.get("blocking_facts") or ()
            if isinstance(item, Mapping)
        ][:8],
    }


# ============================================================
# Episode builder
# ============================================================

def build_causal_episode(
    *,
    signal: DetectionSignal,
    cut: CausalCut,
    record_key: str,
    evolution_direction: str = "",
    allowed_modules: Sequence[str] = (),
    attempts: Sequence[FailureAttempt] = (),
) -> CausalFailureEpisode:
    """Assemble the canonical causal episode for one triggered detection.

    The episode is built strictly from the projected signal + the cut; no
    third pass of owner refinement happens here. The L1 executable-
    completeness gate has already been applied inside ``cut``; if the cut
    was demoted, the episode lands on ``LAYER_NONE`` with the demotion
    reason preserved. The repairability verdict is empty here; it is
    applied by ``repairability.analyze`` + ``episode.with_repairability``.

    For non-actionable cuts (DETECTOR / NONE), the resulting episode still
    carries the full §2.3 field set so a non-evolvable / framework-defect
    record keeps the same shape as a repairable one. Downstream's
    ``repairability.analyze`` is the only consumer that reads the status
    out.
    """
    legal_modules = tuple(dict.fromkeys(str(value) for value in allowed_modules))
    downstream = _downstream_consumption(signal=signal, skill_layer=cut.skill_layer)
    candidate_id, candidate_kind, origin_refs, dependency_refs = _select_candidate(
        signal=signal, cut=cut
    )

    evidence = (
        cut.evidence if isinstance(cut.evidence, Mapping) else {}
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
        raw_missing = ()
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
            if cut.reason_code == REASON_PROVIDER_MALFORMED else ()
        )
    failed_fields = _sorted_dedup(raw_failed_fields)
    malformed_fields = (
        failed_fields if cut.reason_code == REASON_PROVIDER_MALFORMED else ()
    )

    repair_frontier, rejected_frontier_fields = _compile_failure_edges(
        cut=cut, signal=signal, downstream=downstream,
        legal_modules=legal_modules, evolution_direction=evolution_direction,
    )
    primary_edge = repair_frontier[0] if repair_frontier else None

    # Surface envelope: the broad closed vocabulary for the allowed module
    # set, even when the cut produced no frontier. This keeps the prompt
    # projection stable for the L2 no-registered-runtime-action path (C2
    # witness: ``allowed_modules`` is non-empty but the frontier is empty;
    # ``repairability.analyze`` then declares
    # ``UNADDRESSABLE_FRAMEWORK_DEFECT``).
    if cut.skill_layer == LAYER_L2:
        from sehd_rise.skills.vocabulary.contracts import COURT_LOCAL_ACTIONS
        legal_operations = tuple(sorted(COURT_LOCAL_ACTIONS))
        # L2 envelopes do not enumerate per-module output fields / observables:
        # each RepairPlan's effect contract names its own. Leaving the broad
        # envelope's per-field list empty for L2 is by construction.
        legal_output_fields: tuple[str, ...] = ()
        legal_observables: tuple[str, ...] = ()
    else:
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
    repairable_surface = RepairableSurface(
        legal_modules=legal_modules,
        legal_operations=legal_operations,
        legal_output_fields=legal_output_fields,
        legal_observables=legal_observables,
    )
    legal_repair_operations = _legal_repair_operations(
        cut=cut, frontier=repair_frontier
    )

    candidate_rows = (
        _candidate_rows(signal, candidate_kind)
        if candidate_kind != "unknown" else []
    )
    present_fields = _present_fields(candidate_rows[0]) if candidate_rows else ()
    evidence_refs = _evidence_refs(signal, signal.responsible_step)
    required_evidence_refs = _sorted_dedup(
        [*origin_refs, *evidence_refs]
    )[:8]

    completeness = "complete"
    if not candidate_id or primary_edge is None:
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
        failure_edge=primary_edge,
        evidence_refs=evidence_refs,
        completeness=completeness,
        repair_frontier=repair_frontier,
    )

    detection_snapshot = {
        "state": signal.state,
        "evolution_direction": str(evolution_direction or ""),
        "defect": _enum_value(signal.defect),
        "process_diagnostic": _enum_value(signal.process_diagnostic),
        "defect_confidence": signal.defect_confidence,
        "diagnostic_only": signal.diagnostic_only,
    }
    attribution_snapshot = {
        "skill_layer": cut.skill_layer,
        "owner": cut.owner,
        "secondary_owner": cut.secondary_owner,
        "reason_code": cut.reason_code,
        "failure_origin": cut.failure_origin,
        "blocking_predicate": cut.blocking_predicate,
        "rejected_frontier_fields": list(rejected_frontier_fields),
    }
    terminal_basis = _terminal_basis_payload(signal)
    observable_effect = {
        "consumer": str(downstream.get("consumer") or ""),
        "qualification_state": str(downstream.get("qualification_state") or ""),
        "certificate_state": str(downstream.get("certificate_state") or ""),
        "quarantine_state": str(downstream.get("quarantine_state") or ""),
        "coverage_state": str(downstream.get("coverage_state") or ""),
        "court_effect": str(downstream.get("court_effect") or ""),
    }

    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "record_key": record_key,
        "detection": dict(detection_snapshot),
        "attribution": dict(attribution_snapshot),
        "terminal_basis": dict(terminal_basis),
        "causal_slice": causal_slice.to_dict(),
        "repairable_surface": repairable_surface.to_dict(),
    }
    episode_id = _stable_identity(identity_payload)

    return CausalFailureEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id=episode_id,
        record_key=record_key,
        terminal_state=signal.state,
        terminal_basis=terminal_basis,
        blocking_predicate=cut.blocking_predicate,
        causal_failure_edge=primary_edge,
        failure_owner=cut.owner,
        skill_layer=cut.skill_layer,
        repairable_surface=repairable_surface,
        legal_repair_operations=legal_repair_operations,
        observable_effect=observable_effect,
        repairability_status="",
        repairability_reason="",
        failure_origin=cut.failure_origin,
        secondary_owner=cut.secondary_owner,
        reason_code=cut.reason_code,
        evidence=dict(evidence),
        detection_snapshot=detection_snapshot,
        attribution_snapshot=attribution_snapshot,
        causal_slice=causal_slice,
        attempts=tuple(attempts),
    )


# ============================================================
# Runtime observation (preserved from old failure_episode for Phase 6)
# ============================================================

def build_runtime_observation(
    signal: DetectionSignal,
    *,
    allowed_modules: Sequence[str] = (),
    condition_feature_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the bounded structured observation the reflection model sees.

    Derived from the signal and the source run's pre-call condition-feature
    projections: no labels, no case identity. It is an observation, not a
    verdict or a correctness claim. Phase 6's planning layer uses this to
    assemble the reflection prompt; it stays label-free.
    """
    prosecution_rows = _candidate_rows(signal, "error_certificate")
    defense_rows = _candidate_rows(signal, "defense_certificate")
    proof = signal.proof_adjudication or {}

    def _id_count(key: str) -> int:
        value = proof.get(key)
        return len(value) if isinstance(value, (list, tuple)) else 0

    def _proof_count(key: str) -> int:
        if key not in proof:
            return 0
        try:
            return max(0, int(proof.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    surviving_ids = proof.get("surviving_error_certificate_ids")
    surviving_id_set = (
        {str(value) for value in surviving_ids}
        if isinstance(surviving_ids, (list, tuple)) else set()
    )
    surviving_error_certificates = (
        len(surviving_id_set)
        if isinstance(surviving_ids, (list, tuple))
        else max(len(prosecution_rows) - _id_count("quarantined_error_certificate_ids"), 0)
    )

    def _carrier_descriptor(row: Mapping[str, Any]) -> dict[str, Any]:
        candidate_id = str(row.get("source_candidate_id") or "")
        census_row = next(
            (
                entry for entry in signal.census
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

    verdict_state = signal.state
    winning_error_certificate_id = str(
        proof.get("winning_error_certificate_id") or ""
    )
    verdict_carriers: list[dict[str, Any]] = []
    witness_certificates: list[dict[str, Any]] = []
    if verdict_state == "verified_error":
        for row in prosecution_rows:
            certificate_id = str(row.get("certificate_id") or "")
            if not certificate_id:
                continue
            descriptor = _carrier_descriptor(row)
            if certificate_id in surviving_id_set:
                verdict_carriers.append(descriptor)
            else:
                witness_certificates.append(descriptor)
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
        "surviving_error_certificates": max(surviving_error_certificates, 0),
        "quarantined_error_certificates": _id_count("quarantined_error_certificate_ids"),
        "defense_certificates": _proof_count(
            "valid_defense_certificate_count"
        ) or max(len(defense_rows) - _id_count("quarantined_defense_certificate_ids"), 0),
        "candidates_covered_by_clean_defense": _id_count("clean_covered_candidate_ids"),
        "positive_candidates": _id_count("positive_candidate_ids"),
        "consensus_gate_blocked": _id_count("consensus_gate_blocked_ids"),
        "verdict_state": verdict_state,
        "verdict_carriers": verdict_carriers,
        "witness_certificates": witness_certificates,
    }
    field_presence = {
        "candidate_count": signal.candidate_count,
        "prosecution_certificates": len(prosecution_rows),
        "defense_certificates": len(defense_rows),
        "responsible_step": signal.responsible_step is not None,
        "prosecution_coverage": signal.prosecution_coverage.state,
        "defense_coverage": signal.defense_coverage.state,
        "state": signal.state,
        "defect": _enum_value(signal.defect),
    }
    owner_fields = {
        module: list(MODULE_OBSERVABLE_FIELDS.get(module, ()))
        for module in allowed_modules
    }
    return {
        "authority": {
            "runtime_valid": signal.runtime_valid is True,
            "decision_authority": signal.decision_authority,
        },
        "observed_fields": field_presence,
        "court_balance": court_balance,
        "terminal_basis": _terminal_basis_payload(signal),
        "runtime_condition_features": dict(condition_feature_trace or {}),
        "non_empty_evidence": {
            "census": signal.candidate_count > 0,
            "prosecution": bool(prosecution_rows),
            "defense": bool(defense_rows),
        },
        "diagnostic_gap": {
            "defect": _enum_value(signal.defect),
            "process_diagnostic": _enum_value(signal.process_diagnostic),
            "primary_owner": "",  # filled by callers with cut.owner
            "secondary_owner": "",
            "confidence": signal.defect_confidence,
        },
        "module_output_ownership": owner_fields,
    }


# ============================================================
# Prompt projection
# ============================================================

def project_for_prompt(
    episode: CausalFailureEpisode,
    *,
    historical_refs: Sequence[HistoricalEpisodeReference] = (),
) -> dict[str, Any]:
    """Project the episode into the bounded reflection context.

    Excludes record key, case identity, attempts, gold and evaluator data,
    and any unbounded trajectory text. The repairability verdict is
    included only as a closed-vocabulary status token so the prompt can
    frame WHY the case entered (or did not enter) evolution.
    """
    slice_payload = episode.causal_slice
    projection: dict[str, Any] = {
        "schema_version": episode.schema_version,
        "terminal_state": episode.terminal_state,
        "blocking_predicate": episode.blocking_predicate,
        "failure_owner": episode.failure_owner,
        "skill_layer": episode.skill_layer,
        "failure_origin": episode.failure_origin,
        "secondary_owner": episode.secondary_owner,
        "repairability_status": episode.repairability_status,
        "repairability_reason": episode.repairability_reason,
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
        "repairable_surface": episode.repairable_surface.to_dict(),
        "legal_repair_operations": list(episode.legal_repair_operations),
        "observable_effect": dict(episode.observable_effect),
    }
    if historical_refs:
        projection["historical_references"] = [
            reference.to_dict() for reference in historical_refs
        ]
    return projection


# ============================================================
# Validation
# ============================================================

def validate_causal_episode(
    episode: CausalFailureEpisode,
) -> tuple[bool, str]:
    """Validate the episode invariants.

    Returns ``(valid, reason)``. The episode is valid only when its schema,
    stable identity, sorted/deduped field lists and prompt firewall all
    hold.
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
        "detection": dict(episode.detection_snapshot),
        "attribution": dict(episode.attribution_snapshot),
        "terminal_basis": dict(episode.terminal_basis),
        "causal_slice": slice_payload.to_dict(),
        "repairable_surface": episode.repairable_surface.to_dict(),
    }
    if episode.episode_id != _stable_identity(identity_payload):
        return False, "episode_id_mismatch"
    projection = project_for_prompt(episode)
    if "record_key" in _canonical_json(projection):
        return False, "record_key_leaks_into_prompt"
    for forbidden in EVALUATOR_ONLY_KEYS:
        if forbidden in _canonical_json(projection):
            return False, f"evaluator_only_field_leaks_into_prompt:{forbidden}"
    return True, ""


# ============================================================
# Stable attempt identity (preserved from old failure_episode)
# ============================================================

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


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "CausalFailureEpisode",
    "CausalSlice",
    "EVALUATOR_ONLY_KEYS",
    "FailureAttempt",
    "FailureEdge",
    "HistoricalEpisodeReference",
    "RepairableSurface",
    "SCHEMA_VERSION",
    "attempt_id_for",
    "build_causal_episode",
    "build_mechanism_signature",
    "build_runtime_observation",
    "project_for_prompt",
    "proposal_signature_for",
    "validate_causal_episode",
]