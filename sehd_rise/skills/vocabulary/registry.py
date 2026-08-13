"""Versioned baseline registry for the L2 adjudication vocabulary.

Single source of truth for the L2 precondition predicates, court-local
action operators/instances, action parameter tables, and the reserved
engine-level actions. Every consumer — the deterministic evaluator, the
reflection planner/prompt, the policy-genome validator, and the failure
classifier — reads the vocabulary from this module's ``L2_REGISTRY`` so the
prompt vocabulary and the runtime semantics can never drift apart.

The vocabulary is online-evolvable under audit: a minimal whitelist DSL
compiles to pure predicates, ``apply_registry_edit`` applies audited
``RegistryEdit`` operations (register/disable/enable) producing an immutable
lineage with a verifiable hash chain, and newly registered entries are
``pending`` (shadow-only) until explicitly promoted. The registry is the
single authority persisted with the ``PolicyGenome`` and participates in its
``content_hash`` (items + active registry state), so a genome's identity
binds the exact vocabulary it was built on; usage counts and timestamps
never enter the hash.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping

from sehd_rise.skills.capabilities import SKILL_CAPABILITY_REGISTRY

# ============================================================
# Adjudication facts: the addressable observation surface
# ============================================================
# A flat, frozen snapshot of the full surface a policy precondition or a
# failure classification may read. Built once from the proof-court result
# plus the engine-level context (boundary path) by the detection policy
# module, so neither the evaluator nor the classifier ever reaches into raw
# provider payloads. The type lives in this module (the vocabulary's single
# authority) so the registry and the shared contracts import it from one
# root without a circular dependency.

# Policy execution stages. ``post_ledger`` policies act after the occurrence
# ledger is fixed, on coverage and the terminal edge only. ``pre_ledger``
# policies act between the consensus gate and the occurrence snapshot, so a
# restore flows into the ledger instead of repairing it afterwards. The stage
# is explicit on every fact snapshot and every operator so the two surfaces
# can never share an execution path.
POLICY_STAGE_POST_LEDGER = "post_ledger"
POLICY_STAGE_PRE_LEDGER = "pre_ledger"
POLICY_STAGES = frozenset({POLICY_STAGE_POST_LEDGER, POLICY_STAGE_PRE_LEDGER})


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
    # Appeal and localization states; the value domains must exactly match
    # what the proof engine emits. appeal_status: "not_required" | "complete"
    # | "provider_error" | "incomplete_initial_error_preserved" |
    # "incomplete_fail_closed_unresolved".
    appeal_status: str = ""
    appeal_triggered: bool = False
    # localization_status: "complete" | "not_applicable" | "failed_temporal_fallback"
    localization_status: str = ""
    responsible_step_available: bool = False
    # Quarantine reason distribution (closed-vocabulary counts).
    quarantine_reason_schema_mismatch: int = 0
    quarantine_reason_evidence_missing: int = 0
    quarantine_reason_causal_broken: int = 0
    quarantine_reason_other: int = 0

    @property
    def total_certificate_count(self) -> int:
        return (
            self.valid_error_certificate_count
            + self.valid_defense_certificate_count
        )

    @property
    def total_quarantine_count(self) -> int:
        return (
            self.quarantined_error_count
            + self.quarantined_defense_count
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

# Entry status vocabulary (closed). Only ``baseline`` and ``registered``
# (working) entries enter the active evaluator; a freshly registered entry is
# ``pending`` (shadow-only, not executable) until it is explicitly promoted,
# and a ``disabled`` entry is excluded from every active view. Baseline
# entries ship with the runtime and cannot be disabled or removed.
STATUS_BASELINE = "baseline"
STATUS_PENDING = "pending"
STATUS_REGISTERED = "registered"
STATUS_DISABLED = "disabled"
ENTRY_STATUSES = frozenset({
    STATUS_BASELINE, STATUS_PENDING, STATUS_REGISTERED, STATUS_DISABLED,
})
# Statuses that make an entry resolvable by the runtime evaluator/planner.
_ACTIVE_STATUSES = frozenset({STATUS_BASELINE, STATUS_REGISTERED})


# ============================================================
# Predicate entry
# ============================================================

@dataclass(frozen=True)
class PredicateEntry:
    """One closed-vocabulary L2 precondition predicate.

    ``evaluate`` is the deterministic runtime semantics (kept in memory
    only; never serialized). ``canonical_dsl`` / ``facts_whitelist`` /
    ``compiled_digest`` describe the predicate so the same DSL compiler can
    reproduce the semantics later from an auditable source form.
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
# RegistryEdit audit model
# ============================================================

@dataclass(frozen=True)
class RegistryEdit:
    """Audited record of one registry mutation (register/disable/enable).

    ``before_hash`` / ``after_hash`` chain the registry's canonical hash so
    a sequence of edits is replay-auditable.
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

    # --- active views (baseline + registered/working; pending and disabled
    # are excluded so a fresh or retired entry is never executable) ---
    def _active(self, entry: Any) -> bool:
        return entry.status in _ACTIVE_STATUSES

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
        """Court-local executable actions with repair/fitness contracts.

        Proposability is inherited from the instance's bound operator: the
        operator's baseline capability carries the repair/fitness contract,
        so a promoted ``<operator>_instance`` alias is proposable exactly
        when its operator is. Runtime-only telemetry operators have no
        capability and therefore never become proposable.
        """
        return frozenset(
            name for name, entry in self._action_instances.items()
            if self._active(entry)
            and SKILL_CAPABILITY_REGISTRY.contains(entry.operator)
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
            and SKILL_CAPABILITY_REGISTRY.contains(entry.operator)
        })

    # --- raw (status-unfiltered) views: serialization and hashing cover the
    # full registry state, including pending and disabled entries, so the
    # lineage hash captures every registration decision ---
    def all_predicate_names(self) -> tuple[str, ...]:
        return tuple(self._predicates)

    def all_action_instance_names(self) -> tuple[str, ...]:
        return tuple(self._action_instances)

    def all_operator_names(self) -> tuple[str, ...]:
        return tuple(self._action_operators)

    def raw_predicate(self, name: str) -> PredicateEntry | None:
        return self._predicates.get(name)

    def raw_operator(self, name: str) -> ActionOperator | None:
        return self._action_operators.get(name)

    def raw_action_instance(self, name: str) -> ActionInstance | None:
        return self._action_instances.get(name)

    def status_of(self, name: str) -> str:
        entry = self._predicates.get(name) or self._action_instances.get(name)
        return entry.status if entry is not None else ""

    def operator_of(self, action: str) -> str:
        """Resolve an action name to its executable operator.

        A baseline action instance is its own operator; a registered
        instance is an alias bound to an existing operator.  The evaluator
        dispatches on the operator, so a promoted alias executes the same
        semantics as the operator it was bound to and a name with no
        instance resolves to itself (fail-closed at the vocabulary check).
        """
        instance = self._action_instances.get(action)
        return instance.operator if instance is not None else action

    def policy_stage_of(self, action: str) -> str:
        """Resolve the execution stage of an action from the registry.

        Stage routing is owned by the registry alone: a caller splitting a
        genome across the pre-/post-ledger surfaces asks here instead of
        re-deriving stage from an action-name list of its own. An
        unregistered action resolves to the post-ledger surface, where the
        DSL whitelist rejects it fail-closed; it is never silently granted
        the pre-ledger surface, which runs before the occurrence snapshot.
        """
        operator = self._action_operators.get(action)
        if operator is None:
            instance = self._action_instances.get(action)
            if instance is not None:
                operator = self._action_operators.get(instance.operator)
        if operator is None:
            return POLICY_STAGE_POST_LEDGER
        return operator.policy_stage or POLICY_STAGE_POST_LEDGER

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
# Each baseline predicate is defined by its canonical DSL source and
# compiled by the same deterministic DSL compiler used for online
# registration, so there is exactly one semantics authority (no parallel
# hand-written implementation that could drift from the DSL form).

def _predicate(name: str, dsl: str) -> PredicateEntry:
    return PredicateEntry(
        name=name,
        canonical_ast={"dsl": dsl},
        canonical_dsl=dsl,
        facts_whitelist=predicate_facts_whitelist(dsl),
        registered_generation=0,
        status=STATUS_BASELINE,
        evaluate=compile_predicate_dsl(name, dsl),
        compiled_digest=hashlib.sha256(
            f"{COMPILER_VERSION}:{name}:{dsl}".encode("utf-8")
        ).hexdigest(),
    )


def _build_baseline_registry() -> VocabularyRegistry:
    predicates = [
        _predicate(
            "consensus_gate_blocked_nonempty",
            "consensus_gate_applied and consensus_gate_blocked_count > 0",
        ),
        _predicate(
            "defense_coverage_complete_and_zero_certificates",
            'defense_coverage == "complete" and valid_defense_certificate_count == 0',
        ),
        _predicate(
            "prosecution_coverage_complete_and_zero_certificates",
            'prosecution_coverage == "complete" and valid_error_certificate_count == 0',
        ),
        _predicate(
            "boundary_state_conflicts_with_terminal",
            'boundary_path_state == "verified_clean" and terminal_state == "unresolved"',
        ),
        _predicate(
            "surviving_errors_nonempty_and_verdict_clean",
            'surviving_error_count > 0 and terminal_state == "verified_clean"',
        ),
        _predicate(
            "all_candidates_quarantined",
            "fully_quarantined",
        ),
        # Appeal predicates; value domains match the proof engine's actual
        # output ("incomplete_fail_closed_unresolved" = appeal triggered but
        # unfinished with the terminal state unresolved; "provider_error" =
        # the appeal call failed at the provider).
        _predicate(
            "appeal_pending_and_terminal_unresolved",
            'appeal_triggered and appeal_status == "incomplete_fail_closed_unresolved" and terminal_state == "unresolved"',
        ),
        _predicate(
            "appeal_failed_with_surviving_errors",
            'appeal_triggered and appeal_status == "provider_error" and surviving_error_count > 0',
        ),
        # Localization predicates; the engine emits "failed_temporal_fallback".
        _predicate(
            "localization_failed_with_temporal_fallback",
            'localization_status == "failed_temporal_fallback"',
        ),
        _predicate(
            "responsible_step_missing_and_verdict_error",
            'not responsible_step_available and terminal_state == "verified_error"',
        ),
        # Quarantine-reason dominance predicates.
        _predicate(
            "quarantine_schema_mismatch_dominant",
            "quarantine_reason_schema_mismatch > quarantine_reason_evidence_missing and quarantine_reason_schema_mismatch > quarantine_reason_causal_broken and quarantine_reason_schema_mismatch > 0",
        ),
        _predicate(
            "quarantine_evidence_missing_dominant",
            "quarantine_reason_evidence_missing > quarantine_reason_schema_mismatch and quarantine_reason_evidence_missing > quarantine_reason_causal_broken and quarantine_reason_evidence_missing > 0",
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
        ActionOperator(
            operator="require_additional_verification",
            params_schema=("verification_scope",),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        ActionOperator(
            operator="escalate_to_appeal",
            params_schema=(),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        ActionOperator(
            operator="mark_candidate_ambiguous",
            params_schema=("ambiguity_reason",),
            policy_stage=POLICY_STAGE_POST_LEDGER,
            runtime_owner="court",
        ),
        # Reserved for a future engine that can re-execute providers; owned
        # by the engine, not the court, so they are never proposable or
        # executable until the runtime implements them.
        ActionOperator(
            operator="reclassify_candidate_state",
            params_schema=(),
            policy_stage=POLICY_STAGE_PRE_LEDGER,
            runtime_owner="engine",
        ),
        ActionOperator(
            operator="require_route_replay",
            params_schema=(),
            policy_stage=POLICY_STAGE_PRE_LEDGER,
            runtime_owner="engine",
        ),
        # The audited pre-ledger consensus-restore operator. It is a
        # court-local pre-ledger operator (not proposable as a post-ledger
        # action); first registration is conditional/pending until validated.
        ActionOperator(
            operator="restore_audited_consensus",
            params_schema=("certificate_id",),
            policy_stage=POLICY_STAGE_PRE_LEDGER,
            runtime_owner="court",
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

    # Only post-ledger court operators become proposable baseline action
    # instances. A pre-ledger court operator (restore_audited_consensus) is
    # registered for the pre-ledger evaluator but is never a post-ledger
    # proposable action, so the two stages can never share an execution path.
    instances = [
        _instance(operator)
        for operator in operators
        if operator.runtime_owner == "court"
        and operator.policy_stage == POLICY_STAGE_POST_LEDGER
    ]

    return VocabularyRegistry(
        registry_version=REGISTRY_VERSION,
        predicates={entry.name: entry for entry in predicates},
        action_operators={op.operator: op for op in operators},
        action_instances={entry.name: entry for entry in instances},
    )


# ============================================================
# Predicate DSL compiler
# ============================================================
# The minimal DSL compiles a restricted boolean/comparison expression over
# the AdjudicationFacts whitelist into a pure ``(facts) -> bool`` closure.
# Compilation is fail-closed: any construct outside the whitelist (function
# calls, attribute access, subscripts, dynamic names, comprehensions,
# regex, imports) is rejected at parse/validation time and never produces a
# predicate. Only the canonical DSL source and its digest are persisted;
# loading recompiles deterministically.

_FACTS_FIELDS = frozenset(AdjudicationFacts.__dataclass_fields__)

# Field-type map for compile-time type checking: every fact is one of
# str / int / bool. A comparison whose operand types can never match the
# field type is rejected at compile time, so the evaluator never hits a
# runtime TypeError on a policy predicate.
_FIELD_TYPES: dict[str, type] = {
    "prosecution_coverage": str,
    "defense_coverage": str,
    "valid_error_certificate_count": int,
    "valid_defense_certificate_count": int,
    "surviving_error_count": int,
    "defeated_error_count": int,
    "quarantined_error_count": int,
    "quarantined_defense_count": int,
    "consensus_gate_applied": bool,
    "consensus_gate_blocked_count": int,
    "required_candidate_count": int,
    "fully_quarantined": bool,
    "boundary_path_state": str,
    "boundary_authority_valid": bool,
    "terminal_state": str,
    "policy_stage": str,
    "appeal_status": str,
    "appeal_triggered": bool,
    "localization_status": str,
    "responsible_step_available": bool,
    "quarantine_reason_schema_mismatch": int,
    "quarantine_reason_evidence_missing": int,
    "quarantine_reason_causal_broken": int,
    "quarantine_reason_other": int,
}

# Closed value vocabulary a predicate may compare against (string literals
# are confined to the coverage/terminal/boundary states the facts can hold).
# These must exactly match the values the proof engine emits at runtime;
# the proof engine is the single authority for its own status strings.
_DSL_ALLOWED_STRINGS = frozenset({
    "complete", "incomplete", "unresolved", "none",
    "verified_clean", "verified_error", "cleared",
    # appeal states emitted by the proof engine
    "not_required", "provider_error",
    "incomplete_initial_error_preserved", "incomplete_fail_closed_unresolved",
    # localization states emitted by the proof engine
    "not_applicable", "failed_temporal_fallback",
    # quarantine reason categories from court audit failed_checks
    "schema_mismatch", "evidence_missing", "causal_broken",
    # policy-stage values the registry's own operators emit
    "post_ledger", "pre_ledger",
})


class RegistryDslError(ValueError):
    """A DSL source failed whitelist/syntax validation (fail-closed)."""


def _literal_type(node: ast.AST) -> type | None:
    """Resolve the Python type of a literal/unsigned-number node, else None."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return bool
        if isinstance(node.value, int):
            return int
        if isinstance(node.value, str):
            return str
        return None
    if isinstance(node, ast.List):
        element_types = {_literal_type(element) for element in node.elts}
        element_types.discard(None)
        return element_types.pop() if len(element_types) == 1 else None
    return None


def _validate_dsl_node(node: ast.AST, fields: set[str]) -> None:
    if isinstance(node, ast.Expression):
        _validate_dsl_node(node.body, fields)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise RegistryDslError("only and/or boolean operators are allowed")
        for value in node.values:
            _validate_dsl_node(value, fields)
        return
    if isinstance(node, ast.UnaryOp):
        # ``not`` for boolean logic; ``+``/``-`` only as an unsigned numeric
        # literal sign so counts can be compared against e.g. ``>= 0``.
        if isinstance(node.op, ast.Not):
            _validate_dsl_node(node.operand, fields)
            return
        if isinstance(node.op, (ast.UAdd, ast.USub)) and isinstance(
            node.operand, ast.Constant
        ) and isinstance(node.operand.value, int) and not isinstance(
            node.operand.value, bool
        ):
            return
        raise RegistryDslError(
            "only `not` and unsigned numeric signs are allowed unary operators"
        )
    if isinstance(node, ast.Compare):
        _validate_compare_node(node, fields)
        return
    if isinstance(node, ast.Name):
        if node.id not in _FACTS_FIELDS:
            raise RegistryDslError(
                f"field outside AdjudicationFacts whitelist: {node.id}"
            )
        fields.add(node.id)
        return
    if isinstance(node, ast.Constant):
        _validate_constant(node)
        return
    if isinstance(node, ast.List):
        for element in node.elts:
            if not isinstance(element, ast.Constant):
                raise RegistryDslError("`in` lists may only contain literals")
            _validate_constant(element)
        return
    raise RegistryDslError(
        f"DSL construct not allowed: {type(node).__name__}"
    )


def _validate_constant(node: ast.Constant) -> None:
    value = node.value
    if isinstance(value, bool) or value is None:
        raise RegistryDslError("boolean/None literals are not allowed")
    if isinstance(value, str):
        if value not in _DSL_ALLOWED_STRINGS:
            raise RegistryDslError(
                f"string literal outside closed value vocabulary: {value!r}"
            )
        return
    if isinstance(value, int):
        return
    raise RegistryDslError(
        f"literal type not allowed: {type(value).__name__}"
    )


def _operand_field_type(node: ast.AST) -> type | None:
    """If the operand is a single whitelisted fact field, return its type."""
    if isinstance(node, ast.Name):
        return _FIELD_TYPES.get(node.id)
    if isinstance(node, (ast.Constant, ast.List)):
        return _literal_type(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return int
    return None


def _validate_compare_node(node: ast.Compare, fields: set[str]) -> None:
    for operator in node.ops:
        if not isinstance(operator, (
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In,
        )):
            raise RegistryDslError(
                f"comparison operator not allowed: {type(operator).__name__}"
            )
    operands = [node.left, *node.comparators]
    for operand in operands:
        _validate_dsl_node(operand, fields)
    # Compile-time type check: a comparison whose operand types cannot match
    # (e.g. a string field ordered against a numeric literal) would raise
    # TypeError at evaluation time, so it is rejected here. Equality against
    # a mismatched literal is legal (it is simply always False); ordering is
    # not.
    numeric_ops = any(
        isinstance(operator, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
        for operator in node.ops
    )
    if not numeric_ops:
        return
    operand_types = {
        _operand_field_type(operand)
        for operand in operands
    }
    operand_types.discard(None)
    if bool in operand_types:
        raise RegistryDslError(
            "ordering comparison against a boolean field is not meaningful"
        )
    if len(operand_types) > 1:
        raise RegistryDslError(
            "ordering comparison mixes incompatible operand types: "
            + ", ".join(sorted(t.__name__ for t in operand_types))
        )


def compile_predicate_dsl(
    name: str, dsl: str,
) -> Callable[[AdjudicationFacts], bool]:
    """Compile one DSL source into a pure predicate over AdjudicationFacts.

    Returns the deterministic closure; raises ``RegistryDslError`` on any
    whitelist/syntax violation so an invalid source is never registered.
    """
    tree = ast.parse(dsl, mode="eval")
    fields: set[str] = set()
    _validate_dsl_node(tree, fields)
    if not fields:
        raise RegistryDslError("predicate must reference at least one fact field")
    code = compile(tree, filename=f"<registry_predicate:{name}>", mode="eval")

    def evaluate(facts: AdjudicationFacts) -> bool:
        namespace = {
            field_name: getattr(facts, field_name) for field_name in fields
        }
        return bool(eval(code, {"__builtins__": {}}, namespace))

    return evaluate


def predicate_facts_whitelist(dsl: str) -> tuple[str, ...]:
    """Return the sorted fact-field whitelist a DSL source references."""
    tree = ast.parse(dsl, mode="eval")
    fields: set[str] = set()
    _validate_dsl_node(tree, fields)
    return tuple(sorted(fields))


# ============================================================
# Entry deserialization (loading recompiles DSL deterministically)
# ============================================================

def _predicate_from_dict(data: Mapping[str, Any]) -> PredicateEntry:
    name = str(data.get("name") or "")
    dsl = str(data.get("canonical_dsl") or "")
    status = str(data.get("status") or "")
    if status not in ENTRY_STATUSES:
        raise ValueError(f"unknown predicate status: {status!r}")
    # Loading recompiles the predicate from its canonical DSL (never from a
    # persisted callable), so a drifted or uncompilable payload fails closed.
    evaluate = compile_predicate_dsl(name, dsl)
    # facts_whitelist and compiled_digest are derived facts of the DSL; a
    # payload whose persisted copies disagree with the recompile drifted.
    derived_whitelist = predicate_facts_whitelist(dsl)
    derived_digest = hashlib.sha256(
        f"{COMPILER_VERSION}:{name}:{dsl}".encode("utf-8")
    ).hexdigest()
    persisted_whitelist = tuple(data.get("facts_whitelist") or ())
    persisted_digest = str(data.get("compiled_digest") or "")
    if persisted_whitelist and persisted_whitelist != derived_whitelist:
        raise ValueError(
            f"facts_whitelist drift for predicate: {name!r}"
        )
    if persisted_digest and persisted_digest != derived_digest:
        raise ValueError(f"compiled_digest drift for predicate: {name!r}")
    return PredicateEntry(
        name=name,
        canonical_ast=dict(data.get("canonical_ast") or {"dsl": dsl}),
        canonical_dsl=dsl,
        facts_whitelist=derived_whitelist,
        compiled_digest=derived_digest,
        registered_generation=int(data.get("registered_generation") or 0),
        status=status,
        evaluate=evaluate,
    )


def _operator_from_dict(data: Mapping[str, Any]) -> ActionOperator:
    policy_stage = str(data.get("policy_stage") or "")
    if policy_stage and policy_stage not in POLICY_STAGES:
        raise ValueError(f"unknown policy_stage: {policy_stage!r}")
    return ActionOperator(
        operator=str(data.get("operator") or ""),
        params_schema=tuple(data.get("params_schema") or ()),
        policy_stage=policy_stage,
        runtime_owner=str(data.get("runtime_owner") or ""),
    )


def _instance_from_dict(data: Mapping[str, Any]) -> ActionInstance:
    status = str(data.get("status") or "")
    if status not in ENTRY_STATUSES:
        raise ValueError(f"unknown action-instance status: {status!r}")
    return ActionInstance(
        name=str(data.get("name") or ""),
        operator=str(data.get("operator") or ""),
        params=tuple(data.get("params") or ()),
        registered_generation=int(data.get("registered_generation") or 0),
        status=status,
    )


def registry_edit_from_dict(data: Mapping[str, Any]) -> RegistryEdit:
    return RegistryEdit(
        edit_id=str(data.get("edit_id") or ""),
        operation=str(data.get("operation") or ""),
        target=str(data.get("target") or ""),
        before_hash=str(data.get("before_hash") or ""),
        after_hash=str(data.get("after_hash") or ""),
        reason_codes=tuple(data.get("reason_codes") or ()),
        sequence=int(data.get("sequence") or 0),
    )


def _registry_from_dict(data: Mapping[str, Any]) -> VocabularyRegistry:
    """Rebuild a registry from its canonical payload, recompiling DSL."""
    if str(data.get("schema_version") or "") != REGISTRY_SCHEMA_VERSION:
        raise ValueError(
            f"unknown registry schema_version: {data.get('schema_version')!r}"
        )
    predicates = {
        str(row.get("name")): _predicate_from_dict(row)
        for row in data.get("predicates") or ()
    }
    operators = {
        str(row.get("operator")): _operator_from_dict(row)
        for row in data.get("action_operators") or ()
    }
    instances = {
        str(row.get("name")): _instance_from_dict(row)
        for row in data.get("action_instances") or ()
    }
    audit = tuple(
        registry_edit_from_dict(row) for row in data.get("audit") or ()
    )
    return VocabularyRegistry(
        registry_version=str(data.get("registry_version") or REGISTRY_VERSION),
        predicates=predicates,
        action_operators=operators,
        action_instances=instances,
        audit=audit,
    )


VocabularyRegistry.from_dict = staticmethod(_registry_from_dict)


# ============================================================
# RegistryEdit application (online evolution; immutable lineage)
# ============================================================

def _next_sequence(registry: VocabularyRegistry) -> int:
    return (registry.audit[-1].sequence + 1) if registry.audit else 1


def _edit_id(operation: str, target: str, sequence: int, before_hash: str) -> str:
    digest = hashlib.sha256(
        f"{operation}:{target}:{sequence}:{before_hash}".encode("utf-8")
    ).hexdigest()[:12]
    return f"registry_edit_{digest}"


def apply_registry_edit(
    registry: VocabularyRegistry,
    *,
    operation: str,
    target: str,
    dsl: str = "",
    operator: str = "",
    reason_codes: tuple[str, ...] = (),
) -> VocabularyRegistry:
    """Apply one audited registry edit, returning a NEW registry.

    ``operation`` is one of ``register_predicate`` / ``register_action`` /
    ``disable`` / ``enable``. Baseline entries cannot be disabled, and an
    unknown target/operator or uncompilable DSL fails closed (raises). The
    new registry carries the appended ``RegistryEdit`` in its audit trail
    with a chained before/after hash.
    """
    before_hash = registry.content_hash
    sequence = _next_sequence(registry)
    predicates = {
        name: registry.raw_predicate(name)
        for name in registry.all_predicate_names()
    }
    operators = {
        name: registry.raw_operator(name)
        for name in registry.all_operator_names()
    }
    instances = {
        name: registry.raw_action_instance(name)
        for name in registry.all_action_instance_names()
    }

    if operation == "register_predicate":
        if registry.raw_predicate(target) is not None:
            raise ValueError(f"predicate already registered: {target}")
        evaluate = compile_predicate_dsl(target, dsl)
        predicates[target] = PredicateEntry(
            name=target,
            canonical_ast={"dsl": dsl},
            canonical_dsl=dsl,
            facts_whitelist=predicate_facts_whitelist(dsl),
            compiled_digest=hashlib.sha256(
                f"{COMPILER_VERSION}:{target}:{dsl}".encode("utf-8")
            ).hexdigest(),
            registered_generation=sequence,
            # First registration is pending: shadow-only, never executable
            # until a later enable promotes it to working.
            status=STATUS_PENDING,
            evaluate=evaluate,
        )
    elif operation == "register_action":
        source_operator = operators.get(operator)
        if source_operator is None:
            raise ValueError(f"unknown action operator: {operator}")
        if source_operator.runtime_owner != "court":
            raise ValueError(
                f"operator not court-local, cannot instantiate: {operator}"
            )
        if registry.raw_action_instance(target) is not None:
            raise ValueError(f"action instance already registered: {target}")
        instances[target] = ActionInstance(
            name=target,
            operator=operator,
            params=source_operator.params_schema,
            registered_generation=sequence,
            status=STATUS_PENDING,
        )
    elif operation in ("disable", "enable"):
        entry = predicates.get(target) or instances.get(target)
        if entry is None:
            raise ValueError(f"unknown registry entry: {target}")
        if operation == "disable":
            # Only a working entry can be switched off; disabling a pending
            # shadow entry would be a no-op audited as if it mattered.
            if entry.status != STATUS_REGISTERED:
                raise ValueError(
                    f"only registered entries can be disabled: {target} "
                    f"(status={entry.status})"
                )
            new_status = STATUS_DISABLED
        else:
            # Only a shadow or switched-off entry can be promoted to
            # working; re-enabling an active entry is a no-op audit edit.
            if entry.status not in (STATUS_PENDING, STATUS_DISABLED):
                raise ValueError(
                    f"only pending/disabled entries can be enabled: {target} "
                    f"(status={entry.status})"
                )
            new_status = STATUS_REGISTERED
        replacement = replace(entry, status=new_status)
        if isinstance(replacement, PredicateEntry):
            predicates[target] = replacement
        else:
            instances[target] = replacement
    else:
        raise ValueError(f"unknown registry operation: {operation}")

    # ``after_hash`` seals the registry's vocabulary content after this edit
    # (audit trail kept at its prior length), so it is verifiable and free of
    # self-reference: rebuild the post-edit content with the same audit and
    # confirm the hash matches. The edit is then appended to the audit trail.
    content_after = VocabularyRegistry(
        registry_version=registry.registry_version,
        predicates=predicates,
        action_operators=operators,
        action_instances=instances,
        audit=registry.audit,
    )
    edit = RegistryEdit(
        edit_id=_edit_id(operation, target, sequence, before_hash),
        operation=operation,
        target=target,
        before_hash=before_hash,
        after_hash=content_after.content_hash,
        reason_codes=tuple(reason_codes),
        sequence=sequence,
    )
    return VocabularyRegistry(
        registry_version=registry.registry_version,
        predicates=predicates,
        action_operators=operators,
        action_instances=instances,
        audit=registry.audit + (edit,),
    )


# The single authoritative baseline registry for the L2 vocabulary. It is
# built after the DSL compiler is defined so every baseline predicate is
# compiled by the same deterministic compiler used for online registration.
L2_REGISTRY = _build_baseline_registry()
