"""BCCSE v4 independent semantic, leakage, and construct critics.

Mutation proposal and mutation approval are intentionally separated.  These
critics are deterministic and fail-closed: the semantic critic rejects no-op
or unsupported operations, the leakage critic enforces the identifier/content
firewall, and the construct critic preserves immutable layers, official versus
auxiliary accounting, recovery semantics, agent-origin attribution, and the
registered CRS weights.

Passing critics does not imply promotion; it only permits deterministic genome
compilation followed by G0-G3 validation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .types import FailureEpisode
from .genome import (
    FORBIDDEN_CONTENT_MARKERS,
    AtomicMutation,
    MutationOp,
    SkillGenome,
    validate_mutation,
)
from .mutator import MUTABLE_MODULES, MutationProposal, ProposedMutation


FROZEN_CRS_WEIGHTS = (0.35, 0.15, 0.20, 0.20, 0.10)
IDENTITY_PATTERNS = (
    re.compile(r"\bcase[_:\-\s]*[a-z0-9]{2,}\b", re.IGNORECASE),
    re.compile(r"\bsource[_:\-\s]*[a-z0-9]{2,}\b", re.IGNORECASE),
    # Round 8 fix: require digit in suffix to avoid matching domain terms
    # like "agent_adapts". Real identifiers have alphanumeric suffixes (e.g. agent_007).
    re.compile(r"\bagent[_:\-\s]*[a-z0-9]*\d[a-z0-9]*\b", re.IGNORECASE),
    re.compile(r"\bmodel[_:\-\s]*(?:id|name|[a-z0-9]{2,})\b", re.IGNORECASE),
)
CONSTRUCT_VIOLATION_PATTERNS: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "process_only_as_official_positive": (
        re.compile(r"process[_\s-]*only.*official[_\s-]*positive", re.IGNORECASE),
        re.compile(r"process[_\s-]*debt.*hallucination", re.IGNORECASE),
    ),
    "external_observation_as_agent_origin": (
        re.compile(r"external[_\s-]*observation.*agent[_\s-]*origin", re.IGNORECASE),
        re.compile(r"tool[_\s-]*output.*(?:is|as).*origin", re.IGNORECASE),
    ),
    "recovery_erases_incident": (
        re.compile(r"recovery.*(?:erase|negate|remove).*incident", re.IGNORECASE),
        re.compile(r"recovered.*(?:clean|negative).*official", re.IGNORECASE),
    ),
}


class CriticDecision(str, Enum):
    PASS = "pass"
    REJECT = "reject"


@dataclass(frozen=True)
class CriticResult:
    """Result from one independent critic."""

    critic: str
    decision: CriticDecision
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.decision == CriticDecision.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "critic": self.critic,
            "decision": self.decision.value,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class CriticSuiteResult:
    """Joint verdict; all three critics must independently pass."""

    semantic: CriticResult
    leakage: CriticResult
    construct: CriticResult
    schema_version: str = "agenthallu.skill_critic_suite.v4"

    @property
    def passed(self) -> bool:
        return self.semantic.passed and self.leakage.passed and self.construct.passed

    @property
    def decision(self) -> CriticDecision:
        return CriticDecision.PASS if self.passed else CriticDecision.REJECT

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision.value,
            "passed": self.passed,
            "results": {
                "semantic": self.semantic.to_dict(),
                "leakage": self.leakage.to_dict(),
                "construct": self.construct.to_dict(),
            },
        }


def _result(name: str, reasons: Iterable[str], evidence: Iterable[str] = ()) -> CriticResult:
    reason_rows = tuple(dict.fromkeys(reasons))
    return CriticResult(
        critic=name,
        decision=CriticDecision.REJECT if reason_rows else CriticDecision.PASS,
        reasons=reason_rows,
        evidence=tuple(dict.fromkeys(evidence)),
    )


def _proposal_rows(
    proposal: MutationProposal | Sequence[ProposedMutation] | Sequence[AtomicMutation],
) -> list[tuple[AtomicMutation, tuple[str, ...]]]:
    if isinstance(proposal, MutationProposal):
        return [(item.mutation, item.support_episode_ids) for item in proposal.proposed]
    rows: list[tuple[AtomicMutation, tuple[str, ...]]] = []
    for item in proposal:
        if isinstance(item, ProposedMutation):
            rows.append((item.mutation, item.support_episode_ids))
        elif isinstance(item, AtomicMutation):
            rows.append((item, ()))
        else:
            raise TypeError(f"unsupported mutation proposal item: {type(item)!r}")
    return rows


def semantic_critic(
    proposal: MutationProposal | Sequence[ProposedMutation] | Sequence[AtomicMutation],
    *,
    parent: SkillGenome,
    episodes: Sequence[FailureEpisode],
) -> CriticResult:
    """Reject invalid, no-op, ungrounded, or semantically over-broad edits."""

    rows = _proposal_rows(proposal)
    episode_by_id = {item.episode_id: item for item in episodes}
    reasons: list[str] = []
    evidence: list[str] = []
    if not 1 <= len(rows) <= 2:
        reasons.append("atomic_mutation_budget_violation")

    for mutation, support_ids in rows:
        valid, reason = validate_mutation(mutation, parent)
        if not valid:
            reasons.append(f"{mutation.mutation_id}:invalid_mutation:{reason}")
            continue
        if mutation.target_module not in MUTABLE_MODULES:
            reasons.append(f"{mutation.mutation_id}:immutable_target")
        module = parent.modules[mutation.target_module]
        if mutation.operation == MutationOp.ACTIVATE and module.active:
            reasons.append(f"{mutation.mutation_id}:activate_noop")
        if mutation.operation == MutationOp.DEACTIVATE and not module.active:
            reasons.append(f"{mutation.mutation_id}:deactivate_noop")
        if mutation.operation == MutationOp.RETIRE and (module.retired or not module.active):
            reasons.append(f"{mutation.mutation_id}:retire_noop_or_inactive")
        if mutation.operation == MutationOp.ADD_NEGATIVE_BOUNDARY:
            if mutation.new_content in module.negative_boundaries:
                reasons.append(f"{mutation.mutation_id}:duplicate_negative_boundary")
        if mutation.operation == MutationOp.REPLACE_TYPED_CLAUSE:
            old = next((clause.body for clause in module.clauses if clause.clause_id == mutation.target_clause_id), None)
            if old is None:
                reasons.append(f"{mutation.mutation_id}:unknown_target_clause")
            elif old.strip() == str(mutation.new_content or "").strip():
                reasons.append(f"{mutation.mutation_id}:replacement_noop")
        if mutation.operation == MutationOp.TIGHTEN_PRECONDITION:
            clause = next((item for item in module.clauses if item.clause_id == mutation.target_clause_id), None)
            if clause is None:
                reasons.append(f"{mutation.mutation_id}:unknown_target_clause")
            elif mutation.new_content in clause.preconditions:
                reasons.append(f"{mutation.mutation_id}:duplicate_precondition")

        if not mutation.rationale.strip():
            reasons.append(f"{mutation.mutation_id}:missing_rationale")
        if not support_ids:
            reasons.append(f"{mutation.mutation_id}:missing_episode_provenance")
        elif not set(support_ids) <= set(episode_by_id):
            reasons.append(f"{mutation.mutation_id}:unknown_episode_provenance")
        else:
            hinted = {episode_by_id[item].affected_module_hint for item in support_ids}
            if mutation.target_module not in hinted:
                # Cross-module edits are allowed when the rationale names the
                # targeted mechanism. Round 8: accept if the rationale references
                # the target clause id, the full module name, or any significant
                # word from the module name (e.g. "defense" for clean_defense_boundary).
                token = mutation.target_module.replace("_", " ")
                rationale_lower = mutation.rationale.lower().replace("_", " ")
                clause_id = str(mutation.target_clause_id or "").lower()
                clause_in_rationale = bool(clause_id) and clause_id in mutation.rationale.lower()
                module_words = [w for w in mutation.target_module.split("_") if len(w) > 3]
                word_in_rationale = any(w in rationale_lower for w in module_words)
                if (
                    token not in rationale_lower
                    and not clause_in_rationale
                    and not word_in_rationale
                ):
                    reasons.append(f"{mutation.mutation_id}:unsupported_cross_module_target")
            evidence.extend(support_ids)

    return _result("semantic", reasons, evidence)


def leakage_critic(
    proposal: MutationProposal | Sequence[ProposedMutation] | Sequence[AtomicMutation],
) -> CriticResult:
    """Scan the complete proposal for forbidden identifiers and feedback."""

    rows = _proposal_rows(proposal)
    value = [
        {
            "mutation": mutation.to_dict(),
            "support_episode_ids": list(support_ids),
        }
        for mutation, support_ids in rows
    ]
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    lowered = text.lower()
    reasons = [
        f"forbidden_marker:{marker}"
        for marker in sorted(FORBIDDEN_CONTENT_MARKERS)
        if marker in lowered
    ]
    for pattern in IDENTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            reasons.append(f"identity_pattern:{match.group(0)[:48]}")
    for marker in ("gold_label", "gold explanation", "outer feedback", "outer score"):
        if marker in lowered:
            reasons.append(f"forbidden_feedback:{marker}")
    return _result("leakage", reasons)


def _changed_crs_weights(text: str) -> bool:
    lowered = text.lower()
    if not any(token in lowered for token in ("factuality", "counterfactual", "recovery", "process", "scope")):
        return False
    numbers = {
        name: re.search(rf"{name}\s*=\s*(0?\.\d+|1(?:\.0+)?)", lowered)
        for name in ("factuality", "counterfactual", "recovery", "process", "scope")
    }
    if not all(numbers.values()):
        return False
    observed = tuple(float(numbers[name].group(1)) for name in numbers)
    return observed != FROZEN_CRS_WEIGHTS


def construct_critic(
    proposal: MutationProposal | Sequence[ProposedMutation] | Sequence[AtomicMutation],
    *,
    expected_constitution_hash: str,
    observed_constitution_hash: str,
) -> CriticResult:
    """Preserve the registered hallucination construct and immutable layers."""

    rows = _proposal_rows(proposal)
    reasons: list[str] = []
    if not expected_constitution_hash or expected_constitution_hash != observed_constitution_hash:
        reasons.append("constitution_hash_mismatch")
    for mutation, _ in rows:
        if mutation.target_module not in MUTABLE_MODULES:
            reasons.append(f"{mutation.mutation_id}:L0_L1_L6_L7_mutation")
        text = " ".join(filter(None, (mutation.new_content, mutation.rationale)))
        if _changed_crs_weights(text):
            reasons.append(f"{mutation.mutation_id}:CRS_weight_change")
        for name, patterns in CONSTRUCT_VIOLATION_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                reasons.append(f"{mutation.mutation_id}:{name}")
        if "official" in text.lower() and "auxiliary" in text.lower():
            if re.search(r"(?:merge|conflat|same|equivalent|promote)", text, re.IGNORECASE):
                reasons.append(f"{mutation.mutation_id}:official_auxiliary_conflation")
        if "continuous_threshold_search" in text.lower() or re.search(
            r"(?:optimi[sz]e|search|tune).{0,24}threshold", text, re.IGNORECASE
        ):
            reasons.append(f"{mutation.mutation_id}:continuous_threshold_search")
    return _result("construct", reasons)


def run_critics(
    proposal: MutationProposal | Sequence[ProposedMutation] | Sequence[AtomicMutation],
    *,
    parent: SkillGenome,
    episodes: Sequence[FailureEpisode],
    expected_constitution_hash: str,
    observed_constitution_hash: str,
) -> CriticSuiteResult:
    """Run all independent critics and return their joint fail-closed verdict."""

    return CriticSuiteResult(
        semantic=semantic_critic(proposal, parent=parent, episodes=episodes),
        leakage=leakage_critic(proposal),
        construct=construct_critic(
            proposal,
            expected_constitution_hash=expected_constitution_hash,
            observed_constitution_hash=observed_constitution_hash,
        ),
    )
