"""SEHD v6 — versioned baseline registry for the L2 adjudication vocabulary.

Single source of truth for the L2 precondition predicates, court-local
action operators/instances, action parameter tables, and the reserved
engine-level actions. Every consumer — the deterministic evaluator, the
reflection planner/prompt, the policy-genome validator, and the failure
classifier — reads the vocabulary from this module's ``L2_REGISTRY`` so the
prompt vocabulary and the runtime semantics can never drift apart.

P3A migrates the frozen constant set into this versioned registry without
changing behavior: the baseline entries are exactly the pre-existing
``v6_contracts`` constants, evaluation semantics are unchanged, and the
registry itself is immutable. Online registration of new entries (the
audited ``RegistryEdit`` generation path) belongs to P3B and is deliberately
not wired here — only the audit data model is defined so later stages can
record registration/disable/remove decisions without reshaping this module.

The registry never enters ``PolicyGenome.content_hash`` (P3A scope): genome
identity stays a pure function of the policy items, keeping lineage and
cache keys stable across this refactor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

# ============================================================
# Adjudication facts: the addressable observation surface
# ============================================================
# A flat, frozen snapshot of everything a policy precondition or a failure
# classification may look at. Built once from the proof-court result plus
# the engine-level context (boundary path) by
# ``v6_contracts.build_adjudication_facts``, so neither the evaluator nor
# the classifier ever reaches into raw provider payloads. The type lives in
# this module (the vocabulary's single authority) so the registry and the
# shared contracts import it from one root without a circular dependency.

# Policy execution stages. The current runtime only implements the
# post-ledger surface (policies act after the occurrence ledger is fixed,
# on coverage and the terminal edge only); the constant makes the stage
# explicit on every fact snapshot so a future pre-ledger surface cannot
# silently share the same execution path.
POLICY_STAGE_POST_LEDGER = "post_ledger"


@dataclass(frozen=True)
class AdjudicationFacts:
    prosecution_coverage: str = ""
    defense_coverage: str = ""
    valid_error_certificate_count: int = 0
    valid_defense_certificate_count: int = 0
    surviving_error_count: int = 0
    defeated_error_count: int = 0
    quarantined_error_count: int = 0
    quarantined_defense_count: int = 0
    consensus_gate_applied: bool = False
    consensus_gate_blocked_count: int = 0
    required_candidate_count: int = 0
    fully_quarantined: bool = False
    boundary_path_state: str = ""
    boundary_authority_valid: bool = False
    terminal_state: str = ""
    policy_stage: str = POLICY_STAGE_POST_LEDGER

    @property
    def total_certificate_count(self) -> int:
        return (
            self.valid_error_certificate_count
            + self.valid_defense_certificate_count
        )


# ============================================================
# Schema / compiler version anchors
# ============================================================
# ``schema_version`` identifies the registry payload shape;
# ``facts_schema_version`` identifies the AdjudicationFacts snapshot the
# predicates read; ``compiler_version`` identifies the deterministic
# predicate/action semantics. Bumping any of them is a deliberate,
# audited change — never an implicit side effect of editing an entry.

REGISTRY_SCHEMA_VERSION = "sehd.l2_vocabulary_registry.v1"
FACTS_SCHEMA_VERSION = "sehd.adjudication_facts.v1"
COMPILER_VERSION = "sehd.l2_baseline_compiler.v1"
REGISTRY_VERSION = "l2_baseline_v1"

# Entry status vocabulary (closed): baseline entries ship with the runtime
# and cannot be removed; registered/disabled are reserved for the P3B online
# path, which this stage does not wire.
STATUS_BASELINE = "baseline"
STATUS_REGISTERED = "registered"
STATUS_DISABLED = "disabled"
ENTRY_STATUSES = frozenset({STATUS_BASELINE, STATUS_REGISTERED, STATUS_DISABLED})


# ============================================================
# Predicate entry
# ============================================================

@dataclass(frozen=True)
class PredicateEntry:
    """One closed-vocabulary L2 precondition predicate.

    ``evaluate`` is the deterministic runtime semantics (kept in memory
    only; never serialized). ``canonical_dsl`` / ``facts_whitelist`` /
    ``compiled_digest`` describe the predicate so a later DSL compiler
    (P3B) can reproduce the same semantics from an auditable source form.
    """
    name: str
    canonical_ast: Mapping[str, Any]
    canonical_dsl: str
    facts_whitelist: tuple[str, ...]
    compiled_digest: str
    registered_generation: int
    status: str
    evaluate: Callable[[AdjudicationFacts], bool] = field(
        repr=False, compare=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "canonical_ast": json.loads(json.dumps(self.canonical_ast)),
            "canonical_dsl": self.canonical_dsl,
            "facts_whitelist": list(self.facts_whitelist),
            "compiled_digest": self.compiled_digest,
            "registered_generation": self.registered_generation,
            "status": self.status,
        }


# ============================================================
# Action operator and instance
# ============================================================

@dataclass(frozen=True)
class ActionOperator:
    """A fixed runtime operator the evaluator can execute.

    ``policy_stage`` marks the adjudication edge the operator acts on;
    ``runtime_owner`` names the deterministic component that owns its
    semantics. Engine-level operators are reserved (owned by a future
    engine) and are not proposable or executable at this stage.
    """
    operator: str
    params_schema: tuple[str, ...]
    policy_stage: str
    runtime_owner: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "params_schema": list(self.params_schema),
            "policy_stage": self.policy_stage,
            "runtime_owner": self.runtime_owner,
        }


@dataclass(frozen=True)
class ActionInstance:
    """A proposable action: an operator bound to its parameter table."""
    name: str
    operator: str
    params: tuple[str, ...]
    registered_generation: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operator": self.operator,
            "params": list(self.params),
            "registered_generation": self.registered_generation,
            "status": self.status,
        }


# ============================================================
# RegistryEdit audit model (data shape only; online path is P3B)
# ============================================================

@dataclass(frozen=True)
class RegistryEdit:
    """Audited record of one registry mutation (register/disable/remove).

    Only the data model exists at this stage; no code path produces or
    applies a RegistryEdit yet. ``before_hash`` / ``after_hash`` chain the
    registry's canonical hash so a sequence of edits is replay-auditable.
    """
    edit_id: str
    operation: str
    target: str
    before_hash: str
    after_hash: str
    reason_codes: tuple[str, ...]
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "operation": self.operation,
            "target": self.target,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "reason_codes": list(self.reason_codes),
            "sequence": self.sequence,
        }


# ============================================================
# Registry
# ============================================================

def _freeze(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping))


class VocabularyRegistry:
    """Immutable, versioned authority over the L2 vocabulary.

    All lookups are closed: an unknown or disabled name is simply absent
    from the active views, so every consumer fails closed on it.
    """

    def __init__(
        self,
        *,
        registry_version: str,
        predicates: Mapping[str, PredicateEntry],
        action_operators: Mapping[str, ActionOperator],
        action_instances: Mapping[str, ActionInstance],
        audit: tuple[RegistryEdit, ...] = (),
    ) -> None:
        self._registry_version = registry_version
        self._predicates = _freeze(predicates)
        self._action_operators = _freeze(action_operators)
        self._action_instances = _freeze(action_instances)
        self._audit = tuple(audit)

    # --- version anchors ---
    @property
    def registry_version(self) -> str:
        return self._registry_version

    @property
    def schema_version(self) -> str:
        return REGISTRY_SCHEMA_VERSION

    @property
    def facts_schema_version(self) -> str:
        return FACTS_SCHEMA_VERSION

    @property
    def compiler_version(self) -> str:
        return COMPILER_VERSION

    @property
    def audit(self) -> tuple[RegistryEdit, ...]:
        return self._audit

    # --- active views (baseline + registered; disabled excluded) ---
    def _active(self, entry: Any) -> bool:
        return entry.status != STATUS_DISABLED

    def predicate_entry(self, name: str) -> PredicateEntry | None:
        entry = self._predicates.get(name)
        return entry if (entry is not None and self._active(entry)) else None

    def predicate_names(self) -> tuple[str, ...]:
        return tuple(
            name for name, entry in self._predicates.items()
            if self._active(entry)
        )

    def predicate_fn(
        self, name: str,
    ) -> Callable[[AdjudicationFacts], bool] | None:
        entry = self.predicate_entry(name)
        return entry.evaluate if entry is not None else None

    def action_names(self) -> frozenset[str]:
        """Court-local proposable AND executable actions."""
        return frozenset(
            name for name, entry in self._action_instances.items()
            if self._active(entry)
        )

    def engine_level_action_names(self) -> frozenset[str]:
        """Reserved engine-level operators: not proposable, not executable."""
        return frozenset(
            name for name, operator in self._action_operators.items()
            if operator.runtime_owner == "engine"
        )

    def action_params(self) -> Mapping[str, tuple[str, ...]]:
        return _freeze({
            name: entry.params
            for name, entry in self._action_instances.items()
            if self._active(entry)
        })

    # --- canonical serialization / hash ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "facts_schema_version": self.facts_schema_version,
            "compiler_version": self.compiler_version,
            "registry_version": self.registry_version,
            "predicates": [
                self._predicates[name].to_dict()
                for name in sorted(self._predicates)
            ],
            "action_operators": [
                self._action_operators[name].to_dict()
                for name in sorted(self._action_operators)
            ],
            "action_instances": [
                self._action_instances[name].to_dict()
                for name in sorted(self._action_instances)
            ],
            "audit": [edit.to_dict() for edit in self._audit],
        }

    @property
    def content_hash(self) -> str:
        # Canonical over the serialized vocabulary only: stable across
        # processes and independent of dict insertion order. Runtime
        # statistics, usage counts and timestamps never enter it.
        text = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, VocabularyRegistry):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash(self.content_hash)


# ============================================================
# Baseline predicate semantics (deterministic, pure)
# ============================================================
# These are exactly the pre-registry v6_contracts predicate functions,
# relocated here so the registry owns both the names and the semantics.

def _pred_consensus_gate_blocked_nonempty(facts: AdjudicationFacts) -> bool:
    return facts.consensus_gate_applied and facts.consensus_gate_blocked_count > 0


def _hollow_coverage(facts: AdjudicationFacts, coverage: str, count: int) -> bool:
    return coverage == "complete" and count == 0


def _pred_defense_coverage_complete_and_zero_certificates(
    facts: AdjudicationFacts,
) -> bool:
    return _hollow_coverage(
        facts, facts.defense_coverage, facts.valid_defense_certificate_count
    )


def _pred_prosecution_coverage_complete_and_zero_certificates(
    facts: AdjudicationFacts,
) -> bool:
    return _hollow_coverage(
        facts, facts.prosecution_coverage, facts.valid_error_certificate_count
    )


def _pred_boundary_state_conflicts_with_terminal(facts: AdjudicationFacts) -> bool:
    return (
        facts.boundary_path_state == "verified_clean"
        and facts.terminal_state == "unresolved"
    )


def _pred_surviving_errors_nonempty_and_verdict_clean(
    facts: AdjudicationFacts,
) -> bool:
    return facts.surviving_error_count > 0 and facts.terminal_state == "verified_clean"


def _pred_all_candidates_quarantined(facts: AdjudicationFacts) -> bool:
    return facts.fully_quarantined


def _predicate(
    name: str,
    dsl: str,
    whitelist: tuple[str, ...],
    evaluate: Callable[[AdjudicationFacts], bool],
) -> PredicateEntry:
    return PredicateEntry(
        name=name,
        canonical_ast={"dsl": dsl},
        canonical_dsl=dsl,
        facts_whitelist=whitelist,
        registered_generation=0,
        status=STATUS_BASELINE,
        evaluate=evaluate,
        compiled_digest=hashlib.sha256(
            f"{COMPILER_VERSION}:{name}:{dsl}".encode("utf-8")
        ).hexdigest(),
    )


def _build_baseline_registry() -> VocabularyRegistry:
    predicates = [
        _predicate(
            "consensus_gate_blocked_nonempty",
            "consensus_gate_applied and consensus_gate_blocked_count > 0",
            ("consensus_gate_applied", "consensus_gate_blocked_count"),
            _pred_consensus_gate_blocked_nonempty,
        ),
        _predicate(
            "defense_coverage_complete_and_zero_certificates",
            'defense_coverage == "complete" and valid_defense_certificate_count == 0',
            ("defense_coverage", "valid_defense_certificate_count"),
            _pred_defense_coverage_complete_and_zero_certificates,
        ),
        _predicate(
            "prosecution_coverage_complete_and_zero_certificates",
            'prosecution_coverage == "complete" and valid_error_certificate_count == 0',
            ("prosecution_coverage", "valid_error_certificate_count"),
            _pred_prosecution_coverage_complete_and_zero_certificates,
        ),
        _predicate(
            "boundary_state_conflicts_with_terminal",
            'boundary_path_state == "verified_clean" and terminal_state == "unresolved"',
            ("boundary_path_state", "terminal_state"),
            _pred_boundary_state_conflicts_with_terminal,
        ),
        _predicate(
            "surviving_errors_nonempty_and_verdict_clean",
            'surviving_error_count > 0 and terminal_state == "verified_clean"',
            ("surviving_error_count", "terminal_state"),
            _pred_surviving_errors_nonempty_and_verdict_clean,
        ),
        _predicate(
            "all_candidates_quarantined",
            "fully_quarantined",
            ("fully_quarantined",),
            _pred_all_candidates_quarantined,
        ),
    ]

    operators = [
        ActionOperator(
            operator="downgrade_coverage",
            params_schema=("side", "to_state"),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        ActionOperator(
            operator="invalidate_coverage_claim",
            params_schema=("side",),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        ActionOperator(
            operator="emit_telemetry_only",
            params_schema=(),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        ActionOperator(
            operator="consume_verified_clean_boundary",
            params_schema=(),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        # Reserved for a future engine that can re-execute providers; owned
        # by the engine, not the court, so they are never proposable or
        # executable until the runtime implements them.
        ActionOperator(
            operator="reclassify_candidate_state",
            params_schema=(),
            policy_stage="pre_ledger",
            runtime_owner="engine",
        ),
        ActionOperator(
            operator="require_route_replay",
            params_schema=(),
            policy_stage="pre_ledger",
            runtime_owner="engine",
        ),
    ]

    def _instance(operator: ActionOperator) -> ActionInstance:
        return ActionInstance(
            name=operator.operator,
            operator=operator.operator,
            params=operator.params_schema,
            registered_generation=0,
            status=STATUS_BASELINE,
        )

    instances = [
        _instance(operator)
        for operator in operators
        if operator.runtime_owner == "court"
    ]

    return VocabularyRegistry(
        registry_version=REGISTRY_VERSION,
        predicates={entry.name: entry for entry in predicates},
        action_operators={op.operator: op for op in operators},
        action_instances={entry.name: entry for entry in instances},
    )


# The single authoritative registry instance for the L2 vocabulary.
L2_REGISTRY = _build_baseline_registry()
