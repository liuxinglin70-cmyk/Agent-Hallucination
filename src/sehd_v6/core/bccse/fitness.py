"""Fitness primitives for BCCSE v5 shadow evolution.

The structural gate answers whether an edit is legal.  This module answers a
different question: whether the edit produced a measurable improvement in the
part of the pipeline its target module can influence.

The evaluator deliberately does not require every local edit to turn an
unresolved source into a definite state.  Such edits can enter a reversible
shadow genome and are committed only after a small batch remains beneficial.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .step_attributor import AttributionSignal
from .signal_adapter import COVERAGE_STATE_SCORE, resolve_authoritative_state


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
    "candidate_to_verifier_route": None,
    "atomic_verifier_instruction": None,
    # v5.6 fix: this was ``True`` ("gap up = prosecution stronger"), an
    # annotation left over from when the score was ``_coverage_gap``. The
    # score has been ``min(prosecution, defense)`` since the one-sided-proof
    # fix, i.e. it rises only when the WEAKER side improves, which is
    # polarity-neutral by construction. Keeping the stale ``True`` meant
    # every genuine evidence_obligation gain on a gold-CLEAN case failed the
    # direction check and was demoted to ``no_effect``: the module could only
    # ever be credited on gold-error cases. Same bias class as B2.
    "evidence_obligation": None,
    "clean_defense_boundary": False,  # defense up = cleaner
    "causal_link_test": True,      # causal chain up = more error-like
    "earliest_origin_tiebreak": None,
    "abstention_rule": None,  # v5.3.1: neutral - any resolution is progress
}


def _score_direction_matches_gold(module: str, delta: float, gold: bool) -> bool:
    """Check if score change direction is consistent with gold direction."""
    if delta <= 0:
        return True
    direction = _MODULE_SCORE_GOLD_DIRECTION.get(module)
    if direction is None:
        return True
    # direction=True means score-up is good for error (gold=True)
    # direction=False means score-up is good for clean (gold=False)
    return direction == gold


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
    """
    if state == "unresolved":
        return 1
    if state == "verified_error":
        return 2 if gold_label else 0
    if state == "verified_clean":
        return 0 if gold_label else 2
    raise ValueError(f"invalid detector state: {state!r}")


def _coverage(signal: AttributionSignal, side: str) -> float | None:
    value = getattr(signal, f"{side}_coverage")
    return None if value is None else float(value)


def _coverage_rank(signal: AttributionSignal, side: str) -> float:
    value = _coverage(signal, side)
    if value is not None:
        return value
    state = getattr(signal, f"{side}_coverage_state")
    # Single source of truth: the categorical->numeric mapping is owned by
    # signal_adapter (where "incomplete"=0.3 is deliberately aligned with
    # COVERAGE_LOW_THRESHOLD). A second literal copy here silently drifts.
    return COVERAGE_STATE_SCORE.get(str(state or ""), 0.0)


def _candidate_rows(row: Mapping[str, Any]) -> int:
    payload = row.get("combined_prosecution") or row.get("prosecution") or {}
    candidates = payload.get("candidates") if isinstance(payload, Mapping) else []
    return len(candidates or [])


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


def _causal_field_score(row: Mapping[str, Any]) -> float:
    certificates = _certificate_rows(row, "prosecution")
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
    signal: AttributionSignal,
    row: Mapping[str, Any],
) -> float:
    """Grade localization by correctness, not by mere emission of a step.

    The previous metric rewarded any non-empty ``responsible_step``, so a
    module could "improve" simply by always naming an origin — including
    the wrong one. This grades the origin instead:

    - No origin emitted at all -> 0.0.
    - Gold available for an error case -> correctness is authoritative.
    - Otherwise -> the detector's own localization procedure quality, so
      an ambiguous temporal fallback scores well below a counterfactually
      ranked origin and evolution has a real gradient to climb.
    """
    if not _responsible_step_present(row):
        return 0.0
    # ``responsible_step_correct`` is only trustworthy as POSITIVE evidence:
    # it is also False when no gold steps were supplied. Consult it only on
    # gold-labelled error cases, where an origin is genuinely expected.
    if signal.has_gold and signal.gold_label is True:
        return 1.0 if signal.responsible_step_correct else 0.0
    return _LOCALIZATION_STATUS_QUALITY.get(
        str(signal.localization_status or ""), 0.4
    )


def module_local_score(
    module: str,
    signal: AttributionSignal,
    row: Mapping[str, Any] | None,
) -> float:
    """Return a module-aligned process score, not a global CRS surrogate."""
    row = row or {}
    prosecution = _coverage_rank(signal, "prosecution")
    defense = _coverage_rank(signal, "defense")

    if module == "candidate_trigger":
        # Crossing from no candidate to at least one candidate is meaningful;
        # unbounded candidate growth is intentionally not rewarded.
        return 1.0 if signal.candidate_count > 0 else 0.0
    if module == "candidate_to_verifier_route":
        return float(_candidate_rows(row)) + prosecution
    if module == "atomic_verifier_instruction":
        valid = len(_certificate_rows(row, "prosecution"))
        return float(valid - _quarantine_count(row)) + prosecution
    if module == "evidence_obligation":
        # A one-sided proof is not progress. Reward the weaker side so that
        # evolution cannot improve this module by starving its challenger.
        return min(prosecution, defense)
    if module == "clean_defense_boundary":
        return defense + float(len(_certificate_rows(row, "defense")))
    if module == "causal_link_test":
        return _causal_field_score(row)
    if module == "earliest_origin_tiebreak":
        # Correctness/quality of the chosen origin, not merely that one
        # exists. Guarding against "emit any step" gaming is the whole
        # point of this module's score.
        return _localization_quality(signal, row) + _causal_field_score(row)
    if module == "abstention_rule":
        # v5.3.2: graduated score reflecting partial progress toward verdict.
        # Full resolution is best, but CRS/consensus progress is measurable.
        #
        # FIX (B2): 旧版 clean=2.0, error=1.5 偏好 clean 方向，导致进化
        # 系统性地将 unresolved→clean，即使 gold=positive（应判 error）。
        # 修复策略：
        # 1. 当 gold_label 可用时，正确方向得高分，错误方向得 0 分
        # 2. 当 gold_label 不可用时（自监督），clean 和 error 对称（均 2.0）
        if signal.state == "verified_clean":
            if signal.has_gold:
                return 2.0 if not signal.gold_label else 0.0
            return 2.0
        if signal.state == "verified_error":
            if signal.has_gold:
                return 2.0 if signal.gold_label else 0.0
            return 2.0
        # unresolved: measure partial progress toward resolution
        score = 0.0
        # CRS completeness indicates diagnostic signal is available
        if signal.crs_complete:
            score += 0.4
        # Consensus gate applied means evidence was sufficient for a decision
        if signal.consensus_gate_applied:
            score += 0.4
        # v5.6 fix (B2, second half): the graduated sub-score used to weight
        # defense above prosecution (complete: 0.3 vs 0.2) and then take
        # max(), which re-introduced exactly the clean-leaning bias B2 was
        # opened to remove — only at lower amplitude and only on the
        # unresolved branch, so the B2 verdict-level fix did not cover it.
        # Two consequences, both observed:
        #   - completing the DEFENSE scored higher than completing the
        #     PROSECUTION from the same starting point (0.7 vs 0.6);
        #   - because of max(), an edit that completed the second side was
        #     credited when that side was the defense but registered exactly
        #     0.0 delta ("no_effect") in the mirror-image case.
        # Coverage progress is now the MEAN of both sides' ranks: symmetric
        # under prosecution/defense swap, and it keeps rewarding the second
        # side because an unresolved case needs both to resolve.
        score += 0.4 * (prosecution + defense) / 2.0
        return min(score, 1.2)  # cap below definite verdict scores
    return 0.0


def observable_process_delta(
    before: AttributionSignal,
    after: AttributionSignal,
    *,
    target_module: str,
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    epsilon: float = 0.05,
) -> tuple[str, ...]:
    """Describe bounded detector-observable changes caused by one edit.

    The returned tokens contain no case identifiers or free-form trajectory
    text, so they are safe to persist and feed into a later reflection. This
    is the missing bridge between replay and learning: a rejected rule should
    teach the mutator *which executable output failed to move*, not merely
    that a scalar fitness gate rejected it.
    """
    changes: list[str] = []
    if before.state != after.state:
        changes.append(f"state:{before.state}->{after.state}")
    if before.should_evolve != after.should_evolve:
        changes.append(
            "trigger:closed" if not after.should_evolve else "trigger:opened"
        )
    if before.candidate_count != after.candidate_count:
        direction = "increased" if after.candidate_count > before.candidate_count else "decreased"
        changes.append(f"candidate_count:{direction}")

    for side in ("prosecution", "defense"):
        before_rank = _coverage_rank(before, side)
        after_rank = _coverage_rank(after, side)
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
    counters = (
        (
            "prosecution_certificates",
            len(_certificate_rows(before_result, "prosecution")),
            len(_certificate_rows(after_result, "prosecution")),
        ),
        (
            "defense_certificates",
            len(_certificate_rows(before_result, "defense")),
            len(_certificate_rows(after_result, "defense")),
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
    if before.localization_status != after.localization_status:
        changes.append(
            f"localization_status:{before.localization_status}"
            f"->{after.localization_status}"
        )
    before_localization = _localization_quality(before, before_result)
    after_localization = _localization_quality(after, after_result)
    if after_localization - before_localization > epsilon:
        changes.append("localization_quality:increased")
    elif before_localization - after_localization > epsilon:
        changes.append("localization_quality:decreased")
    if before.crs_complete != after.crs_complete:
        changes.append(
            "diagnostic_signal:gained" if after.crs_complete else "diagnostic_signal:lost"
        )
    return tuple(dict.fromkeys(changes))


def assess_source_fitness(
    before: AttributionSignal,
    after: AttributionSignal,
    *,
    target_module: str,
    operation: str = "add_rule",
    before_result: Mapping[str, Any] | None = None,
    after_result: Mapping[str, Any] | None = None,
    gold_label: bool | None = None,
    epsilon: float = 0.05,
) -> FitnessAssessment:
    """Classify source replay evidence without self-certifying promotion.

    In self-supervised mode detector-internal state and local-score changes are
    candidate-generation telemetry only. Only explicit gold-assisted scoring
    (or a future independent promotion hook) may emit strong/local decisions.
    """
    if before_result is not None:
        before = replace(
            before,
            state=resolve_authoritative_state(before_result),
        )
    if after_result is not None:
        after = replace(
            after,
            state=resolve_authoritative_state(after_result),
        )
    reasons: list[str] = []
    if after.state not in VALID_STATES:
        return FitnessAssessment("worse", ("invalid_after_state",))
    if before.crs_complete and not after.crs_complete:
        return FitnessAssessment("worse", ("required_diagnostic_signal_lost",))
    # A verdict-preserving edit that degrades the origin is still a
    # regression: downstream repair depends on the responsible step, and
    # the module_local_score below only sees it when the edit happened to
    # target earliest_origin_tiebreak.
    if (
        _responsible_step_present(before_result or {})
        and not _responsible_step_present(after_result or {})
    ):
        return FitnessAssessment("worse", ("responsible_step_lost",))

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
                if task_after == 2 and not after.should_evolve
                else "local_improvement"
            )
            return FitnessAssessment(
                decision,
                ("gold_assisted_task_score_improved",),
                task_before,
                task_after,
            )

    if before.should_evolve and not after.should_evolve:
        if gold_label is None:
            # Source replay is generated by the same detector whose rules are
            # being edited. Without an external label it can demonstrate that
            # the process became stable/decisive, but cannot establish that
            # the new verdict is correct. Keep this auditable evidence out of
            # the promotable strong/local classes.
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

    # v5.4 symmetric regression check: an edit that turns a previously
    # stable (non-triggered) detection into a triggered one is a regression,
    # even when coverage states are unchanged. The old asymmetric logic only
    # rewarded closing triggers and never penalized introducing them, which
    # let "decisive -> unresolved" regressions pass as no_effect in
    # self-supervised mode.
    if not before.should_evolve and after.should_evolve:
        return FitnessAssessment(
            "worse",
            ("new_failure_trigger_introduced",),
            task_before,
            task_after,
        )

    local_before = module_local_score(target_module, before, before_result)
    local_after = module_local_score(target_module, after, after_result)
    delta = local_after - local_before
    if delta > epsilon:
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
        # v5.3: check gold direction consistency
        if gold_label is not None and not _score_direction_matches_gold(target_module, delta, gold_label):
            return FitnessAssessment(
                "no_effect",
                ("score_direction_mismatch_with_gold",),
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
    if delta < -epsilon:
        reasons.append(f"module_local_score_regressed:{local_before:.3f}->{local_after:.3f}")
        return FitnessAssessment(
            "worse",
            tuple(reasons),
            task_before,
            task_after,
            local_before,
            local_after,
        )

    return FitnessAssessment(
        "no_effect",
        ("no_measurable_target_local_change",),
        task_before,
        task_after,
        local_before,
        local_after,
    )
