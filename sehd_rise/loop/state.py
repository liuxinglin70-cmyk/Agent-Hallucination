"""SEHD-RISE loop mutable run state and L2 promotion evidence.

``RunState`` groups the rebinding fields shared by the probe / L2 /
shadow-batch stages so each extracted stage function reads and writes
``state.<field>`` instead of threading dozens of returns through the main
loop. ``l2_promotion_evidence`` distills the measured L2 replay facts into a
``PromotionEvidence`` so the promotion grade reflects reality instead of
defaulting to weak.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sehd_rise.evaluation.promotion import PromotionEvidence
from sehd_rise.loop.result import EvolutionStep


@dataclass
class RunState:
    """Mutable run state shared by the probe / L2 / shadow-batch stages.

    These fields are rebound as the run evolves: probe rollback swaps the
    genome triple, fitness commit advances the edit counters, and the
    episode payload is rebuilt per attempt. Grouping them lets the stage
    functions read/write ``state.<field>`` instead of threading dozens of
    rebinding returns through the main loop.
    """

    champion_genome: Any = None
    current_genome: Any = None
    stable_genome: Any = None
    policy_genome: Any = None
    stable_policy_genome: Any = None
    l2_policy_accepts: int = 0
    stable_l2_policy_accepts: int = 0
    meta_skill_state: Any = None
    edits_accepted: int = 0
    edits_rejected: int = 0
    rollback_reason: str = ""
    last_probe_accepted_count: int = 0
    last_probe_triggered_count: int = 0
    stable_history_length: int = 0
    stable_memory_payload: Any = None
    stable_canaries_checkpoint: Any = None
    active_episode_payload: Any = None
    triggered_cases: int = 0
    stable_cases: int = 0


def l2_promotion_evidence(
    l2_audit: Mapping[str, Any], step: EvolutionStep,
) -> PromotionEvidence:
    """Distill the measured L2 replay facts into a PromotionEvidence.

    The L2 accept path already enforced every safety check before reaching
    ``ACCEPTED``: the proposed item must have fired, the terminal state
    must have changed toward the gold label, and no canary may have
    regressed. Those facts live in the audit entry but were never forwarded
    to ``promote()``, so every accept was graded ``weak`` regardless of how
    strong the measured evidence was. This function reads the same audit
    fields the accept path wrote and packages them so the grade reflects
    reality.
    """
    proposal = step.edit_proposal if isinstance(step.edit_proposal, Mapping) else {}
    proposal_item_id = ""
    new_item = proposal.get("new_item")
    if isinstance(new_item, Mapping):
        proposal_item_id = str(new_item.get("item_id") or "")

    policy_eval = l2_audit.get("policy_evaluation")
    if not isinstance(policy_eval, Mapping):
        policy_eval = {}

    fired_rows = policy_eval.get("fired") or []
    execution_established = bool(
        proposal_item_id
        and any(
            isinstance(row, Mapping)
            and str(row.get("item_id") or "") == proposal_item_id
            for row in fired_rows
        )
    )

    replay_state = str(l2_audit.get("replay_state") or "")
    detection_state = str(step.detection_state or "")
    terminal_effect = bool(replay_state and replay_state != detection_state)

    canary_regressions = l2_audit.get("canary_regressions") or []
    canary_audit = l2_audit.get("canary_audit") or []
    non_regression = (
        not canary_regressions
        and all(
            isinstance(row, Mapping) and row.get("preserved") is not False
            for row in canary_audit
        )
        if canary_audit
        else True
    )

    # The L2 path only accepts a state change when gold was available and
    # the task score improved. An accepted state-changing L2 edit therefore
    # carries a gold-polarity verification by construction. A
    # state-preserving accept (coverage-only) carries no terminal evidence
    # and stays weak.
    gold_polarity = terminal_effect and str(step.gate_decision) == "ACCEPTED"

    return PromotionEvidence(
        execution_established=execution_established,
        terminal_effect=terminal_effect,
        gold_polarity=gold_polarity,
        independent_effect=None,
        non_regression=non_regression,
    )
