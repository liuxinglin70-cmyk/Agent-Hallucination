"""Atomic RepairUnit schema, contracts, and error taxonomy.

A RepairUnit is the unified minimal unit of governance for self-evolution:
from generation, compilation, development, paired measurement to deployment
and retirement. It defines an explicit target, scope, observation, decision,
and claim, which compiles deterministically to intervention DAG entries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

REPAIR_UNIT_SCHEMA = "evolution.repair_unit.v1"
COMPILED_UNIT_SCHEMA = "evolution.compiled_unit.v1"

# Versioned compilation error codes (§5.5)
ERROR_SCHEMA_INVALID = "schema_invalid"
ERROR_MECHANISM_UNBOUND = "mechanism_unbound"
ERROR_UNSUPPORTED_CAPABILITY = "unsupported_capability"
ERROR_DEPENDENCY_UNRESOLVED = "dependency_unresolved"
ERROR_UNSAFE_TERMINAL_ACTION = "unsafe_terminal_action"
ERROR_SCOPE_UNSAFE = "scope_unsafe"
ERROR_DUPLICATE_EXECUTABLE = "duplicate_executable"
ERROR_PROVIDER_FAILURE = "provider_failure"

COMPILATION_ERROR_CODES = frozenset({
    ERROR_SCHEMA_INVALID,
    ERROR_MECHANISM_UNBOUND,
    ERROR_UNSUPPORTED_CAPABILITY,
    ERROR_DEPENDENCY_UNRESOLVED,
    ERROR_UNSAFE_TERMINAL_ACTION,
    ERROR_SCOPE_UNSAFE,
    ERROR_DUPLICATE_EXECUTABLE,
    ERROR_PROVIDER_FAILURE,
})

OBJECTIVES = frozenset({"detection", "resolution", "localization", "joint"})
POLARITIES = frozenset({"recall", "precision", "boundary"})


class RepairUnitError(Exception):
    """Structured, versioned failure raised during unit compilation or validation."""

    def __init__(self, code: str, detail: str) -> None:
        if code not in COMPILATION_ERROR_CODES:
            raise ValueError(f"Unknown compilation error code: {code!r}")
        super().__init__(f"[{code}] {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TargetContract:
    mechanism_id: str
    objective: str
    polarity: str
    repair_operation: str

    def validate(self) -> None:
        if not self.mechanism_id or not isinstance(self.mechanism_id, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "target.mechanism_id must be a non-empty string")
        if self.objective not in OBJECTIVES:
            raise RepairUnitError(ERROR_SCHEMA_INVALID, f"target.objective must be one of {sorted(OBJECTIVES)}, got {self.objective!r}")
        if self.polarity not in POLARITIES:
            raise RepairUnitError(ERROR_SCHEMA_INVALID, f"target.polarity must be one of {sorted(POLARITIES)}, got {self.polarity!r}")
        if not self.repair_operation or not isinstance(self.repair_operation, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "target.repair_operation must be a non-empty string")


@dataclass(frozen=True)
class ScopeContract:
    contract_kind: str
    anchor_selector: str
    target_selector: str
    event_window: str
    must_include: tuple[str, ...] = field(default_factory=tuple)
    on_incomplete: str = "abstain"

    def validate(self) -> None:
        if not self.contract_kind or not isinstance(self.contract_kind, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "scope.contract_kind must be a non-empty string")
        if not self.anchor_selector or not isinstance(self.anchor_selector, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "scope.anchor_selector must be a non-empty string")
        if not self.target_selector or not isinstance(self.target_selector, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "scope.target_selector must be a non-empty string")


@dataclass(frozen=True)
class ObservationContract:
    runner: str
    predicate_name: str
    input_views: tuple[str, ...]
    rules: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    default_verdict: str = "not_applicable"
    description: str = ""
    conviction_verdicts: tuple[str, ...] = ("violated",)
    verdicts: tuple[str, ...] = ("satisfied", "violated", "not_applicable")

    def validate(self) -> None:
        if not self.runner or not isinstance(self.runner, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "observation.runner must be a non-empty string")
        if not self.predicate_name or not isinstance(self.predicate_name, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "observation.predicate_name must be a non-empty string")


@dataclass(frozen=True)
class DecisionContract:
    requested_action: str
    required_evidence: tuple[str, ...] = field(default_factory=tuple)
    not_when: tuple[str, ...] = field(default_factory=tuple)
    on_uncertain: str = "no_new_action"

    def validate(self) -> None:
        if not self.requested_action or not isinstance(self.requested_action, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "decision.requested_action must be a non-empty string")


@dataclass(frozen=True)
class ClaimContract:
    expected_behavior: str
    target_kind: str = "detection"

    def validate(self) -> None:
        if not self.expected_behavior or not isinstance(self.expected_behavior, str):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "claim.expected_behavior must be a non-empty string")
        if self.target_kind not in OBJECTIVES:
            raise RepairUnitError(ERROR_SCHEMA_INVALID, f"claim.target_kind must be one of {sorted(OBJECTIVES)}, got {self.target_kind!r}")


@dataclass(frozen=True)
class RepairUnit:
    schema_version: str
    target: TargetContract
    scope: ScopeContract
    observation: ObservationContract
    decision: DecisionContract
    claim: ClaimContract
    provenance: Mapping[str, Any] = field(default_factory=dict)
    artifact: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if self.schema_version != REPAIR_UNIT_SCHEMA:
            raise RepairUnitError(
                ERROR_SCHEMA_INVALID,
                f"schema_version must be {REPAIR_UNIT_SCHEMA!r}, got {self.schema_version!r}",
            )
        self.target.validate()
        self.scope.validate()
        self.observation.validate()
        self.decision.validate()
        self.claim.validate()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.artifact is None:
            result.pop("artifact", None)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepairUnit:
        if not isinstance(data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "RepairUnit data must be a mapping")
        schema = str(data.get("schema_version") or "")
        if schema != REPAIR_UNIT_SCHEMA:
            raise RepairUnitError(
                ERROR_SCHEMA_INVALID,
                f"schema_version must be {REPAIR_UNIT_SCHEMA!r}, got {schema!r}",
            )

        target_data = data.get("target") or {}
        scope_data = data.get("scope") or {}
        obs_data = data.get("observation") or {}
        dec_data = data.get("decision") or {}
        claim_data = data.get("claim") or {}

        if not isinstance(target_data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "target must be a mapping")
        if not isinstance(scope_data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "scope must be a mapping")
        if not isinstance(obs_data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "observation must be a mapping")
        if not isinstance(dec_data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "decision must be a mapping")
        if not isinstance(claim_data, Mapping):
            raise RepairUnitError(ERROR_SCHEMA_INVALID, "claim must be a mapping")

        unit = cls(
            schema_version=schema,
            target=TargetContract(
                mechanism_id=str(target_data.get("mechanism_id") or ""),
                objective=str(target_data.get("objective") or "detection"),
                polarity=str(target_data.get("polarity") or "recall"),
                repair_operation=str(target_data.get("repair_operation") or ""),
            ),
            scope=ScopeContract(
                contract_kind=str(scope_data.get("contract_kind") or ""),
                anchor_selector=str(scope_data.get("anchor_selector") or ""),
                target_selector=str(scope_data.get("target_selector") or ""),
                event_window=str(scope_data.get("event_window") or ""),
                must_include=tuple(str(x) for x in scope_data.get("must_include") or ()),
                on_incomplete=str(scope_data.get("on_incomplete") or "abstain"),
            ),
            observation=ObservationContract(
                runner=str(obs_data.get("runner") or ""),
                predicate_name=str(obs_data.get("predicate_name") or ""),
                input_views=tuple(str(x) for x in obs_data.get("input_views") or ()),
                rules=tuple(dict(r) for r in obs_data.get("rules") or ()),
                default_verdict=str(obs_data.get("default_verdict") or "not_applicable"),
                description=str(obs_data.get("description") or ""),
                conviction_verdicts=tuple(str(x) for x in obs_data.get("conviction_verdicts") or ("violated",)),
                verdicts=tuple(str(x) for x in obs_data.get("verdicts") or ("satisfied", "violated", "not_applicable")),
            ),
            decision=DecisionContract(
                requested_action=str(dec_data.get("requested_action") or ""),
                required_evidence=tuple(str(x) for x in dec_data.get("required_evidence") or ()),
                not_when=tuple(str(x) for x in dec_data.get("not_when") or ()),
                on_uncertain=str(dec_data.get("on_uncertain") or "no_new_action"),
            ),
            claim=ClaimContract(
                expected_behavior=str(claim_data.get("expected_behavior") or ""),
                target_kind=str(claim_data.get("target_kind") or "detection"),
            ),
            provenance=dict(data.get("provenance") or {}),
            artifact=dict(data["artifact"]) if isinstance(data.get("artifact"), Mapping) else None,
        )
        unit.validate()
        return unit


@dataclass(frozen=True)
class CompiledUnit:
    unit_hash: str
    compiler_version: str
    entries: tuple[Mapping[str, Any], ...]
    dependency_graph: Mapping[str, tuple[str, ...]]
    required_runner_artifacts: tuple[str, ...]
    effect_signature: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPILED_UNIT_SCHEMA,
            "unit_hash": self.unit_hash,
            "compiler_version": self.compiler_version,
            "entries": list(self.entries),
            "dependency_graph": {k: list(v) for k, v in self.dependency_graph.items()},
            "required_runner_artifacts": list(self.required_runner_artifacts),
            "effect_signature": dict(self.effect_signature),
        }


def canonical_unit_bytes(unit: RepairUnit, compiler_version: str = "v1") -> bytes:
    """Deterministic canonical byte representation of a RepairUnit."""
    payload = {
        "schema_version": unit.schema_version,
        "compiler_version": compiler_version,
        "target": {
            "mechanism_id": unit.target.mechanism_id,
            "objective": unit.target.objective,
            "polarity": unit.target.polarity,
            "repair_operation": unit.target.repair_operation,
        },
        "scope": {
            "anchor_selector": unit.scope.anchor_selector,
            "contract_kind": unit.scope.contract_kind,
            "event_window": unit.scope.event_window,
            "must_include": sorted(unit.scope.must_include),
            "on_incomplete": unit.scope.on_incomplete,
            "target_selector": unit.scope.target_selector,
        },
        "observation": {
            "conviction_verdicts": sorted(unit.observation.conviction_verdicts),
            "default_verdict": unit.observation.default_verdict,
            "description": unit.observation.description,
            "input_views": sorted(unit.observation.input_views),
            "predicate_name": unit.observation.predicate_name,
            "rules": [dict(sorted(r.items())) for r in unit.observation.rules],
            "runner": unit.observation.runner,
            "verdicts": sorted(unit.observation.verdicts),
        },
        "decision": {
            "not_when": sorted(unit.decision.not_when),
            "on_uncertain": unit.decision.on_uncertain,
            "requested_action": unit.decision.requested_action,
            "required_evidence": sorted(unit.decision.required_evidence),
        },
        "claim": {
            "expected_behavior": unit.claim.expected_behavior,
            "target_kind": unit.claim.target_kind,
        },
    }
    if unit.artifact:
        payload["artifact"] = dict(sorted(unit.artifact.items()))
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def repair_unit_hash(unit: RepairUnit, compiler_version: str = "v1") -> str:
    """Compute the immutable SHA-256 fingerprint for a RepairUnit."""
    return hashlib.sha256(canonical_unit_bytes(unit, compiler_version)).hexdigest()
