"""Deterministic failure classifier for SEHD v6.

Attribution answers exactly one question: *at which layer, on which
artifact, did the failure happen?* Routing an unownable failure to some
module anyway ends in no-edit steps or no-effect replays, so classification
is a pure deterministic function of the addressable failure shape of one
detection record — no guessing, no LLM, no heuristics. The two evolvable
surfaces are:

- L1 = the 8 provider-rule modules (artifact = the provider-emitted
  candidate / certificate / evidence field),
- L2 = the adjudication policies (artifacts = consensus-gate policy /
  coverage-validity policy / boundary-consumption policy).

Classification is a pure deterministic function of the addressable
failure shape of one detection record — no guessing, no LLM, no
heuristics. Every predicate test goes through the ``v6_contracts``
``L2_PREDICATES`` registry (single source of truth); this module never
re-implements predicate logic.

Input contract for ``classify_failure(record)``
-----------------------------------------------
``record`` is the per-detection record dict with these addressable
fields (all defensively read; missing fields never raise):

- ``record["adjudication"]``: proof-court result dict with keys
  ``state``, ``prosecution_coverage``, ``defense_coverage``,
  ``valid_error_certificate_count``, ``valid_defense_certificate_count``,
  ``surviving_error_certificate_ids``,
  ``quarantined_error_certificate_ids``,
  ``quarantined_defense_certificate_ids``,
  ``consensus_gate_applied``, ``consensus_gate_blocked_ids``,
  ``required_candidate_count``, ``error_audits``, ``defense_audits``.
- ``record["boundary_path_state"]``: str, may be absent/empty.
- ``record["provider_artifacts"]``: optional list; each item has
  ``{"kind": "candidate" | "error_certificate" | "defense_certificate",
  "module_hint": str, "malformed": bool, "missing_fields": [str]}``.
  The evolution runner builds this list as the complete integrity
  projection of one detection (construct-stage candidate counts, court
  audit failed-checks, quarantine tallies, appeal provider status, and the
  single gold-informed FN+omission signal).

Classification rules (priority order)
-------------------------------------
1. A semantically missing provider artifact -> L1, owning module resolved
   via ``ARTIFACT_OWNER``. A malformed emitted artifact is classified by
   ``classify_malformed_origin`` into a typed origin: only
   ``provider_generation_nonconformance`` (a registered relational invariant
   failed on provider-emitted fields) routes to L1; ``validator_contract_fault``,
   ``transport_or_parse_fault`` and ``route_or_config_fault`` route to the
   detector inference-contract boundary, where bounded in-call repair owns
   them and long-term Skill formation must not relearn the validator contract.
2. Boundary path verified_clean but terminal unresolved -> L2,
   ``boundary_consumption_policy``.
3. Consensus gate blocked nonempty -> L2, ``consensus_gate_policy``.
4. Either side claims coverage=complete with zero valid certificates
   on that side -> L2, ``coverage_validity_policy``.
5. Fall-through: unresolved terminal -> ``no_actionable``; terminal
   verdict present but internally inconsistent ->
   ``detector_layer_out_of_scope``; otherwise ``no_actionable``.

Why this order: a missing or malformed provider artifact is the most
upstream failure — the adjudication layer cannot even observe facts
correctly if its inputs are broken, so artifact integrity must be
checked before any adjudication-layer pattern (rules 2-4), which all
presume well-formed provider output.
"""

from __future__ import annotations

from typing import Any, Mapping

from .genome import MODULE_IDS
from .module_charter import MODULE_ACTION_OPCODES, MODULE_OPERATION_TARGETS
from .v6_contracts import (
    L2_ARTIFACT_APPEAL_MECHANISM,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_PREDICATES,
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
    FailureAttribution,
    build_adjudication_facts,
)

# ============================================================
# L1 ownership map: provider artifact kind -> owning rule module
# ============================================================

ARTIFACT_OWNER: dict[str, str] = {
    "candidate": "candidate_trigger",
    "error_certificate": "evidence_obligation",
    "defense_certificate": "clean_defense_boundary",
}

PROVIDER_ARTIFACT_KINDS = frozenset(ARTIFACT_OWNER)

# Reason codes (closed vocabulary, local to the classifier).
REASON_PROVIDER_MISSING = "provider_artifact_missing"
REASON_PROVIDER_MALFORMED = "provider_artifact_malformed"
REASON_CONSENSUS_BLOCKED = "consensus_gate_blocked"
REASON_HOLLOW_DEFENSE = "hollow_complete_defense"
REASON_HOLLOW_PROSECUTION = "hollow_complete_prosecution"
REASON_BOUNDARY_CONFLICT = "boundary_terminal_conflict"
REASON_INTERNAL_INCONSISTENCY = "internal_inconsistency"
REASON_NO_FAILURE_SHAPE = "no_failure_shape"
# v6.1 扩展：新失败形状
REASON_APPEAL_PENDING = "appeal_pending_blocked"
REASON_APPEAL_FAILED = "appeal_failed_with_errors"
REASON_LOCALIZATION_FALLBACK = "localization_temporal_fallback"
REASON_RESPONSIBLE_STEP_MISSING = "responsible_step_missing"
REASON_QUARANTINE_PATTERN = "quarantine_pattern_dominant"


# ============================================================
# Malformed failure-origin taxonomy (single semantic source)
# ============================================================
#
# A single ``malformed`` boolean cannot tell whether the provider produced
# non-conforming content (an evolvable provider-instruction gap) or the
# validator/transport/route layer broke the artifact (a detector/inference
# boundary that bounded in-call repair owns). ``classify_failure`` therefore
# routes every malformed artifact through this typed origin before deciding
# its layer. Only ``provider_generation_nonconformance`` is L1-addressable;
# the other three origins stop at their respective boundaries so long-term
# Skill formation never relearns a validator contract or a transport fault.
#
# Origins are derived purely from already-present typed evidence fields on the
# artifact (``failed_invariants`` / ``failed_fields`` / ``missing_fields`` /
# ``source``) — no guessing, no case identity, no heuristics.

ORIGIN_PROVIDER_GENERATION = "provider_generation_nonconformance"
ORIGIN_VALIDATOR_CONTRACT = "validator_contract_fault"
ORIGIN_TRANSPORT_PARSE = "transport_or_parse_fault"
ORIGIN_ROUTE_CONFIG = "route_or_config_fault"

# An artifact that carries a registered relational invariant failure proves
# the provider emitted populated fields whose cross-field / trajectory-
# addressability relation is wrong. The validator already knows the invariant
# and reported it; the provider instruction can be evolved to not produce that
# relation. This is the only malformed origin routed to L1.
ORIGINS_ROUTED_TO_L1 = frozenset({ORIGIN_PROVIDER_GENERATION})

_FAILURE_ORIGINS = (
    ORIGIN_PROVIDER_GENERATION,
    ORIGIN_VALIDATOR_CONTRACT,
    ORIGIN_TRANSPORT_PARSE,
    ORIGIN_ROUTE_CONFIG,
)


def classify_malformed_origin(artifact: Mapping[str, Any]) -> str:
    """Return the typed failure origin for one malformed provider artifact.

    Decision order (first match wins; every branch is evidence-backed):

    1. ``transport_or_parse_fault`` — the artifact was flagged malformed by a
       provider error / appeal crash signal and carries no field-level audit
       (``failed_fields`` and ``failed_invariants`` both empty). A provider
       that errored or could not be parsed did not produce content to evolve.
    2. ``provider_generation_nonconformance`` — the artifact carries a
       registered relational ``failed_invariants`` entry. The provider emitted
       populated fields that violate a known relation; the verifier-instruction
       surface can be evolved to not produce that relation.
    3. ``validator_contract_fault`` — the artifact carries only
       ``failed_fields`` with no invariant and no missing fields. The fields
       exist but the validator's own field mapping / schema rejected them; the
       fault is in the validator contract, not provider instruction.
    4. ``route_or_config_fault`` — fall-closed default. The malformed signal
       has no typed audit evidence (no invariants, no fields, no transport
       marker). Its owner cannot be resolved from evidence, so it is routed to
       the inference boundary rather than guessed into L1.
    """
    def values(key: str) -> list[str]:
        raw = artifact.get(key)
        return list(raw) if isinstance(raw, (list, tuple, set, frozenset)) else []

    failed_invariants = values("failed_invariants")
    failed_fields = values("failed_fields")
    missing_fields = values("missing_fields")
    source = str(artifact.get("source") or "")

    # 1. Transport / parse: a provider error or appeal crash with no field
    #    audit. ``appeal_status == provider_error`` projects this shape (see
    #    ``project_provider_artifacts``); a crash did not emit content.
    is_provider_error_signal = (
        source in ("appeal_status", "provider_error")
        or "provider_error" in source
    )
    if is_provider_error_signal and not failed_invariants and not failed_fields:
        return ORIGIN_TRANSPORT_PARSE

    # 2. Provider generation nonconformance: a registered relational invariant
    #    failed on populated provider-emitted fields. The validator already
    #    encoded the invariant, so this is an evolvable provider-instruction
    #    gap, not a validator contract to relearn.
    if failed_invariants:
        return ORIGIN_PROVIDER_GENERATION

    # 3. Validator contract fault: only field-level failures, no invariant.
    #    The provider emitted the fields; the validator's own mapping/schema
    #    rejected them — a detector boundary, not an L1 surface.
    if failed_fields and not missing_fields:
        return ORIGIN_VALIDATOR_CONTRACT

    # 4. Route / config fault: no typed audit evidence to resolve an owner.
    #    Fall closed to the inference boundary rather than guessing L1.
    return ORIGIN_ROUTE_CONFIG


def origin_layer(origin: str) -> str:
    """Map a malformed failure origin to its owning layer.

    Only ``provider_generation_nonconformance`` is L1-addressable; the other
    three origins are detector / inference-contract boundaries owned by
    bounded in-call repair, not long-term Skill formation.
    """
    if origin == ORIGIN_PROVIDER_GENERATION:
        return LAYER_L1
    return LAYER_DETECTOR


def _boundary_repair_owner(origin: str) -> str:
    """Name the bounded in-call repair owner for a boundary origin.

    These owners are diagnostic labels for the inference-contract boundary:
    they name which non-evolvable repair surface owns the fault, so the
    auditor can tell a validator-mapping fault from a transport fault without
    routing either into long-term Skill formation.
    """
    if origin == ORIGIN_TRANSPORT_PARSE:
        return "transport_parse_retry"
    if origin == ORIGIN_VALIDATOR_CONTRACT:
        return "validator_contract_repair"
    return "route_config_repair"


# ============================================================
# L1 executable-completeness gate (Fix #2 strong invariant)
# ============================================================
#
# Strong invariant: ``classify_failure`` returns ``LAYER_L1`` => the
# attribution must be able to compile at least one executable repair edge
# with owner / route / object / field / operation / postcondition. A
# "nominally L1 but no execution semantics" intermediate state is forbidden.
#
# This gate is the source-side enforcement. It mirrors the field-extraction
# logic of ``_compile_repair_frontier`` (failure_episode.py) without
# compiling the full episode: it checks that the owner module has registered
# opcodes and that the evidence carries at least one field belonging to an
# opcode's target set. Direction-specific narrowing happens downstream in
# ``_field_is_executable``; this gate is direction-agnostic so it can run
# inside ``classify_failure`` (which has no evolution_direction).

# Reason codes that carry their executable field directly on the evidence
# (missing_fields / failed_invariants / failed_fields), as opposed to those
# whose field must be synthesized from the terminal basis.
_REASON_CARRIES_ARTIFACT_FIELDS = frozenset({
    REASON_PROVIDER_MISSING,
    REASON_PROVIDER_MALFORMED,
})

# Reason codes whose executable field is synthesized from the failure shape
# itself (not from artifact frontier fields). Each maps to the owner module
# and the canonical executable field for that shape.
_REASON_SYNTHESIZED_FIELD: dict[str, tuple[str, str]] = {
    # Localization fallback / responsible step missing: the earliest-origin
    # tiebreak module's select_earliest_origin opcode targets responsible_event_id.
    REASON_LOCALIZATION_FALLBACK: (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    REASON_RESPONSIBLE_STEP_MISSING: (
        "earliest_origin_tiebreak", "responsible_event_id",
    ),
    # Quarantine pattern dominant: schema mismatch is a verifier-instruction
    # invariant surface (certificate_id), evidence missing is an
    # evidence-obligation field surface. The dominant reason is carried in
    # evidence["dominant_reason"].
    REASON_QUARANTINE_PATTERN: (
        "atomic_verifier_instruction", "certificate_id",
    ),
}


def _module_has_executable_field(module: str, field: str) -> bool:
    """Direction-agnostic check: does this field belong to any registered
    operation target of the module?"""
    targets_by_op = MODULE_OPERATION_TARGETS.get(module, {})
    return any(
        field in targets
        for targets in targets_by_op.values()
    )


def can_compile_repair_obligation(attribution: FailureAttribution) -> bool:
    """Return True iff this L1 attribution can compile at least one executable
    repair edge with owner / route / object / field / operation / postcondition.

    This is the source-side enforcement of the strong invariant
    ``LAYER_L1 => executable``. It does not compile the episode; it checks
    that the owner module has registered opcodes and that the evidence
    carries at least one field belonging to an opcode target. When this
    returns False the classifier must not return ``LAYER_L1`` — it demotes
    to ``LAYER_NONE`` so no "nominally L1 but no execution semantics"
    intermediate state reaches the evolution runner.
    """
    if attribution.layer != LAYER_L1:
        return False
    owner = attribution.artifact
    if owner not in MODULE_ACTION_OPCODES:
        return False
    opcodes = MODULE_ACTION_OPCODES.get(owner, ())
    if not opcodes:
        return False
    reason = attribution.reason_code
    evidence = (
        attribution.evidence if isinstance(attribution.evidence, Mapping) else {}
    )

    # 1. Artifact-carried fields: missing_fields / failed_invariants /
    #    failed_fields from the proof-cut frontier.
    if reason in _REASON_CARRIES_ARTIFACT_FIELDS:
        for key in ("failed_invariants", "failed_fields", "missing_fields"):
            raw = evidence.get(key)
            if isinstance(raw, (list, tuple, set, frozenset)):
                for value in raw:
                    field = "certificate_id" if str(value) == "certificate" else str(value)
                    if _module_has_executable_field(owner, field):
                        return True
        # Fall through to the frontier rows (artifact_frontier may carry
        # fields the top-level evidence does not).
        frontier = evidence.get("artifact_frontier")
        if isinstance(frontier, list):
            for row in frontier:
                if not isinstance(row, Mapping):
                    continue
                for key in ("failed_invariants", "failed_fields", "missing_fields"):
                    raw = row.get(key)
                    if isinstance(raw, (list, tuple, set, frozenset)):
                        for value in raw:
                            field = (
                                "certificate_id"
                                if str(value) == "certificate"
                                else str(value)
                            )
                            if _module_has_executable_field(owner, field):
                                return True
        return False

    # 2. Synthesized-field reasons: the field is canonical for the shape.
    if reason in _REASON_SYNTHESIZED_FIELD:
        synth_owner, field = _REASON_SYNTHESIZED_FIELD[reason]
        # The quarantine reason's owner depends on the dominant sub-reason;
        # if the evidence names evidence_obligation, use that owner instead.
        if reason == REASON_QUARANTINE_PATTERN:
            dominant = str(evidence.get("dominant_reason") or "")
            if dominant == "evidence_missing":
                synth_owner = "evidence_obligation"
        if owner != synth_owner:
            # The classifier routed this to a different owner than the
            # synthesized field expects; check if the actual owner can still
            # execute the canonical field.
            return _module_has_executable_field(owner, field)
        return _module_has_executable_field(synth_owner, field)

    # 3. Unknown L1 reason code with no field extraction rule: fail closed.
    return False


# ============================================================
# Helpers (pure, defensive record reading)
# ============================================================

def _find_bad_artifacts(
    artifacts: Any,
) -> tuple[tuple[dict[str, Any], ...], str, str] | None:
    """Return the complete upstream artifact frontier for one failure kind.

    A malformed emitted object is classified by ``classify_malformed_origin``
    into a typed origin; only ``provider_generation_nonconformance`` is
    L1-addressable, the rest are detector/inference boundaries. Malformed
    artifacts take precedence over semantic absence (a broken emitted object
    is upstream of a missing one). Otherwise every missing artifact is
    retained. The previous first-match helper silently discarded sibling
    proof blockers, making reflection depend on list order rather than the
    proof court's causal cut.

    Returns ``(frontier, reason_code, failure_origin)`` where ``failure_origin``
    is one of ``_FAILURE_ORIGINS`` for malformed artifacts and the empty
    string for missing artifacts.
    """
    if not isinstance(artifacts, list):
        return None
    # Group malformed artifacts by their typed origin so the classifier can
    # route each origin to its own layer. L1-addressable origins (provider
    # generation nonconformance) take precedence over boundary origins,
    # because an evolvable provider-instruction gap is the most upstream
    # actionable failure. Among boundary origins the order is transport >
    # validator > route, matching fail-closed precedence.
    malformed_by_origin: dict[str, list[dict[str, Any]]] = {
        origin: [] for origin in _FAILURE_ORIGINS
    }
    missing: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in PROVIDER_ARTIFACT_KINDS:
            continue
        if bool(item.get("malformed")):
            origin = classify_malformed_origin(item)
            malformed_by_origin[origin].append(item)
            continue
        missing_fields = item.get("missing_fields")
        if missing_fields not in (None, "") and not isinstance(
            missing_fields, (list, tuple, set)
        ):
            # Non-sequence missing_fields is itself a malformed signal with
            # no typed audit — route to the fail-closed boundary origin.
            malformed_by_origin[ORIGIN_ROUTE_CONFIG].append(item)
            continue
        if list(missing_fields or []):
            missing.append(item)
    # Precedence: L1-addressable origins first, then boundary origins in
    # fail-closed order. This keeps an evolvable provider gap from being
    # masked by a co-occurring transport fault.
    for origin in _FAILURE_ORIGINS:
        bucket = malformed_by_origin[origin]
        if bucket:
            return tuple(bucket), REASON_PROVIDER_MALFORMED, origin
    if missing:
        return tuple(missing), REASON_PROVIDER_MISSING, ""
    return None


def _artifact_evidence(
    artifact: dict[str, Any], reason: str,
) -> dict[str, Any]:
    """Canonical per-artifact evidence used by classifier and frontier."""
    def values(key: str) -> list[Any]:
        raw = artifact.get(key)
        return list(raw) if isinstance(raw, (list, tuple, set)) else []

    return {
        "kind": str(artifact.get("kind") or ""),
        "module_hint": str(artifact.get("module_hint") or ""),
        "owner_module": _resolve_l1_module(artifact, reason),
        "missing_fields": values("missing_fields"),
        "failed_fields": values("failed_fields"),
        "failed_invariants": values("failed_invariants"),
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "source": str(artifact.get("source") or ""),
    }


def _resolve_l1_module(artifact: dict[str, Any], reason: str) -> str:
    """Deterministic artifact-kind -> module mapping; a registered
    ``module_hint`` takes precedence over the default owner.

    A certificate that was emitted but failed field validation is a
    certificate-quality gap owned by the verifier instruction; a certificate
    that should exist but was never emitted is an evidence-obligation gap.
    """
    hint = str(artifact.get("module_hint") or "")
    if hint in MODULE_IDS:
        return hint
    if (
        str(artifact.get("kind") or "") == "error_certificate"
        and reason == REASON_PROVIDER_MALFORMED
    ):
        return "atomic_verifier_instruction"
    return ARTIFACT_OWNER[str(artifact["kind"])]


def _attr(layer: str, artifact: str, reason: str, evidence: dict[str, Any]) -> FailureAttribution:
    return FailureAttribution(
        layer=layer, artifact=artifact, reason_code=reason, evidence=evidence
    )


# ============================================================
# Classifier
# ============================================================

def classify_failure(record: dict[str, Any]) -> FailureAttribution:
    """Classify one detection record into a deterministic attribution.

    Pure function: identical records always yield identical
    attributions. See the module docstring for the input contract and
    the priority rationale.

    Strong invariant (Fix #2): if the raw classification returns
    ``LAYER_L1`` but the attribution cannot compile at least one executable
    repair edge (owner / route / object / field / operation /
    postcondition), it is demoted to ``LAYER_NONE`` with reason
    ``demoted_l1_no_executable_obligation``. This forbids the "nominally L1
    but no execution semantics" intermediate state from reaching the
    evolution runner, where it would raise ``UnaddressableFailure`` and skip
    reflection entirely.
    """
    attribution = _classify_failure_raw(record)
    if attribution.layer == LAYER_L1 and not can_compile_repair_obligation(attribution):
        return _attr(
            LAYER_NONE,
            "",
            "demoted_l1_no_executable_obligation",
            {
                "classified_layer": attribution.layer,
                "classified_artifact": attribution.artifact,
                "classified_reason": attribution.reason_code,
                "classified_evidence": dict(attribution.evidence),
            },
        )
    return attribution


def _classify_failure_raw(record: dict[str, Any]) -> FailureAttribution:
    """Raw classification before the L1 executable-completeness gate."""
    court_result = record.get("adjudication")
    if not isinstance(court_result, dict):
        court_result = {}
    facts = build_adjudication_facts(
        court_result,
        boundary_path_state=str(record.get("boundary_path_state") or ""),
    )

    # Rule 1 — most upstream: provider artifact integrity.
    bad = _find_bad_artifacts(record.get("provider_artifacts"))
    if bad is not None:
        artifacts, reason, failure_origin = bad
        artifact = artifacts[0]
        artifact_frontier = [
            _artifact_evidence(item, reason) for item in artifacts
        ]
        primary = artifact_frontier[0]
        if reason == REASON_PROVIDER_MALFORMED:
            # Typed origin routing: only provider generation nonconformance
            # (a registered relational invariant failed on provider-emitted
            # fields) is L1-addressable. Validator/transport/route faults are
            # detector / inference-contract boundaries owned by bounded
            # in-call repair — long-term Skill formation must not relearn them.
            layer = origin_layer(failure_origin)
            if layer == LAYER_L1:
                return _attr(
                    LAYER_L1,
                    str(primary["owner_module"]),
                    reason,
                    {
                        **primary,
                        "failure_origin": failure_origin,
                        "artifact_frontier": artifact_frontier,
                    },
                )
            # Boundary origins: route to the inference contract boundary with
            # the typed origin and the intended (but non-evolvable) owner, so
            # bounded in-call repair owns it instead of genome formation.
            return _attr(
                LAYER_DETECTOR,
                "inference_contract_boundary",
                reason,
                {
                    "kind": str(artifact.get("kind") or ""),
                    "intended_owner": _resolve_l1_module(artifact, reason),
                    "missing_fields": list(primary["missing_fields"]),
                    "failed_fields": list(primary["failed_fields"]),
                    "failed_invariants": list(primary["failed_invariants"]),
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "source": str(artifact.get("source") or ""),
                    "failure_origin": failure_origin,
                    "repair_owner": _boundary_repair_owner(failure_origin),
                    "artifact_frontier": artifact_frontier,
                },
            )
        return _attr(
            LAYER_L1,
            str(primary["owner_module"]),
            reason,
            {
                **primary,
                "failure_origin": "",
                "artifact_frontier": artifact_frontier,
            },
        )

    # Rule 2 — a verified-clean boundary is the closest executable
    # cause of an unresolved terminal. It takes priority over upstream gate
    # telemetry because the registered policy can consume this exact state.
    if L2_PREDICATES["boundary_state_conflicts_with_terminal"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_BOUNDARY_CONSUMPTION,
            REASON_BOUNDARY_CONFLICT,
            {
                "boundary_path_state": facts.boundary_path_state,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 3 — consensus gate blocked live candidates. The gate is a
    # protection mechanism: blocking low-confidence candidates only becomes
    # an L2 failure when the court cannot reach a verdict because of it. A
    # gate that filtered candidates yet still produced a verdict is the
    # detector working as designed, not an addressable failure.
    if (
        L2_PREDICATES["consensus_gate_blocked_nonempty"](facts)
        and facts.terminal_state == "unresolved"
    ):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_CONSENSUS_GATE,
            REASON_CONSENSUS_BLOCKED,
            {
                "consensus_gate_blocked_count": facts.consensus_gate_blocked_count,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4 — hollow "complete" coverage: both sides go through the
    # registered predicates, so the vocabulary stays single-sourced.
    if L2_PREDICATES["defense_coverage_complete_and_zero_certificates"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_COVERAGE_VALIDITY,
            REASON_HOLLOW_DEFENSE,
            {
                "defense_coverage": facts.defense_coverage,
                "valid_defense_certificate_count": facts.valid_defense_certificate_count,
            },
        )
    if L2_PREDICATES["prosecution_coverage_complete_and_zero_certificates"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_COVERAGE_VALIDITY,
            REASON_HOLLOW_PROSECUTION,
            {
                "prosecution_coverage": facts.prosecution_coverage,
                "valid_error_certificate_count": facts.valid_error_certificate_count,
            },
        )

    # Rule 4.1 — appeal pending blocked terminal.
    if L2_PREDICATES["appeal_pending_and_terminal_unresolved"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_APPEAL_MECHANISM,
            REASON_APPEAL_PENDING,
            {
                "appeal_status": facts.appeal_status,
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4.2 — v6.1: appeal failed with surviving errors.
    if L2_PREDICATES["appeal_failed_with_surviving_errors"](facts):
        return _attr(
            LAYER_L2,
            L2_ARTIFACT_APPEAL_MECHANISM,
            REASON_APPEAL_FAILED,
            {
                "appeal_status": facts.appeal_status,
                "surviving_error_count": facts.surviving_error_count,
            },
        )

    # Rule 4.3 — v6.1: localization fallback used.
    if L2_PREDICATES["localization_failed_with_temporal_fallback"](facts):
        return _attr(
            LAYER_L1,
            "earliest_origin_tiebreak",
            REASON_LOCALIZATION_FALLBACK,
            {
                "localization_status": facts.localization_status,
            },
        )

    # Rule 4.4 — v6.1: responsible step missing with error verdict.
    # Only fires when the court explicitly reported a localization attempt
    # but produced no responsible step; a missing field in the record is
    # not a failure shape (the fixture simply never ran localization).
    if (
        L2_PREDICATES["responsible_step_missing_and_verdict_error"](facts)
        and facts.localization_status != ""
    ):
        return _attr(
            LAYER_L1,
            "earliest_origin_tiebreak",
            REASON_RESPONSIBLE_STEP_MISSING,
            {
                "terminal_state": facts.terminal_state,
            },
        )

    # Rule 4.5 — v6.1: quarantine pattern analysis.
    if L2_PREDICATES["quarantine_schema_mismatch_dominant"](facts):
        return _attr(
            LAYER_L1,
            "atomic_verifier_instruction",
            REASON_QUARANTINE_PATTERN,
            {
                "dominant_reason": "schema_mismatch",
                "count": facts.quarantine_reason_schema_mismatch,
            },
        )
    if L2_PREDICATES["quarantine_evidence_missing_dominant"](facts):
        return _attr(
            LAYER_L1,
            "evidence_obligation",
            REASON_QUARANTINE_PATTERN,
            {
                "dominant_reason": "evidence_missing",
                "count": facts.quarantine_reason_evidence_missing,
            },
        )

    # Rule 5 — fall-through.
    if facts.terminal_state == "unresolved":
        return _attr(LAYER_NONE, "", REASON_NO_FAILURE_SHAPE, {
            "terminal_state": facts.terminal_state,
        })
    # Terminal verdict present but internally inconsistent: the court
    # verdict contradicts its own certificate bookkeeping. Both checks
    # reuse registered predicates.
    if facts.terminal_state in ("verified_clean", "verified_error"):
        if L2_PREDICATES["surviving_errors_nonempty_and_verdict_clean"](facts):
            return _attr(LAYER_DETECTOR, "", REASON_INTERNAL_INCONSISTENCY, {
                "terminal_state": facts.terminal_state,
                "surviving_error_count": facts.surviving_error_count,
            })
        if (
            L2_PREDICATES["all_candidates_quarantined"](facts)
            and facts.total_certificate_count == 0
        ):
            return _attr(LAYER_DETECTOR, "", REASON_INTERNAL_INCONSISTENCY, {
                "terminal_state": facts.terminal_state,
                "quarantined_error_count": facts.quarantined_error_count,
                "total_certificate_count": facts.total_certificate_count,
            })
    return _attr(LAYER_NONE, "", REASON_NO_FAILURE_SHAPE, {
        "terminal_state": facts.terminal_state,
    })
