"""Cross-Case Strategy Learner state and audited strategy edits (plan §2.8).

C7 redefines the Meta layer from a gate-profile switcher into a Cross-Case
Strategy Learner: it learns *how to search and select* across many verified
RepairPlan outcomes, never *what is causally broken*. The five strategy
domains are the closed learning surface (plan §2.8 "Meta 负责"):

    search prior | operation preference | mechanism ranking
    retry strategy | candidate diversity

Everything outside that surface — the current failure owner, the causal cut,
repairability, the correctness authority and the hard gate — is structurally
unreachable from here: no token for it exists in any domain vocabulary, so a
``StrategyEdit`` can never express it (§2.8 "Meta 不负责", made a type-level
constraint rather than a convention).

``StrategyState`` carries a stable ``content_hash`` and immutable lineage so a
strategy configuration is replay-auditable like any other genome. Schema:
``sehd_rise.strategy_state.v1``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION = "sehd_rise.strategy_state.v1"

# ============================================================
# Strategy domains (plan §2.8) — the closed learning surface
# ============================================================

SEARCH_PRIOR = "search_prior"
OPERATION_PREFERENCE = "operation_preference"
MECHANISM_RANKING = "mechanism_ranking"
RETRY_STRATEGY = "retry_strategy"
CANDIDATE_DIVERSITY = "candidate_diversity"

STRATEGY_DOMAINS = frozenset({
    SEARCH_PRIOR,
    OPERATION_PREFERENCE,
    MECHANISM_RANKING,
    RETRY_STRATEGY,
    CANDIDATE_DIVERSITY,
})

# Per-domain closed vocabularies. A token belongs to exactly one domain, so
# the domain declared on a StrategyEdit is both an audit signal and a second
# validity gate: a token valid in one domain is rejected in every other.
#
# Tokens that named an owner, the causal cut or the gate (e.g.
# ``bind_action_to_owned_output``, ``avoid_downstream_invariant_surface``,
# ``owned_output_match``, ``downstream_edge_reachable`` and the gate profiles)
# have no home here — §2.8 "Meta 不负责 当前 failure owner / 当前 causal cut /
# repairability / hard gate relaxation".
DOMAIN_TOKENS: Mapping[str, frozenset[str]] = {
    SEARCH_PRIOR: frozenset({
        "prefer_observable_discriminator",
        "require_independent_anchor",
        "avoid_condition_without_discriminator",
    }),
    OPERATION_PREFERENCE: frozenset({
        "prefer_less_destructive_policy",
        "prefer_registered_runtime_route",
        "avoid_pending_registry_dependency",
        "avoid_redundant_intervention",
        "destructive_scope",
        "registry_stability",
        "cost_efficiency",
    }),
    MECHANISM_RANKING: frozenset({
        "action_shape_previously_effective",
        "action_shape_previously_no_effect",
        "observable_field_available",
        "independent_anchor_available",
        "recent_feedback_weight",
        "severity_weight",
        "mechanism_reliability",
    }),
    RETRY_STRATEGY: frozenset({
        "reuse_effective_action_shape",
        "avoid_unexecuted_action_shape",
        "avoid_action_shape_previously_failed",
    }),
    CANDIDATE_DIVERSITY: frozenset({
        "seek_distinct_candidate_direction",
        "duplicate_risk",
    }),
}

ALL_STRATEGY_TOKENS = frozenset().union(*DOMAIN_TOKENS.values())

# ============================================================
# StrategyEdit operations — the closed first-version set
# ============================================================

OP_ENABLE = "enable_strategy"
OP_DISABLE = "disable_strategy"
STRATEGY_EDIT_OPERATIONS = frozenset({OP_ENABLE, OP_DISABLE})


# ============================================================
# StrategyEdit
# ============================================================

@dataclass(frozen=True)
class StrategyEdit:
    """One audited strategy-level edit.

    ``domain``/``target`` name a closed-vocabulary strategy token; never free
    text, a numeric threshold, an owner, a cut, or a gate. The target MUST
    belong to the declared domain or the edit is rejected at validation.
    """

    edit_id: str
    operation: str
    domain: str
    target: str
    rationale: str = ""
    source_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "operation": self.operation,
            "domain": self.domain,
            "target": self.target,
            "rationale": self.rationale,
            "source_evidence": list(self.source_evidence),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StrategyEdit":
        if not isinstance(value, Mapping):
            raise ValueError("strategy edit must contain an object")
        return cls(
            edit_id=str(value.get("edit_id") or ""),
            operation=str(value.get("operation") or ""),
            domain=str(value.get("domain") or ""),
            target=str(value.get("target") or ""),
            rationale=str(value.get("rationale") or ""),
            source_evidence=tuple(
                str(v) for v in (value.get("source_evidence") or ())
            ),
        )


# ============================================================
# StrategyState
# ============================================================

@dataclass
class StrategyState:
    """The evolving strategy configuration: per-domain enabled tokens, with a
    stable identity and immutable lineage."""

    state_id: str
    enabled: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    generation: int = 0
    parent_hash: str | None = None
    edits_applied: list[StrategyEdit] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Closed-vocabulary enforcement: an unknown domain or token can never
        # enter the state (fail-closed). Every domain is always present, so a
        # reader never distinguishes "absent" from "empty".
        cleaned: dict[str, tuple[str, ...]] = {}
        for domain in STRATEGY_DOMAINS:
            tokens = self.enabled.get(domain, ())
            cleaned[domain] = tuple(
                dict.fromkeys(
                    token for token in tokens
                    if token in DOMAIN_TOKENS[domain]
                )
            )
        self.enabled = cleaned

    @property
    def content_hash(self) -> str:
        # Identity covers the configuration only; the audit trail and
        # timestamps never enter it.
        state = {
            "schema_version": SCHEMA_VERSION,
            "enabled": {
                domain: sorted(tokens)
                for domain, tokens in self.enabled.items()
            },
        }
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state_id": self.state_id,
            "enabled": {
                domain: list(tokens)
                for domain, tokens in self.enabled.items()
            },
            "generation": self.generation,
            "parent_hash": self.parent_hash,
            "content_hash": self.content_hash,
            "edits_applied": [edit.to_dict() for edit in self.edits_applied],
            "audit": [dict(row) for row in self.audit],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StrategyState":
        if not isinstance(value, Mapping):
            raise ValueError("strategy state must contain an object")
        if str(value.get("schema_version") or "") != SCHEMA_VERSION:
            raise ValueError(
                f"unknown strategy state schema_version: "
                f"{value.get('schema_version')!r}"
            )
        return cls(
            state_id=str(value.get("state_id") or ""),
            enabled={
                str(domain): tuple(
                    str(v) for v in tokens
                )
                for domain, tokens in dict(value.get("enabled") or {}).items()
            },
            generation=int(value.get("generation") or 0),
            parent_hash=value.get("parent_hash"),
            edits_applied=[
                StrategyEdit.from_dict(row)
                for row in (value.get("edits_applied") or ())
                if isinstance(row, Mapping)
            ],
            audit=[
                dict(row)
                for row in (value.get("audit") or ())
                if isinstance(row, Mapping)
            ],
        )

    def clone(self) -> "StrategyState":
        return StrategyState.from_dict(self.to_dict())

    def enabled_in(self, domain: str) -> tuple[str, ...]:
        """Read the enabled tokens for one domain (empty when none)."""
        return self.enabled.get(domain, ())


# ============================================================
# Edit validation and application (immutable lineage)
# ============================================================

def validate_strategy_edit(edit: StrategyEdit, state: StrategyState) -> tuple[bool, str]:
    """Validate one strategy edit against the closed vocabularies. Fail-closed."""
    if edit.operation not in STRATEGY_EDIT_OPERATIONS:
        return False, f"invalid_operation:{edit.operation}"
    if edit.domain not in STRATEGY_DOMAINS:
        return False, f"unknown_domain:{edit.domain}"
    if edit.target not in DOMAIN_TOKENS[edit.domain]:
        return False, f"target_not_in_domain:{edit.domain}:{edit.target}"
    active = state.enabled_in(edit.domain)
    if edit.operation == OP_ENABLE and edit.target in active:
        return False, "duplicate_strategy_token"
    if edit.operation == OP_DISABLE and edit.target not in active:
        return False, "strategy_token_absent"
    return True, ""


def apply_strategy_edit(state: StrategyState, edit: StrategyEdit) -> StrategyState:
    """Apply one validated strategy edit, returning a NEW state (immutable)."""
    valid, reason = validate_strategy_edit(edit, state)
    if not valid:
        raise ValueError(f"Invalid strategy edit: {reason}")

    child = state.clone()
    child.generation = state.generation + 1
    child.parent_hash = state.content_hash

    if edit.operation == OP_ENABLE:
        child.enabled = {
            domain: (
                tuple(dict.fromkeys([*tokens, edit.target]))
                if domain == edit.domain else tokens
            )
            for domain, tokens in child.enabled.items()
        }
    else:  # OP_DISABLE
        child.enabled = {
            domain: (
                tuple(token for token in tokens if token != edit.target)
                if domain == edit.domain else tokens
            )
            for domain, tokens in child.enabled.items()
        }

    child.edits_applied = state.edits_applied + [edit]
    child.audit = state.audit + [{
        "edit_id": edit.edit_id,
        "operation": edit.operation,
        "domain": edit.domain,
        "target": edit.target,
        "generation": child.generation,
        "before_hash": state.content_hash,
        "after_hash": child.content_hash,
    }]
    # Deterministic instance label derived from the new content, so a replay
    # of the same lineage reproduces the same state identity.
    child.state_id = f"strategy_g{child.generation}_{child.content_hash[:8]}"
    return child


def create_baseline_strategy() -> StrategyState:
    """The conservative baseline strategy state: no domain has any token enabled."""
    return StrategyState(state_id="strategy_baseline_v1")
