"""Terminal Causal Cut: the single owner-defining attribution authority.

Plan §2.4 redefines root-cause attribution. The target is no longer "find the
bad artifact" but "find the minimal repairable cause preventing the correct
terminal state". Malformed/missing artifacts, quarantine, coverage anomalies,
appeal anomalies are evidence used to construct the cut; the cut's owner is
the smallest L1 module or L2 policy surface whose legal operation would lift
the blocking predicate on the terminal edge, not the artifact itself.

C3 (plan §3.2) collapses three re-attribution authorities into one
``build_causal_cut``. The old package had:

- ``step_attributor.attribute_failure`` -- self-supervised + gold-polarity
  trigger-to-module routing that produced an ``AttributionSignal`` (target
  module, secondary module, reflection type, ...).
- ``failure_classifier.classify_failure`` -- a deterministic priority chain
  over AdjudicationFacts + provider artifacts that produced a
  ``FailureAttribution`` (layer, artifact, reason_code, evidence).
- ``failure_episode.build_failure_episode`` -- assembled both into a
  FailureEpisode, doing a third pass of owner refinement via the
  gold-locked route helper.

The new ``build_causal_cut`` performs a single descent over the projected
``DetectionSignal``. It re-uses the closed-vocabulary failure-shape
constants (missing/malformed artifact, consensus blocked, hollow coverage,
boundary conflict, appeal pending/failed, localization fallback / responsible
step missing, quarantine pattern dominant) and the malformed-origin taxonomy
(transport/parse, provider-generation, validator-contract, route/config),
then folds the self-supervised defect, the process diagnostic and (when gold
opt-in is on) the polarity-locked route helper into the same owner. The L1
executable-completeness gate is preserved verbatim: a "nominally L1 but no
execution semantics" intermediate state is demoted to ``LAYER_NONE`` so the
repairability authority sees a real surface.

C4 (plan §2.5): validator/transport/parse/route/config defects route to the
detector inference boundary; they never produce an L1 owner. The boundary
owners are diagnostic labels (``transport_parse_retry`` /
``validator_contract_repair`` / ``route_config_repair``); ``repairability.py``
turns them into ``UNADDRESSABLE_FRAMEWORK_DEFECT``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sehd_rise.causal.signals import (
    ARTIFACT_KIND_CANDIDATE,
    ARTIFACT_KIND_DEFENSE_CERTIFICATE,
    ARTIFACT_KIND_ERROR_CERTIFICATE,
    ARTIFACT_KINDS,
    DetectionDefect,
    DetectionSignal,
    ProcessDiagnostic,
)
from sehd_rise.detection.policy import build_adjudication_facts
from sehd_rise.skills.vocabulary.charters import (
    MODULE_ACTION_OPCODES,
    MODULE_OPERATION_TARGETS,
)
from sehd_rise.skills.vocabulary.contracts import (
    L2_ARTIFACT_APPEAL_MECHANISM,
    L2_ARTIFACT_BOUNDARY_CONSUMPTION,
    L2_ARTIFACT_CONSENSUS_GATE,
    L2_ARTIFACT_COVERAGE_VALIDITY,
    L2_PREDICATES,
    LAYER_DETECTOR,
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
    MODULE_IDS,
)


# ============================================================
# Failure-shape vocabulary (closed; the single source for cut reasons)
# ============================================================

REASON_PROVIDER_MISSING = "provider_artifact_missing"
REASON_PROVIDER_MALFORMED = "provider_artifact_malformed"
REASON_CONSENSUS_BLOCKED = "consensus_gate_blocked"
REASON_HOLLOW_DEFENSE = "hollow_complete_defense"
REASON_HOLLOW_PROSECUTION = "hollow_complete_prosecution"
REASON_BOUNDARY_CONFLICT = "boundary_terminal_conflict"
REASON_INTERNAL_INCONSISTENCY = "internal_inconsistency"
REASON_NO_FAILURE_SHAPE = "no_failure_shape"
REASON_APPEAL_PENDING = "appeal_pending_blocked"
REASON_APPEAL_FAILED = "appeal_failed_with_errors"
REASON_LOCALIZATION_FALLBACK = "localization_temporal_fallback"
REASON_RESPONSIBLE_STEP_MISSING = "responsible_step_missing"
REASON_QUARANTINE_PATTERN = "quarantine_pattern_dominant"
# Gold-locked polarity mismatches: the supervised label names the true pole
# when no artifact evidence caught the failure. FP = error verdict on a clean
# case; FN-clean = clean verdict on an error case.
REASON_GOLD_FALSE_POSITIVE = "gold_false_positive"
REASON_GOLD_FALSE_NEGATIVE_CLEAN = "gold_false_negative_clean"
# Demotion when the raw cut routes to L1 but the L1 executable-completeness
# gate fails: no "nominally L1 but no execution semantics" intermediate may
# reach the evolution side (plan §2.4 -- the cut must be repairable).
REASON_DEMOTED_L1_NO_EXECUTABLE = "demoted_l1_no_executable_obligation"

# Self-supervised defect reasons carrying their executable field canonically
# (rather than on the artifact frontier). The quarantine reason's owner is
# overridden to ``evidence_obligation`` when evidence_missing is the dominant
# quarantine sub-reason (see ``_compile_executable_field``).
_REASON_SYNTHESIZED_FIELD: dict[str, tuple[str, str]] = {
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
    REASON_GOLD_FALSE_NEGATIVE_CLEAN: (
        "evidence_obligation", "certificate_id",
    ),
}

# Reasons that carry executable fields directly on the artifact frontier
# (missing_fields / failed_invariants / failed_fields) versus synthesized.
_REASON_CARRIES_ARTIFACT_FIELDS = frozenset({
    REASON_PROVIDER_MISSING,
    REASON_PROVIDER_MALFORMED,
})


# ============================================================
# Malformed-origin taxonomy (plan §2.5 -- detector/evolution boundary)
# ============================================================

ORIGIN_PROVIDER_GENERATION = "provider_generation_nonconformance"
ORIGIN_VALIDATOR_CONTRACT = "validator_contract_fault"
ORIGIN_TRANSPORT_PARSE = "transport_or_parse_fault"
ORIGIN_ROUTE_CONFIG = "route_or_config_fault"

_FAILURE_ORIGINS = (
    ORIGIN_PROVIDER_GENERATION,
    ORIGIN_VALIDATOR_CONTRACT,
    ORIGIN_TRANSPORT_PARSE,
    ORIGIN_ROUTE_CONFIG,
)

# Only provider generation nonconformance (a registered relational invariant
# failed on populated provider-emitted fields) is L1-addressable. The other
# three origins are detector in-call repair surfaces (plan §2.5).
_ORIGINS_ROUTED_TO_L1 = frozenset({ORIGIN_PROVIDER_GENERATION})


# Boundary owners are diagnostic labels naming which non-evolvable repair
# surface owns the fault. The repairability authority turns these into
# ``UNADDRESSABLE_FRAMEWORK_DEFECT`` (C2/C4): they never feed Skill formation.
_BOUNDARY_REPAIR_OWNER = {
    ORIGIN_TRANSPORT_PARSE: "transport_parse_retry",
    ORIGIN_VALIDATOR_CONTRACT: "validator_contract_repair",
    ORIGIN_ROUTE_CONFIG: "route_config_repair",
}


def classify_malformed_origin(artifact: Mapping[str, Any]) -> str:
    """Return the typed failure origin for one malformed provider artifact.

    Decision order (first match wins; every branch is evidence-backed):

    1. ``transport_or_parse_fault`` -- the artifact was flagged malformed by a
       provider error / appeal crash signal and carries no field-level audit.
       A provider that errored or could not be parsed did not produce content
       to evolve.
    2. ``provider_generation_nonconformance`` -- the artifact carries a
       registered relational ``failed_invariants`` entry. The provider emitted
       populated fields that violate a known relation; the verifier instruction
       can be evolved to not produce that relation.
    3. ``validator_contract_fault`` -- the artifact carries only
       ``failed_fields`` with no invariant and no missing fields. The fields
       exist but the validator's own mapping/schema rejected them; the fault
       is in the validator contract, not provider instruction.
    4. ``route_or_config_fault`` -- fail-closed default. The malformed signal
       has no typed audit evidence so its owner cannot be resolved; route to
       the inference boundary rather than guessing L1.
    """
    def values(key: str) -> list[str]:
        raw = artifact.get(key)
        return list(raw) if isinstance(raw, (list, tuple, set, frozenset)) else []

    failed_invariants = values("failed_invariants")
    failed_fields = values("failed_fields")
    missing_fields = values("missing_fields")
    source = str(artifact.get("source") or "")

    is_provider_error_signal = (
        source in ("appeal_status", "provider_error")
        or "provider_error" in source
    )
    if is_provider_error_signal and not failed_invariants and not failed_fields:
        return ORIGIN_TRANSPORT_PARSE

    if failed_invariants:
        return ORIGIN_PROVIDER_GENERATION

    if failed_fields and not missing_fields:
        return ORIGIN_VALIDATOR_CONTRACT

    return ORIGIN_ROUTE_CONFIG


def origin_layer(origin: str) -> str:
    """Layer for a malformed failure origin (plan §2.5 ownership boundary)."""
    if origin == ORIGIN_PROVIDER_GENERATION:
        return LAYER_L1
    return LAYER_DETECTOR


def _boundary_repair_owner(origin: str) -> str:
    """Name the bounded in-call repair owner for a detector-boundary origin."""
    return _BOUNDARY_REPAIR_OWNER.get(origin, "route_config_repair")


# ============================================================
# L1 owner resolution (artifact kind -> registered module id)
# ============================================================

# Default owner per artifact kind: kept here because the cut's owner logic
# depends on it; the table is closed by what ``skills.vocabulary.contracts``
# exposes as ``MODULE_IDS`` (the eight L1 modules).
_ARTIFACT_OWNER: dict[str, str] = {
    ARTIFACT_KIND_CANDIDATE: "candidate_trigger",
    ARTIFACT_KIND_ERROR_CERTIFICATE: "evidence_obligation",
    ARTIFACT_KIND_DEFENSE_CERTIFICATE: "clean_defense_boundary",
}


def _resolve_l1_module(artifact: Mapping[str, Any], reason: str) -> str:
    """Deterministic artifact-kind -> module mapping; a registered
    ``module_hint`` takes precedence over the default owner.

    A certificate that was emitted but failed field validation is a
    certificate-quality gap owned by the verifier instruction; a certificate
    that should exist but was never emitted is an evidence-obligation gap.
    """
    hint = str(artifact.get("module_hint") or "")
    if hint in MODULE_IDS:
        return hint
    kind = str(artifact.get("kind") or "")
    if kind == ARTIFACT_KIND_ERROR_CERTIFICATE and reason == REASON_PROVIDER_MALFORMED:
        return "atomic_verifier_instruction"
    if kind in _ARTIFACT_OWNER:
        return _ARTIFACT_OWNER[kind]
    return "evidence_obligation"


def _module_has_executable_field(module: str, field: str) -> bool:
    """Direction-agnostic check: does the field belong to any registered
    operation target of the module?"""
    targets_by_op = MODULE_OPERATION_TARGETS.get(module, {})
    return any(field in targets for targets in targets_by_op.values())


def can_compile_repair_obligation(
    *, owner: str, reason_code: str, evidence: Mapping[str, Any]
) -> bool:
    """Return True iff this L1 cut can compile at least one executable repair
    edge with owner / route / object / field / operation / postcondition.

    Source-side enforcement of the strong invariant ``LAYER_L1 => executable``
    (plan §2.4 -- a cut not repairable from the L1 surface is not a cut). It
    does not compile the episode; it checks that the owner module has
    registered opcodes and that the evidence carries at least one field
    belonging to an opcode target. When this returns False the cut is
    demoted to ``LAYER_NONE`` so no "nominally L1 but no execution semantics"
    intermediate reaches the evolution side.
    """
    if owner not in MODULE_ACTION_OPCODES:
        return False
    opcodes = MODULE_ACTION_OPCODES.get(owner, ())
    if not opcodes:
        return False
    if reason_code in _REASON_CARRIES_ARTIFACT_FIELDS:
        for key in ("failed_invariants", "failed_fields", "missing_fields"):
            raw = evidence.get(key)
            if isinstance(raw, (list, tuple, set, frozenset)):
                for value in raw:
                    field = "certificate_id" if str(value) == "certificate" else str(value)
                    if _module_has_executable_field(owner, field):
                        return True
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

    if reason_code in _REASON_SYNTHESIZED_FIELD:
        synth_owner, field = _REASON_SYNTHESIZED_FIELD[reason_code]
        if reason_code == REASON_QUARANTINE_PATTERN:
            dominant = str(evidence.get("dominant_reason") or "")
            if dominant == "evidence_missing":
                synth_owner = "evidence_obligation"
        if owner != synth_owner:
            return _module_has_executable_field(owner, field)
        return _module_has_executable_field(synth_owner, field)

    return False


# ============================================================
# CausalCut record (the single attribution output)
# ============================================================

@dataclass(frozen=True)
class CausalCut:
    """Minimal repairable cause preventing a correct terminal state.

    Carries:

    - ``skill_layer`` -- one of ``LAYER_L1`` / ``LAYER_L2`` / ``LAYER_DETECTOR``
      / ``LAYER_NONE`` (closed by skills.vocabulary.contracts).
    - ``owner`` -- an L1 module id when ``skill_layer == LAYER_L1``; an L2
      artifact name when ``skill_layer == LAYER_L2``; a boundary repair owner
      label when ``skill_layer == LAYER_DETECTOR`` and the failure origin is
      a detector-boundary origin; empty otherwise.
    - ``secondary_owner`` -- partner module used by Phase 6 retrieval / prompt
      hints. May be empty. It never competes with ``owner`` for causality.
    - ``reason_code`` -- closed vocabulary (``REASON_*``).
    - ``failure_origin`` -- one of ``_FAILURE_ORIGINS`` for malformed
      artifacts; empty otherwise.
    - ``blocking_predicate`` -- the terminal predicate this cut would lift.
      For L2 cuts this is the reason code itself (the policy surface that
      would resolve the block). For L1 cuts it is the per-artifact blocking
      predicate synthesized from the court's terminal_basis.
    - ``evidence`` -- the artifact_frontier plus supporting facts; consumed
      by ``episode.py`` to compile the causal failure edge.
    - ``defect`` -- the self-supervised defect reading carried from the
      signal. Used by retrieval / prompt scaffolding in Phase 6.
    - ``process_diagnostic`` -- the self-supervised process diagnostic
      reading; same downstream consumer.
    - ``diagnostic_only`` -- True when the underlying signal was
      diagnostic-only (runtime invalid). The cut is then NOT actionable.
    """

    skill_layer: str
    owner: str
    secondary_owner: str
    reason_code: str
    failure_origin: str
    blocking_predicate: str
    evidence: Mapping[str, Any]
    defect: DetectionDefect
    process_diagnostic: ProcessDiagnostic
    diagnostic_only: bool

    @property
    def actionable(self) -> bool:
        """True iff the cut lands on an evolvable surface (L1 or L2)."""
        return self.skill_layer in (LAYER_L1, LAYER_L2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_layer": self.skill_layer,
            "owner": self.owner,
            "secondary_owner": self.secondary_owner,
            "reason_code": self.reason_code,
            "failure_origin": self.failure_origin,
            "blocking_predicate": self.blocking_predicate,
            "evidence": dict(self.evidence),
            "defect": self.defect.value,
            "process_diagnostic": self.process_diagnostic.value,
            "diagnostic_only": self.diagnostic_only,
        }


def _cut(
    *,
    signal: DetectionSignal,
    skill_layer: str,
    owner: str,
    reason_code: str,
    evidence: Mapping[str, Any],
    failure_origin: str = "",
    blocking_predicate: str = "",
    secondary_owner: str = "",
) -> CausalCut:
    return CausalCut(
        skill_layer=skill_layer,
        owner=owner,
        secondary_owner=secondary_owner,
        reason_code=reason_code,
        failure_origin=failure_origin,
        blocking_predicate=blocking_predicate or reason_code,
        evidence=evidence,
        defect=signal.defect,
        process_diagnostic=signal.process_diagnostic,
        diagnostic_only=signal.diagnostic_only,
    )


# ============================================================
# Helpers (pure, defensive)
# ============================================================

def _find_bad_artifacts(
    artifacts: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], str, str] | None:
    """Return the complete upstream artifact frontier for one failure kind.

    Precedence: L1-addressable origins first, then boundary origins in
    fail-closed order (transport > validator > route). This keeps an
    evolvable provider gap from being masked by a co-occurring transport
    fault. Missing artifacts follow malformed (a broken emitted object is
    upstream of a missing one). Returns ``(frontier, reason_code,
    failure_origin)`` where ``failure_origin`` is one of ``_FAILURE_ORIGINS``
    for malformed and empty for missing.
    """
    if not isinstance(artifacts, (list, tuple)):
        return None
    malformed_by_origin: dict[str, list[dict[str, Any]]] = {
        origin: [] for origin in _FAILURE_ORIGINS
    }
    missing: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        if kind not in ARTIFACT_KINDS:
            continue
        if bool(item.get("malformed")):
            origin = classify_malformed_origin(item)
            malformed_by_origin[origin].append(dict(item))
            continue
        missing_fields = item.get("missing_fields")
        if missing_fields not in (None, "") and not isinstance(
            missing_fields, (list, tuple, set)
        ):
            # Non-sequence missing_fields is itself a malformed signal with no
            # typed audit; route to the fail-closed boundary origin.
            malformed_by_origin[ORIGIN_ROUTE_CONFIG].append(dict(item))
            continue
        if list(missing_fields or []):
            missing.append(dict(item))
    for origin in _FAILURE_ORIGINS:
        bucket = malformed_by_origin[origin]
        if bucket:
            return tuple(bucket), REASON_PROVIDER_MALFORMED, origin
    if missing:
        return tuple(missing), REASON_PROVIDER_MISSING, ""
    return None


def _artifact_evidence(
    artifact: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    """Canonical per-artifact evidence used by the cut and the episode."""
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


def _terminal_blocking_predicates(
    signal: DetectionSignal,
) -> tuple[str, ...]:
    """Read the blocking predicate names of the proof court's terminal cut."""
    proof = signal.proof_adjudication or {}
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
    """Pick the terminal predicate the cut would lift, when one is named."""
    preferred = {
        ARTIFACT_KIND_CANDIDATE: "required_candidates_terminal",
        ARTIFACT_KIND_ERROR_CERTIFICATE: "prosecution_coverage_complete",
        ARTIFACT_KIND_DEFENSE_CERTIFICATE: "defense_coverage_complete",
    }.get(kind, "")
    if preferred in blocking_predicates:
        return preferred
    return "provider_artifact_integrity"


# ============================================================
# Gold-polarity route helper (preserved from step_attributor)
# ============================================================

def _route_gold_locked_owner(
    *,
    state: str,
    prosecution_state: str | None,
    defense_state: str | None,
    default_module: str,
    default_secondary: str,
    surviving_error_count: int = 0,
) -> tuple[str, str]:
    """Route a gold-locked FP/FN to the stage where the court actually broke.

    Gold polarity selects the wrong outcome side; coverage states identify the
    first unsatisfied process contract on that side.

    - FN (clean verdict, gold=error): if prosecution is already complete, the
      court wrongly accepted clean despite a complete error proof; that is an
      abstention/adjudication failure. If prosecution is incomplete, evidence
      construction is the defect. If defense is the only complete side, the
      court over-deferred to it.
    - FP (error verdict, gold=clean): the only path that can relax a
      surviving error certificate is the prosecution-side owner
      (``evidence_obligation`` -- keep_coverage_incomplete to demote the
      verdict -- or ``atomic_verifier_instruction`` -- withhold_certificate
      on the carrier). Adding more defense certificates does NOT demote a
      verdict carried by a still-surviving error certificate. Therefore: for
      FP + verified_error, when ``surviving_error_count > 0`` the owner is
      always the prosecution-side evidence/verifier pair, REGARDLESS of the
      defense coverage state.
    """
    if state == "verified_clean":
        if prosecution_state == "complete":
            return "abstention_rule", "causal_link_test"
        if prosecution_state in {"incomplete", "unresolved"}:
            return default_module or "evidence_obligation", "atomic_verifier_instruction"
        return "evidence_obligation", "atomic_verifier_instruction"
    if state == "verified_error":
        if surviving_error_count > 0:
            return "evidence_obligation", "atomic_verifier_instruction"
        if defense_state == "complete":
            return "evidence_obligation", "atomic_verifier_instruction"
        if defense_state in {"incomplete", "unresolved"}:
            return default_module or "clean_defense_boundary", "abstention_rule"
        return "clean_defense_boundary", "abstention_rule"
    return default_module, default_secondary


# ============================================================
# Self-supervised route helpers (preserved from step_attributor)
# ============================================================
# Plan §2.4: owner = minimal repairable surface that lifts the blocking
# predicate. When no anomalous artifact surfaces (Rule 5 fall-through), the
# cut stays ``LAYER_NONE`` with ``no_failure_shape`` rather than inferring a
# Skill surface from a default module table -- the inferred routing the old
# package used (``_route_process_failure`` / ``_route_unresolved_weak_defense``
# / ``_DEFECT_DEFAULT_OWNER``) is what §2.4 forbids. Those helpers are
# intentionally NOT carried forward: their function is replaced by artifact evidence.

# Maps process diagnostic -> secondary module. The detector reported the
# exact broken stage, so the partner module follows from pipeline structure.
# Used only when the cut already has an L1 owner from artifact evidence; the
# diagnostic supplies the partner module the reflection prompt should also
# consider, never the polarity-locking primary owner.
_PROCESS_DIAGNOSTIC_SECONDARY: dict[ProcessDiagnostic, str] = {
    ProcessDiagnostic.FOCUSED_QUARANTINE: "evidence_obligation",
    # Appeal breakages refine themselves via ``_route_appeal_diagnostic``;
    # the fallback secondary is the prosecution-side evidence surface.
    ProcessDiagnostic.APPEAL_FAILURE: "evidence_obligation",
    ProcessDiagnostic.LOCALIZATION_AMBIGUOUS: "causal_link_test",
}


def _route_appeal_diagnostic(
    signal: DetectionSignal,
) -> tuple[str, str] | None:
    """Resolve the appeal breakage to the broken appellate party."""
    if signal.process_diagnostic is not ProcessDiagnostic.APPEAL_FAILURE:
        return None
    error_type_token = str(signal.diagnostics.get("appeal_error_type") or "")
    error_text = str(signal.diagnostics.get("appeal_error") or "").lower()
    if "appeal defense candidate coverage mismatch" in error_text:
        return "candidate_to_verifier_route", "atomic_verifier_instruction"
    if "appeal defense is incomplete" in error_text or (
        "appeal defender input firewall violation" in error_text
    ):
        return "clean_defense_boundary", "atomic_verifier_instruction"
    if (
        "appeal prosecution is incomplete" in error_text
        or "appeal prosecution contains another candidate" in error_text
        or "appeal prosecutor input firewall violation" in error_text
    ):
        return "atomic_verifier_instruction", "evidence_obligation"
    if error_type_token:
        # Unrecognized appeal breakage: prosecution-side appellate obligation
        # runs first; default to the verifier instruction.
        return "atomic_verifier_instruction", "evidence_obligation"
    return None


# Error-type tokens (gold-polarity reading) that lock the owner to the
# polarity's side. Listed for documentation; the cut uses them through
# ``_gold_error_type_token`` and ``_gold_step_correct`` below.
_GOLD_POLARITY_ERROR_TYPES = frozenset({
    "false_positive",
    "false_negative",
    "false_negative_unresolved",
    "false_negative_clean",
    "unresolved_should_error",
    "unresolved_should_clean",
})


def _gold_error_type_token(signal: DetectionSignal) -> str:
    """Map gold polarity + state to the polarity error type token.

    Empty when no gold label is set; the cut then routes via self-supervised
    signals alone.
    """
    if not signal.has_gold or signal.gold_label is None:
        return ""
    gold = signal.gold_label
    predicted = None
    if signal.state == "verified_error":
        predicted = True
    elif signal.state == "verified_clean":
        predicted = False
    if predicted is True and gold is False:
        return "false_positive"
    if predicted is False and gold is True:
        return "false_negative_clean"
    if signal.state == "unresolved":
        return "unresolved_should_error" if gold else "unresolved_should_clean"
    return ""


def _gold_step_correct(signal: DetectionSignal) -> bool | None:
    """Whether the predicted responsible step matches a gold step annotation."""
    if not signal.has_gold or not signal.gold_steps:
        return None
    if signal.responsible_step is None:
        return None
    return str(signal.responsible_step) in signal.gold_steps


def _gold_polarity_mismatch_owner(
    signal: DetectionSignal, facts: Any,
) -> tuple[str, str, str] | None:
    """Gold-locked owner for a verdict that flipped the wrong way.

    Only the FP and FN-clean shapes are addressable here; unresolved shapes
    carry no flipped verdict.

    - FP (error verdict, gold=clean): the only L1 op that can relax a
      surviving error certificate is prosecution-side, so the owner is always
      the evidence/verifier pair (``keep_coverage_incomplete`` demotes the
      verdict). Defense is never the owner — adding defense cannot demote a
      verdict carried by a still-surviving error certificate.
    - FN-clean (clean verdict, gold=error): the symmetric fix is to
      strengthen prosecution into a decisive error, so the owner is also the
      evidence/verifier pair — whether the error was never found (incomplete
      prosecution) or found-but-defeated (complete prosecution). The
      ``abstention_rule`` shape of ``_route_gold_locked_owner`` has no
      clean→error opcode, so it is deliberately never emitted here.
    """
    gold_token = _gold_error_type_token(signal)
    if gold_token == "false_positive":
        owner, secondary = _route_gold_locked_owner(
            state=signal.state,
            prosecution_state=signal.prosecution_coverage.state,
            defense_state=signal.defense_coverage.state,
            default_module="evidence_obligation",
            default_secondary="atomic_verifier_instruction",
            surviving_error_count=facts.surviving_error_count,
        )
        return owner, secondary, REASON_GOLD_FALSE_POSITIVE
    if gold_token == "false_negative_clean":
        return (
            "evidence_obligation",
            "atomic_verifier_instruction",
            REASON_GOLD_FALSE_NEGATIVE_CLEAN,
        )
    return None


# ============================================================
# Single cut builder
# ============================================================

def build_causal_cut(
    signal: DetectionSignal,
    *,
    boundary_path_state: str = "",
) -> CausalCut:
    """Build the terminal causal cut for one projected detection signal.

    Single descent over the signal. Returns a ``CausalCut`` carrying
    ``skill_layer / owner / secondary_owner / reason_code / failure_origin /
    blocking_predicate / evidence`` and the carried defect + process
    diagnostic readings. Owner is the minimal repairable surface that
    would lift the blocking predicate (plan §2.4); anomalous artifacts are
    evidence used to construct the cut, not the owner itself.

    Priority order reflects the v3.5 proof court's pipeline structure:

    1. Provider artifact integrity (rule 1). A malformed emitted object is
       classified by typed origin; only provider generation nonconformance
       routes to L1. Validator/transport/route/config faults route to the
       detector inference boundary (C4): the owner is the boundary repair
       label and the cut is non-actionable. Missing artifacts route to the
       registered L1 module for their kind.
    2. Boundary path verified_clean but terminal unresolved -> L2
       boundary_consumption_policy.
    3. Consensus gate blocked nonempty AND terminal unresolved -> L2
       consensus_gate_policy.
    4. Hollow "complete" coverage with zero valid certificates -> L2
       coverage_validity_policy (defense or prosecution).
    4.1. Appeal pending and terminal unresolved -> L2 appeal_mechanism_policy.
    4.2. Appeal failed with surviving errors -> L2 appeal_mechanism_policy.
    4.3. Localization fallback used -> L1 earliest_origin_tiebreak.
    4.4. Responsible step missing AND verdict error -> L1
         earliest_origin_tiebreak.
    4.5. Quarantine reason dominant -> L1 atomic_verifier_instruction or
         evidence_obligation (by dominant reason).
    5. Fall-through: unresolved terminal -> LAYER_NONE with no_failure_shape;
       terminal verdict with internal inconsistency -> LAYER_DETECTOR with
       internal_inconsistency; otherwise LAYER_NONE.

    Gold polarity (when ``signal.has_gold``) refines the L1 owner for the
    gold-locked FP/FN shapes and the gold-confirmed localization mismatch;
    it never overrides a defect-derived L1 owner outside those shapes. A
    self-supervised process diagnostic surfaces the broken stage for the
    unresolved/crs/evidence-incomplete cases when no gold locks the polarity.
    """
    if signal.diagnostic_only:
        # Runtime-invalid rows carry no authoritative verdict: the only
        # honest cut is non-actionable with the runtime error category on
        # the signal. Downstream is forbidden from treating this as fitness
        # or correctness evidence.
        return _cut(
            signal=signal,
            skill_layer=LAYER_NONE,
            owner="",
            reason_code=REASON_NO_FAILURE_SHAPE,
            evidence={"terminal_state": signal.state},
            blocking_predicate="runtime_invalid",
        )

    facts = build_adjudication_facts(
        dict(signal.proof_adjudication) if signal.proof_adjudication else {},
        boundary_path_state=boundary_path_state,
    )

    # --- Rule 1: provider artifact integrity --------------------------
    bad = _find_bad_artifacts(list(signal.provider_artifacts))
    if bad is not None:
        artifacts, reason, failure_origin = bad
        artifact = artifacts[0]
        artifact_frontier = [_artifact_evidence(item, reason) for item in artifacts]
        primary = artifact_frontier[0]
        if reason == REASON_PROVIDER_MALFORMED:
            layer = origin_layer(failure_origin)
            if layer == LAYER_L1:
                owner = str(primary["owner_module"])
                secondary = _gold_or_process_secondary(signal, owner)
                cut = _cut(
                    signal=signal,
                    skill_layer=LAYER_L1,
                    owner=owner,
                    reason_code=reason,
                    evidence={
                        **primary,
                        "failure_origin": failure_origin,
                        "artifact_frontier": artifact_frontier,
                    },
                    failure_origin=failure_origin,
                    blocking_predicate=_artifact_blocking_predicate(
                        str(primary["kind"]),
                        _terminal_blocking_predicates(signal),
                    ),
                    secondary_owner=secondary,
                )
                return _enforce_executable_gate(cut)
            # Detector-boundary origin: route to the inference contract with
            # the typed origin and the intended (non-evolvable) owner so
            # bounded in-call repair owns it instead of genome formation (C4).
            return _cut(
                signal=signal,
                skill_layer=LAYER_DETECTOR,
                owner=_boundary_repair_owner(failure_origin),
                reason_code=reason,
                evidence={
                    "kind": str(artifact.get("kind") or ""),
                    "intended_owner": _resolve_l1_module(artifact, reason),
                    "missing_fields": list(primary["missing_fields"]),
                    "failed_fields": list(primary["failed_fields"]),
                    "failed_invariants": list(primary["failed_invariants"]),
                    "artifact_id": str(artifact.get("artifact_id") or ""),
                    "source": str(artifact.get("source") or ""),
                    "failure_origin": failure_origin,
                    "artifact_frontier": artifact_frontier,
                },
                failure_origin=failure_origin,
                blocking_predicate=_artifact_blocking_predicate(
                    str(primary["kind"]),
                    _terminal_blocking_predicates(signal),
                ),
                secondary_owner="",
            )
        # Missing provider artifact: register to its L1 module and run the
        # L1 executable-completeness gate. Gold polarity may refine the
        # owner for the unresolved-should-clean / unresolved-should-error
        # shapes when the missing artifact is one of the certificate kinds.
        owner = str(primary["owner_module"])
        secondary = _gold_or_process_secondary(signal, owner)
        if signal.has_gold and signal.defect is DetectionDefect.UNRESOLVED:
            owner, secondary = _gold_route_owner(
                signal, default_owner=owner, default_secondary=secondary
            )
        cut = _cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner=owner,
            reason_code=reason,
            evidence={
                **primary,
                "failure_origin": "",
                "artifact_frontier": artifact_frontier,
            },
            failure_origin="",
            blocking_predicate=_artifact_blocking_predicate(
                str(primary["kind"]),
                _terminal_blocking_predicates(signal),
            ),
            secondary_owner=secondary,
        )
        return _enforce_executable_gate(cut)

    # --- Rule 1.5: gold-locked polarity mismatch (FP / FN-clean) ------
    # Supervised polarity names the true pole when no artifact evidence
    # (Rule 1) caught the failure. A false positive must relax prosecution; a
    # false-negative-clean must strengthen it. Placed before the
    # self-supervised rules so an FP with a co-occurring localization
    # fallback still routes to its true owner (over-prosecution, not
    # mislocalization of a real error).
    gold_mismatch = _gold_polarity_mismatch_owner(signal, facts)
    if gold_mismatch is not None:
        owner, secondary, reason = gold_mismatch
        return _enforce_executable_gate(_cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner=owner,
            secondary_owner=secondary,
            reason_code=reason,
            evidence={
                "terminal_state": signal.state,
                "gold_polarity": (
                    "clean" if reason == REASON_GOLD_FALSE_POSITIVE else "error"
                ),
                "surviving_error_count": facts.surviving_error_count,
                "prosecution_coverage": signal.prosecution_coverage.state,
                "defense_coverage": signal.defense_coverage.state,
            },
            blocking_predicate="gold_polarity_mismatch",
        ))

    # --- Rule 2: boundary terminal conflict ---------------------------
    if L2_PREDICATES["boundary_state_conflicts_with_terminal"](facts):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_BOUNDARY_CONSUMPTION,
            reason_code=REASON_BOUNDARY_CONFLICT,
            evidence={
                "boundary_path_state": facts.boundary_path_state,
                "terminal_state": facts.terminal_state,
            },
            blocking_predicate=REASON_BOUNDARY_CONFLICT,
        )

    # --- Rule 3: consensus gate blocked the terminal ------------------
    if (
        L2_PREDICATES["consensus_gate_blocked_nonempty"](facts)
        and facts.terminal_state == "unresolved"
    ):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_CONSENSUS_GATE,
            reason_code=REASON_CONSENSUS_BLOCKED,
            evidence={
                "consensus_gate_blocked_count": facts.consensus_gate_blocked_count,
                "terminal_state": facts.terminal_state,
            },
            blocking_predicate=REASON_CONSENSUS_BLOCKED,
        )

    # --- Rule 4: hollow "complete" coverage ---------------------------
    if L2_PREDICATES["defense_coverage_complete_and_zero_certificates"](facts):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_COVERAGE_VALIDITY,
            reason_code=REASON_HOLLOW_DEFENSE,
            evidence={
                "defense_coverage": facts.defense_coverage,
                "valid_defense_certificate_count": facts.valid_defense_certificate_count,
            },
            blocking_predicate=REASON_HOLLOW_DEFENSE,
        )
    if L2_PREDICATES["prosecution_coverage_complete_and_zero_certificates"](facts):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_COVERAGE_VALIDITY,
            reason_code=REASON_HOLLOW_PROSECUTION,
            evidence={
                "prosecution_coverage": facts.prosecution_coverage,
                "valid_error_certificate_count": facts.valid_error_certificate_count,
            },
            blocking_predicate=REASON_HOLLOW_PROSECUTION,
        )

    # --- Rule 4.1: appeal pending blocked the terminal ----------------
    if L2_PREDICATES["appeal_pending_and_terminal_unresolved"](facts):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_APPEAL_MECHANISM,
            reason_code=REASON_APPEAL_PENDING,
            evidence={
                "appeal_status": facts.appeal_status,
                "terminal_state": facts.terminal_state,
            },
            blocking_predicate=REASON_APPEAL_PENDING,
        )

    # --- Rule 4.2: appeal failed with surviving errors ----------------
    if L2_PREDICATES["appeal_failed_with_surviving_errors"](facts):
        return _cut(
            signal=signal,
            skill_layer=LAYER_L2,
            owner=L2_ARTIFACT_APPEAL_MECHANISM,
            reason_code=REASON_APPEAL_FAILED,
            evidence={
                "appeal_status": facts.appeal_status,
                "surviving_error_count": facts.surviving_error_count,
            },
            blocking_predicate=REASON_APPEAL_FAILED,
        )

    # --- Rule 4.3: localization fallback used -------------------------
    if L2_PREDICATES["localization_failed_with_temporal_fallback"](facts):
        cut = _cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner="earliest_origin_tiebreak",
            reason_code=REASON_LOCALIZATION_FALLBACK,
            evidence={"localization_status": facts.localization_status},
            blocking_predicate="earliest_origin_tiebreak_required",
        )
        return _enforce_executable_gate(cut)

    # --- Rule 4.4: responsible step missing with error verdict --------
    if (
        L2_PREDICATES["responsible_step_missing_and_verdict_error"](facts)
        and facts.localization_status != ""
    ):
        cut = _cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner="earliest_origin_tiebreak",
            reason_code=REASON_RESPONSIBLE_STEP_MISSING,
            evidence={"terminal_state": facts.terminal_state},
            blocking_predicate="responsible_step_required",
        )
        return _enforce_executable_gate(cut)

    # --- Rule 4.5: quarantine reason dominant ------------------------
    if L2_PREDICATES["quarantine_schema_mismatch_dominant"](facts):
        cut = _cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner="atomic_verifier_instruction",
            reason_code=REASON_QUARANTINE_PATTERN,
            evidence={
                "dominant_reason": "schema_mismatch",
                "count": facts.quarantine_reason_schema_mismatch,
            },
            blocking_predicate="quarantine_schema_mismatch_dominant",
        )
        return _enforce_executable_gate(cut)
    if L2_PREDICATES["quarantine_evidence_missing_dominant"](facts):
        cut = _cut(
            signal=signal,
            skill_layer=LAYER_L1,
            owner="evidence_obligation",
            reason_code=REASON_QUARANTINE_PATTERN,
            evidence={
                "dominant_reason": "evidence_missing",
                "count": facts.quarantine_reason_evidence_missing,
            },
            blocking_predicate="quarantine_evidence_missing_dominant",
        )
        return _enforce_executable_gate(cut)

    # --- Rule 5: fall-through ----------------------------------------
    if facts.terminal_state == "unresolved":
        return _cut(
            signal=signal,
            skill_layer=LAYER_NONE,
            owner="",
            reason_code=REASON_NO_FAILURE_SHAPE,
            evidence={"terminal_state": facts.terminal_state},
            blocking_predicate="unresolved_no_failure_shape",
        )

    if facts.terminal_state in ("verified_clean", "verified_error"):
        if L2_PREDICATES["surviving_errors_nonempty_and_verdict_clean"](facts):
            return _cut(
                signal=signal,
                skill_layer=LAYER_DETECTOR,
                owner="",
                reason_code=REASON_INTERNAL_INCONSISTENCY,
                evidence={
                    "terminal_state": facts.terminal_state,
                    "surviving_error_count": facts.surviving_error_count,
                },
                blocking_predicate=REASON_INTERNAL_INCONSISTENCY,
            )
        if (
            L2_PREDICATES["all_candidates_quarantined"](facts)
            and facts.total_certificate_count == 0
        ):
            return _cut(
                signal=signal,
                skill_layer=LAYER_DETECTOR,
                owner="",
                reason_code=REASON_INTERNAL_INCONSISTENCY,
                evidence={
                    "terminal_state": facts.terminal_state,
                    "quarantined_error_count": facts.quarantined_error_count,
                    "total_certificate_count": facts.total_certificate_count,
                },
                blocking_predicate=REASON_INTERNAL_INCONSISTENCY,
            )
    return _cut(
        signal=signal,
        skill_layer=LAYER_NONE,
        owner="",
        reason_code=REASON_NO_FAILURE_SHAPE,
        evidence={"terminal_state": facts.terminal_state},
        blocking_predicate="no_failure_shape",
    )


# ============================================================
# Owner refinement helpers (gold polarity + process diagnostic)
# ============================================================

def _gold_route_owner(
    signal: DetectionSignal, *, default_owner: str, default_secondary: str
) -> tuple[str, str]:
    """Apply gold-polarity routing to refine an L1 owner.

    Preserved from ``step_attributor._route_gold_locked_owner``: gold polarity
    selects the failure side; coverage states identify the first unsatisfied
    process contract on that side. Returns ``(owner, secondary_owner)``.
    """
    gold_token = _gold_error_type_token(signal)
    if not gold_token:
        return default_owner, default_secondary

    facts = build_adjudication_facts(
        dict(signal.proof_adjudication) if signal.proof_adjudication else {},
    )
    surviving_error_count = facts.surviving_error_count
    routed = _route_gold_locked_owner(
        state=signal.state,
        prosecution_state=signal.prosecution_coverage.state,
        defense_state=signal.defense_coverage.state,
        default_module=default_owner,
        default_secondary=default_secondary,
        surviving_error_count=surviving_error_count,
    )
    return routed


def _gold_or_process_secondary(
    signal: DetectionSignal, owner: str
) -> str:
    """Refine the secondary owner via gold diagnostic or process diagnostic.

    Process diagnostics supply the partner module for a defect when the gold
    polarity does not lock the case. A gold-locked diagnostic partner must
    survive the self-supervised route: the lower-priority route helper
    overwrites the secondary only when no diagnostic has claimed it.
    """
    default_secondary = ""
    diagnostic_finding = signal.process_diagnostic
    if diagnostic_finding is ProcessDiagnostic.LOCALIZATION_AMBIGUOUS:
        if signal.has_gold and _gold_step_correct(signal) is True:
            # Gold confirms the responsible step is right; an ambiguous tie-break
            # did not actually mislocalize, so earliest_origin_tiebreak is not
            # the partner to blame.
            return default_secondary
        return _PROCESS_DIAGNOSTIC_SECONDARY[ProcessDiagnostic.LOCALIZATION_AMBIGUOUS]
    if diagnostic_finding is not ProcessDiagnostic.NONE:
        appeal_route = _route_appeal_diagnostic(signal)
        if appeal_route is not None:
            return appeal_route[1]
        return _PROCESS_DIAGNOSTIC_SECONDARY[diagnostic_finding]
    return default_secondary


def _enforce_executable_gate(cut: CausalCut) -> CausalCut:
    """Apply the L1 executable-completeness gate (plan §2.4 invariant).

    A "nominally L1 but no execution semantics" intermediate state is
    forbidden: when the raw L1 cut cannot compile at least one executable
    repair edge (owner/route/object/field/operation/postcondition), it is
    demoted to ``LAYER_NONE`` so the evolution runner never observes what
    would otherwise become an ``UnaddressableFailure`` (now subsumed by the
    repairability authority emitting ``UNADDRESSABLE_FRAMEWORK_DEFECT``).

    The demoted cut preserves the carried defect / process_diagnostic /
    diagnostic_only reading so the repairability authority can dispatch
    ``UNADDRESSABLE_FRAMEWORK_DEFECT`` with the same provenance as a
    non-demoted cut.
    """
    if cut.skill_layer != LAYER_L1:
        return cut
    if can_compile_repair_obligation(
        owner=cut.owner, reason_code=cut.reason_code, evidence=cut.evidence
    ):
        return cut
    return CausalCut(
        skill_layer=LAYER_NONE,
        owner="",
        secondary_owner="",
        reason_code=REASON_DEMOTED_L1_NO_EXECUTABLE,
        failure_origin="",
        blocking_predicate="demoted_l1_no_executable_obligation",
        evidence={
            "classified_layer": cut.skill_layer,
            "classified_owner": cut.owner,
            "classified_reason": cut.reason_code,
            "classified_evidence": dict(cut.evidence),
        },
        defect=cut.defect,
        process_diagnostic=cut.process_diagnostic,
        diagnostic_only=cut.diagnostic_only,
    )


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "CausalCut",
    "ORIGIN_PROVIDER_GENERATION",
    "ORIGIN_ROUTE_CONFIG",
    "ORIGIN_TRANSPORT_PARSE",
    "ORIGIN_VALIDATOR_CONTRACT",
    "REASON_APPEAL_FAILED",
    "REASON_APPEAL_PENDING",
    "REASON_BOUNDARY_CONFLICT",
    "REASON_CONSENSUS_BLOCKED",
    "REASON_DEMOTED_L1_NO_EXECUTABLE",
    "REASON_HOLLOW_DEFENSE",
    "REASON_HOLLOW_PROSECUTION",
    "REASON_INTERNAL_INCONSISTENCY",
    "REASON_LOCALIZATION_FALLBACK",
    "REASON_NO_FAILURE_SHAPE",
    "REASON_PROVIDER_MALFORMED",
    "REASON_PROVIDER_MISSING",
    "REASON_QUARANTINE_PATTERN",
    "REASON_RESPONSIBLE_STEP_MISSING",
    "build_causal_cut",
    "can_compile_repair_obligation",
    "classify_malformed_origin",
    "origin_layer",
]