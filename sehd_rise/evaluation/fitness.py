"""Module-local fitness: did an edit move the part of the pipeline it targets?

The structural gate answers whether an edit is legal. This module answers a
different question: whether the edit produced a measurable improvement in the
part of the pipeline its target module can influence. The evaluator does not
require every local edit to turn an unresolved source into a definite state;
such edits enter a reversible shadow genome and commit only after a small
batch remains beneficial.

C10 reconnection (plan §2.12 causal credit chain): the old ``AttributionSignal``
carried a ``should_evolve`` trigger boolean. Phase 4 absorbed that trigger into
the single Repairability Authority (``repairability.analyze``), so
``DetectionSignal`` has no equivalent field. The trigger is therefore threaded
in explicitly as ``before_trigger`` / ``after_trigger`` (the baseline and replay
repairability verdicts), supplied by the loop that already computed them. The
signal itself is the single projection of its result, so the old
``replace(signal, state=resolve_evolution_state(result))`` re-resolution is
gone: a caller passes ``project_detection(result)`` and the state already
matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sehd_rise.causal.signals import (
    COVERAGE_STATE_SCORE,
    CoverageReading,
    DetectionSignal,
)
from sehd_rise.execution.action import (
    certificate_carrier_movement,
    certificate_claim_fingerprint,
    surviving_carrier_fingerprints,
)


VALID_STATES = {"verified_error", "verified_clean", "unresolved"}
CAUSAL_FIELDS = (
    "norm_evidence_refs",
    "violation_evidence_refs",
    "agent_control_evidence_refs",
    "impact_evidence_refs",
)

# Gold direction for module local scores (v5.3):
# True = score up is good for error (prosecution stronger),
# False = score up is good for clean (defense stronger),
# None = score direction is neutral.
_MODULE_SCORE_GOLD_DIRECTION: dict[str, bool | None] = {
    "candidate_trigger": None,
    # Both modules score ``valid_error_certificate_count + prosecution``:
    # a higher score means a stronger error prosecution. On a gold-clean case,
    # quarantining suspect certificates lowers that score — that is the
    # false-positive fix, not a regression — so the direction must be True
    # (score up is only good for gold-error).
    "candidate_to_verifier_route": True,
    "atomic_verifier_instruction": True,
    # ``min(prosecution, defense)`` rises only when the WEAKER side improves,
    # which is polarity-neutral by construction. A stale ``True`` annotation
    # meant every genuine evidence_obligation gain on a gold-CLEAN case failed
    # the direction check and was demoted to ``no_effect``.
    "evidence_obligation": None,
    "causal_link_test": True,      # causal chain up = more error-like
    "earliest_origin_tiebreak": None,
}


@dataclass(frozen=True)
class FitnessAssessment:
    """Auditable classification of one source replay."""

    decision: str  # strong/local | directional_evidence | no_effect | worse
    reasons: tuple[str, ...]
    task_score_before: int | None = None
    task_score_after: int | None = None
    local_score_before: float | None = None
    local_score_after: float | None = None

    @property
    def passed(self) -> bool:
        return self.decision in {"strong_improvement", "local_improvement"}

    @property
    def worsened(self) -> bool:
        return self.decision == "worse"

    @property
    def evidence_only(self) -> bool:
        """Whether replay showed process direction without correctness proof."""
        return self.decision == "directional_evidence"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "passed": self.passed,
            "worsened": self.worsened,
            "evidence_only": self.evidence_only,
            "task_score_before": self.task_score_before,
            "task_score_after": self.task_score_after,
            "local_score_before": self.local_score_before,
            "local_score_after": self.local_score_after,
        }


def task_state_score(state: str, gold_label: bool) -> int:
    """Rank task outcomes without exposing the label to mutation prompts.

    2 = correct definite decision, 1 = unresolved, 0 = wrong definite decision.

    The authoritative copy lives here; ``planning.search._task_state_score`` is
    a layer-rule-forced inline twin (planning may not import evaluation) kept
    byte-identical and covered by the planning parity harness against drift.
    """
    if state == "unresolved":
        return 1
    if state == "verified_error":
        return 2 if gold_label else 0
    if state == "verified_clean":
        return 0 if gold_label else 2
    raise ValueError(f"invalid detector state: {state!r}")


def _coverage_rank(reading: CoverageReading) -> float:
    """Numeric rank of one proof side, categorical or quantitative.

    ``CoverageReading`` already collapses the categorical->numeric mapping
    (owned by ``causal.signals``) into ``score``; the state lookup is the
    fail-closed fallback for an unavailable reading.
    """
    if reading.score is not None:
        return reading.score
    return COVERAGE_STATE_SCORE.get(str(reading.state or ""), 0.0)


def _certificate_rows(row: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    keys = (
        ("combined_prosecution", "prosecution")
        if kind == "prosecution"
        else ("combined_defense", "defense")
    )
    for key in keys:
        payload = row.get(key) or {}
        if not isinstance(payload, Mapping):
            continue
        rows = payload.get("candidates") or payload.get("certificates") or []
        if isinstance(rows, list):
            return [value for value in rows if isinstance(value, Mapping)]
    return []


def _quarantine_count(row: Mapping[str, Any]) -> int:
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping):
        return 0
    values = proof.get("quarantined_error_certificate_ids") or []
    return len(values) if isinstance(values, list) else 0


def _proof_count(row: Mapping[str, Any], key: str, fallback: int) -> int:
    proof = row.get("proof_adjudication") or {}
    if not isinstance(proof, Mapping) or key not in proof:
        return fallback
    try:
        return max(0, int(proof.get(key) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _valid_certificate_count(row: Mapping[str, Any], kind: str) -> int:
    rows = _certificate_rows(row, kind)
    if kind == "prosecution":
        return _proof_count(
            row,
            "valid_error_certificate_count",
            max(0, len(rows) - _quarantine_count(row)),
        )
    proof = row.get("proof_adjudication") or {}
    quarantined = (
        len(proof.get("quarantined_defense_certificate_ids") or [])
        if isinstance(proof, Mapping) else 0
    )
    return _proof_count(
        row,
        "valid_defense_certificate_count",
        max(0, len(rows) - quarantined),
    )


def _valid_claim_fingerprints(
    row: Mapping[str, Any], kind: str = "prosecution",
) -> frozenset[str] | None:
    """Claim fingerprints of the court-valid certificates of one side.

    Renames are identity-preserving across re-samplings, so this set — not the
    row count — is what actually moved when a replay ran. Returns ``None`` when
    the result carries no adjudication payload at all, so the caller keeps its
    count fallback.
    """
    proof = row.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        return None
    quarantine_key = (
        "quarantined_error_certificate_ids"
        if kind == "prosecution"
        else "quarantined_defense_certificate_ids"
    )
    quarantined = {
        str(value)
        for value in (proof.get(quarantine_key) or [])
    }
    rows = _certificate_rows(row, kind)
    if not rows:
        return frozenset()
    tokens: set[str] = set()
    for certificate in rows:
        cid = str(certificate.get("certificate_id") or "")
        if cid and cid in quarantined:
            continue
        fingerprint = certificate_claim_fingerprint(certificate)
        # Unidentifiable rows still count as material, under their own id.
        tokens.add(fingerprint if fingerprint else f"id:{cid or len(tokens)}")
    return frozenset(tokens)


def _prosecution_strength_score(
    signal: DetectionSignal, row: Mapping[str, Any],
) -> float:
    """Prosecution-side local score, measured at claim level.

    Two verdict-relevant axes, both rename-invariant:

    - emitted valid claims (rows minus quarantined): the material the
      prosecution laid before the court. This is the axis an FN-direction
      repair moves (verify_violation / emit_certificate create claims while the
      verdict may still be unresolved — a surviving-only count is blind to
      exactly these repairs).
    - surviving carrier claims: the certificates the court upholds. This is the
      axis an FP-direction withhold moves.

    Positional certificate ids are re-minted on every provider re-sampling, and
    the court's whole-surface ``valid_error_certificate_count`` moves whenever
    the provider reorganizes its census, so neither axis uses positional
    identity. Falls back to the court count only when the result carries no
    adjudication payload.
    """
    valid_claims = _valid_claim_fingerprints(row, "prosecution")
    if valid_claims is None:
        return float(_valid_certificate_count(row, "prosecution")) + _coverage_rank(
            signal.prosecution_coverage
        )
    surviving_claims = surviving_carrier_fingerprints(row)
    surviving_count = (
        len(surviving_claims) if surviving_claims is not None else 0
    )
    return (
        float(len(valid_claims) + surviving_count)
        + _coverage_rank(signal.prosecution_coverage)
    )


def _causal_field_score(row: Mapping[str, Any]) -> float:
    certificates = _certificate_rows(row, "prosecution")
    proof = row.get("proof_adjudication") or {}
    quarantined = {
        str(value)
        for value in (
            proof.get("quarantined_error_certificate_ids") or []
        )
    } if isinstance(proof, Mapping) else set()
    if quarantined:
        certificates = [
            certificate for certificate in certificates
            if str(certificate.get("certificate_id") or "") not in quarantined
        ]
    if not certificates:
        return 0.0
    populated = 0
    total = len(certificates) * len(CAUSAL_FIELDS)
    for certificate in certificates:
        for field in CAUSAL_FIELDS:
            if certificate.get(field):
                populated += 1
    return populated / max(1, total)


def _responsible_step_present(row: Mapping[str, Any]) -> float:
    value = row.get("responsible_step")
    return 1.0 if value is not None and str(value) != "" else 0.0


def _gold_step_correct(signal: DetectionSignal) -> bool | None:
    """Whether the predicted responsible step matches a gold step annotation.

    ``None`` when no gold step annotation exists: an absent gold step is not
    evidence of mislocalization. The signal carries ``gold_steps`` and
    ``responsible_step`` directly, so this reads the projection rather than the
    raw row.
    """
    if not signal.gold_steps:
        return None
    if signal.responsible_step is None:
        return None
    return str(signal.responsible_step) in signal.gold_steps


# How much confidence the detector's own localization procedure earned.
# "complete"  -> counterfactual ranking actually resolved the origin.
# "not_applicable" -> a single surviving origin, so nothing to disambiguate.
# "failed_temporal_fallback" -> two or more origins survived and the ranking
#   failed, so the origin was picked by timestamp rather than causal
#   sufficiency. That is a genuinely weaker localization.
_LOCALIZATION_STATUS_QUALITY = {
    "complete": 1.0,
    "not_applicable": 0.8,
    "failed_temporal_fallback": 0.2,
}


def _localization_quality(
    signal: DetectionSignal,
    row: Mapping[str, Any],
) -> float:
    """Grade localization by correctness, not by mere emission of a step.

    Rewarding any non-empty ``responsible_step`` let a module "improve" simply
    by always naming an origin — including the wrong one. This grades the
    origin instead:

    - No origin emitted at all -> 0.0.
    - Gold available for an error case -> correctness is authoritative.
    - Otherwise -> the detector's own localization procedure quality, so an
      ambiguous temporal fallback scores well below a counterfactually ranked
      origin and evolution has a real gradient to climb.
    """
    if not _responsible_step_present(row):
        return 0.0
    # Gold-step correctness is only trustworthy as POSITIVE evidence: it is
    # also False when no gold steps were supplied. Consult it only on
    # gold-labelled error cases, where an origin is genuinely expected.
    if signal.has_gold and signal.gold_label is True:
        return 1.0 if _gold_step_correct(signal) else 0.0
    localization_status = str(
        (signal.diagnostics.get("localization_status") if isinstance(
            signal.diagnostics, Mapping
        ) else None) or ""
    )
    return _LOCALIZATION_STATUS_QUALITY.get(localization_status, 0.4)


def module_local_score(
    module: str,
    signal: DetectionSignal,
    row: Mapping[str, Any] | None,
) -> float:
    """Return a module-aligned process score, not a global CRS surrogate."""
    row = row or {}
    prosecution = _coverage_rank(signal.prosecution_coverage)
    defense = _coverage_rank(signal.defense_coverage)

    if module == "candidate_trigger":
        # Crossing from no candidate to at least one candidate is meaningful;
        # unbounded candidate growth is intentionally not rewarded.
        return 1.0 if signal.candidate_count > 0 else 0.0
    if module == "candidate_to_verifier_route":
        return _prosecution_strength_score(signal, row)
    if module == "atomic_verifier_instruction":
        return _prosecution_strength_score(signal, row)
    if module == "evidence_obligation":
        # A one-sided proof is not progress. Reward the weaker side so that
        # evolution cannot improve this module by starving its challenger.
        return min(prosecution, defense)
    if module == "causal_link_test":
        # causal_link_test is injected into both causal-field providers and the
        # counterfactual localization route. A rule that turns temporal
        # fallback into complete counterfactual ranking is a genuine
        # module-local gain even when causal certificate field coverage was
        # already saturated.
        return _causal_field_score(row) + _localization_quality(signal, row)
    if module == "earliest_origin_tiebreak":
        # Correctness/quality of the chosen origin, not merely that one exists.
        return _localization_quality(signal, row) + _causal_field_score(row)
    return 0.0


def observable_process_delta(
    before: DetectionSignal,
    after: DetectionSignal,
    *,
    target_module: str,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    before_trigger: bool = False,
    after_trigger: bool = False,
    epsilon: float = 0.05,
) -> tuple[str, ...]:
    """Describe bounded detector-observable changes caused by one edit.

    The returned tokens contain no case identifiers or free-form trajectory
    text, so they are safe to persist and feed into a later reflection. This is
    the bridge between replay and learning: a rejected rule should teach the
    mutator *which executable output failed to move*, not merely that a scalar
    fitness gate rejected it.

    ``before_trigger`` / ``after_trigger`` are the repairability verdicts of the
    baseline and replay episodes (C10): the old ``should_evolve`` boolean lived
    on the signal, but Phase 4 moved the trigger into the Repairability
    Authority, so the loop threads it in explicitly.
    """
    changes: list[str] = []
    if before.state != after.state:
        changes.append(f"state:{before.state}->{after.state}")
    if before_trigger != after_trigger:
        changes.append(
            "trigger:closed" if not after_trigger else "trigger:opened"
        )
    if before.candidate_count != after.candidate_count:
        direction = "increased" if after.candidate_count > before.candidate_count else "decreased"
        changes.append(f"candidate_count:{direction}")

    for side, before_reading, after_reading in (
        ("prosecution", before.prosecution_coverage, after.prosecution_coverage),
        ("defense", before.defense_coverage, after.defense_coverage),
    ):
        before_rank = _coverage_rank(before_reading)
        after_rank = _coverage_rank(after_reading)
        if after_rank - before_rank > epsilon:
            changes.append(f"{side}_coverage:increased")
        elif before_rank - after_rank > epsilon:
            changes.append(f"{side}_coverage:decreased")

    before_local = module_local_score(target_module, before, before_result)
    after_local = module_local_score(target_module, after, after_result)
    if after_local - before_local > epsilon:
        changes.append("target_local_score:increased")
    elif before_local - after_local > epsilon:
        changes.append("target_local_score:decreased")

    before_result = before_result or {}
    after_result = after_result or {}

    # Certificate counters compare CLAIM-LEVEL valid sets (fingerprints of
    # non-quarantined rows), not raw row counts: positional re-minting moves
    # raw counts without moving any claim.
    def _valid_claim_count(value: Mapping[str, Any], kind: str) -> int:
        claims = _valid_claim_fingerprints(value, kind)
        if claims is None:
            return len(_certificate_rows(value, kind))
        return len(claims)

    counters = (
        (
            "prosecution_certificates",
            _valid_claim_count(before_result, "prosecution"),
            _valid_claim_count(after_result, "prosecution"),
        ),
        (
            "defense_certificates",
            _valid_claim_count(before_result, "defense"),
            _valid_claim_count(after_result, "defense"),
        ),
        (
            "quarantined_certificates",
            _quarantine_count(before_result),
            _quarantine_count(after_result),
        ),
    )
    for name, old, new in counters:
        if old != new:
            changes.append(f"{name}:{'increased' if new > old else 'decreased'}")

    # Claim-level surviving-carrier movement (rename-invariant). These are the
    # honest observable for fingerprint-targeted operations
    # (withhold_certificate / emit_certificate): whole-surface counters above
    # move whenever the provider reorganizes its census, while a positional id
    # comparison reads a pure rename as removal/emission. Tokens are emitted
    # only when every carrier resolved to a real content fingerprint — an
    # unresolved identity is never dressed up as movement.
    if before_result and after_result:
        movement = certificate_carrier_movement(before_result, after_result)
        if movement is not None and movement.get("resolved"):
            if movement["withheld"]:
                changes.append("surviving_certificates:decreased")
            if movement["emitted"]:
                changes.append("surviving_certificates:increased")

    before_causal = _causal_field_score(before_result)
    after_causal = _causal_field_score(after_result)
    if after_causal - before_causal > epsilon:
        changes.append("causal_field_coverage:increased")
    elif before_causal - after_causal > epsilon:
        changes.append("causal_field_coverage:decreased")

    before_responsible = bool(_responsible_step_present(before_result))
    after_responsible = bool(_responsible_step_present(after_result))
    if before_responsible != after_responsible:
        changes.append(
            "responsible_step:added" if after_responsible else "responsible_step:removed"
        )
    before_localization = str(
        (before.diagnostics.get("localization_status") if isinstance(
            before.diagnostics, Mapping
        ) else None) or ""
    )
    after_localization = str(
        (after.diagnostics.get("localization_status") if isinstance(
            after.diagnostics, Mapping
        ) else None) or ""
    )
    if before_localization != after_localization:
        changes.append(
            f"localization_status:{before_localization}"
            f"->{after_localization}"
        )
    before_localization_q = _localization_quality(before, before_result)
    after_localization_q = _localization_quality(after, after_result)
    if after_localization_q - before_localization_q > epsilon:
        changes.append("localization_quality:increased")
    elif before_localization_q - after_localization_q > epsilon:
        changes.append("localization_quality:decreased")
    if before.crs.complete != after.crs.complete:
        changes.append(
            "diagnostic_signal:gained" if after.crs.complete else "diagnostic_signal:lost"
        )
    return tuple(dict.fromkeys(changes))


def assess_source_fitness(
    before: DetectionSignal,
    after: DetectionSignal,
    *,
    target_module: str,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    before_trigger: bool = False,
    after_trigger: bool = False,
    gold_label: bool | None = None,
    epsilon: float = 0.05,
) -> FitnessAssessment:
    """Classify source replay evidence without self-certifying promotion.

    In self-supervised mode detector-internal state and local-score changes are
    candidate-generation telemetry only. Only explicit gold-assisted scoring (or
    a future independent promotion hook) may emit strong/local decisions.

    ``before_trigger`` / ``after_trigger`` are the repairability verdicts of the
    baseline and replay episodes (C10): the sole source of the "did the failure
    trigger close / open" signal now that ``should_evolve`` no longer lives on
    the detection signal.
    """
    reasons: list[str] = []
    # Invariant: a diagnostic-only signal (e.g. runtime_invalid) is never
    # promotable evolution evidence. The signal is the single projection, so its
    # state is already the resolved evolution state — no result re-resolution.
    if before.diagnostic_only or after.diagnostic_only:
        return FitnessAssessment("worse", ("diagnostic_only_signal_not_promotable",))
    if after.state not in VALID_STATES:
        return FitnessAssessment("worse", ("invalid_after_state",))

    task_before: int | None = None
    task_after: int | None = None
    if gold_label is not None:
        task_before = task_state_score(before.state, gold_label)
        task_after = task_state_score(after.state, gold_label)
        if task_after < task_before:
            return FitnessAssessment(
                "worse",
                ("gold_assisted_task_score_regressed",),
                task_before,
                task_after,
            )
        if task_after > task_before:
            decision = (
                "strong_improvement"
                if task_after == 2 and not after_trigger
                else "local_improvement"
            )
            return FitnessAssessment(
                decision,
                ("gold_assisted_task_score_improved",),
                task_before,
                task_after,
            )

    # A verdict-preserving edit that degrades the origin is still a regression:
    # downstream repair depends on the responsible step, and the
    # module_local_score below only sees it when the edit happened to target
    # earliest_origin_tiebreak. The guard applies only when the verdict did not
    # move: a wrong verdict retreating to unresolved must not be rejected for
    # losing an origin that the retreat invalidates, and a failure closure
    # (triggered -> stable) must not be demoted to a regression for the same
    # reason.
    if (
        before.state == after.state
        and _responsible_step_present(before_result or {})
        and not _responsible_step_present(after_result or {})
    ):
        return FitnessAssessment("worse", ("responsible_step_lost",))
    if before.crs.complete and not after.crs.complete:
        return FitnessAssessment("worse", ("required_diagnostic_signal_lost",))

    if before_trigger and not after_trigger:
        if gold_label is None:
            # Source replay is generated by the same detector whose rules are
            # being edited. Without an external label it can demonstrate that
            # the process became stable/decisive, but cannot establish that the
            # new verdict is correct. Keep this auditable evidence out of the
            # promotable strong/local classes.
            return FitnessAssessment(
                "directional_evidence",
                ("self_supervised_source_failure_closed_without_correctness",),
                task_before,
                task_after,
            )
        return FitnessAssessment(
            "strong_improvement",
            ("source_failure_closed",),
            task_before,
            task_after,
        )

    # Symmetric regression check: an edit that turns a previously stable
    # (non-triggered) detection into a triggered one is a regression, even when
    # coverage states are unchanged. The old asymmetric logic only rewarded
    # closing triggers and never penalized introducing them, which let
    # "decisive -> unresolved" regressions pass as no_effect in self-supervised
    # mode.
    if not before_trigger and after_trigger:
        return FitnessAssessment(
            "worse",
            ("new_failure_trigger_introduced",),
            task_before,
            task_after,
        )

    local_before = module_local_score(target_module, before, before_result)
    local_after = module_local_score(target_module, after, after_result)
    delta = local_after - local_before
    if abs(delta) <= epsilon:
        return FitnessAssessment(
            "no_effect",
            ("no_measurable_target_local_change",),
            task_before,
            task_after,
            local_before,
            local_after,
        )
    if gold_label is not None and _MODULE_SCORE_GOLD_DIRECTION.get(target_module) is not None:
        # Gold-aware direction: a module's local score is monotonic with one
        # polarity. On a gold-clean case, quarantining suspect certificates
        # LOWERS ``valid_error_certificate_count`` — that is the fix, not a
        # regression. Movement matching the module's polarity is an improvement;
        # movement against it is a regression. Positive movement against polarity
        # keeps the ``no_effect`` contract (audited, never rewarded), so modules
        # like ``causal_link_test`` keep their behavior.
        direction = _MODULE_SCORE_GOLD_DIRECTION[target_module]
        if delta > 0:
            if direction == gold_label:
                reasons.append(
                    f"module_local_score_improved:{local_before:.3f}->{local_after:.3f}"
                )
                return FitnessAssessment(
                    "local_improvement",
                    tuple(reasons),
                    task_before,
                    task_after,
                    local_before,
                    local_after,
                )
            return FitnessAssessment(
                "no_effect",
                ("score_direction_mismatch_with_gold",),
                task_before,
                task_after,
                local_before,
                local_after,
            )
        if direction != gold_label:
            reasons.append(
                f"module_local_score_improved:{local_before:.3f}->{local_after:.3f}"
            )
            return FitnessAssessment(
                "local_improvement",
                tuple(reasons),
                task_before,
                task_after,
                local_before,
                local_after,
            )
        reasons.append(
            f"module_local_score_regressed:{local_before:.3f}->{local_after:.3f}"
        )
        return FitnessAssessment(
            "worse",
            tuple(reasons),
            task_before,
            task_after,
            local_before,
            local_after,
        )
    if delta > 0:
        if gold_label is None:
            return FitnessAssessment(
                "directional_evidence",
                (
                    "self_supervised_module_local_score_improved_without_"
                    "external_correctness",
                    f"module_local_score:{local_before:.3f}->{local_after:.3f}",
                ),
                task_before,
                task_after,
                local_before,
                local_after,
            )
        reasons.append(f"module_local_score_improved:{local_before:.3f}->{local_after:.3f}")
        return FitnessAssessment(
            "local_improvement",
            tuple(reasons),
            task_before,
            task_after,
            local_before,
            local_after,
        )
    if (
        delta < 0
        and (
            _MODULE_SCORE_GOLD_DIRECTION.get(target_module) is None
            or gold_label is None
        )
    ):
        # Neutral-direction module (e.g. candidate_trigger, whose local score
        # only says whether candidates exist): a local-score drop with an
        # unchanged task score carries no demonstrable harm — removing redundant
        # candidates on a clean case is a fix, not a regression. Degrade to
        # ``no_effect`` (audited, never a worse veto) instead of a hard
        # regression; gold-assessed task improvements were already returned
        # above, so reaching here means the task score did not move.
        #
        # Label-free mode extends the same degradation to direction-annotated
        # modules: the direction annotation is only meaningful under a gold
        # label, which tells the loop which way the score may move. Without
        # gold, a score drop is ambiguous — withdrawing an over-convicted
        # certificate (an error_to_clean repair) lowers
        # ``valid_error_certificate_count`` exactly as a genuine loss of error
        # evidence would, and the loop has no authority to pick which.
        # Hard-rejecting it as ``worse`` silently discards every label-free
        # FP-direction repair. Degrading to ``no_effect`` keeps the ambiguous
        # drop auditable without vetoing a possibly-correct repair; gold-mode
        # behavior is unchanged (the gold-aware branch above already returned).
        return FitnessAssessment(
            "no_effect",
            ("neutral_direction_module_local_drop",),
            task_before,
            task_after,
            local_before,
            local_after,
        )
    reasons.append(f"module_local_score_regressed:{local_before:.3f}->{local_after:.3f}")
    return FitnessAssessment(
        "worse",
        tuple(reasons),
        task_before,
        task_after,
        local_before,
        local_after,
    )


__all__ = [
    "FitnessAssessment",
    "assess_source_fitness",
    "module_local_score",
    "observable_process_delta",
    "task_state_score",
]
