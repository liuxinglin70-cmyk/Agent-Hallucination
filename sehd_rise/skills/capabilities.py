"""Machine-readable skill capability authority.

Phase 1 -- Skill Semantic Closure makes this registry the only place that
defines what an induced Skill can actually do.  A capability is admitted here
only when the deployed runtime already has a deterministic executor and the
evaluation layer has an observable postcondition for the same operation.

The registry deliberately describes the current runtime, including its
negative space.  In particular, every L1 capability is a post-court monotonic
transform; no L1 capability creates candidates, evidence, certificates, or
provider reasoning.  Missing constructive behavior is reported as a
``CapabilityGap`` rather than being translated into a suppressive operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


L1_COURT_TRANSFORM_EXECUTOR = "l1_court_transform"
L1_CONSTRUCTIVE_HARNESS_EXECUTOR = "l1_constructive_harness"
L2_ADJUDICATION_POLICY_EXECUTOR = "l2_adjudication_policy"
BINDING_DOMAINS = frozenset({
    "claim_anchor", "candidate", "certificate", "origin", "coverage",
    "boundary", "event",
})
BINDING_CARDINALITIES = frozenset({"exactly_one", "one_or_many"})


@dataclass(frozen=True)
class SkillCapability:
    """One executable repair primitive and its complete semantic contract."""

    skill_layer: str
    module: str
    executable_operation: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    applicable_failure_shapes: tuple[str, ...]
    supported_directions: tuple[str, ...]
    binding_requirements: tuple[str, ...]
    input_binding_domain: str
    required_binding_fields: tuple[str, ...]
    cardinality: str
    expected_local_effect: tuple[str, str]
    possible_terminal_effect: tuple[str, ...]
    executor: str
    fitness_postcondition: str
    route_ids: tuple[str, ...] = ()
    output_object: str = ""
    produces_domain: str = ""
    operation_targets: tuple[str, ...] = ()
    does_not_support: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_layer": self.skill_layer,
            "module": self.module,
            "executable_operation": self.executable_operation,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "applicable_failure_shapes": list(self.applicable_failure_shapes),
            "supported_directions": list(self.supported_directions),
            "binding_requirements": list(self.binding_requirements),
            "input_binding_domain": self.input_binding_domain,
            "required_binding_fields": list(self.required_binding_fields),
            "cardinality": self.cardinality,
            "expected_local_effect": {
                "observable_field": self.expected_local_effect[0],
                "expected_direction": self.expected_local_effect[1],
            },
            "possible_terminal_effect": list(self.possible_terminal_effect),
            "executor": self.executor,
            "fitness_postcondition": self.fitness_postcondition,
            "route_ids": list(self.route_ids),
            "output_object": self.output_object,
            "produces_domain": self.produces_domain,
            "operation_targets": list(self.operation_targets),
            "does_not_support": list(self.does_not_support),
        }


@dataclass(frozen=True)
class CapabilityGap:
    """A learn-worthy failure the current Skill language cannot express."""

    failure: str
    owner: str
    desired_transition: str
    missing_capability: str

    def to_dict(self) -> dict[str, str]:
        return {
            "failure": self.failure,
            "owner": self.owner,
            "desired_transition": self.desired_transition,
            "missing_capability": self.missing_capability,
        }


class SkillCapabilityRegistry:
    """Immutable query surface shared by diagnosis, planning, and runtime."""

    def __init__(
        self,
        capabilities: Iterable[SkillCapability],
        *,
        non_repair_runtime_operations: Mapping[str, str] | None = None,
    ) -> None:
        by_operation: dict[str, SkillCapability] = {}
        for capability in capabilities:
            operation = capability.executable_operation
            if not operation or operation in by_operation:
                raise ValueError(f"duplicate_or_empty_capability:{operation}")
            required = (
                capability.skill_layer,
                capability.module,
                capability.executor,
                capability.fitness_postcondition,
                capability.input_binding_domain,
                capability.cardinality,
                capability.expected_local_effect[0],
                capability.expected_local_effect[1],
            )
            if not all(required):
                raise ValueError(f"incomplete_capability:{operation}")
            if capability.input_binding_domain not in BINDING_DOMAINS:
                raise ValueError(f"invalid_binding_domain:{operation}")
            if capability.cardinality not in BINDING_CARDINALITIES:
                raise ValueError(f"invalid_binding_cardinality:{operation}")
            if not capability.required_binding_fields:
                raise ValueError(f"binding_fields_missing:{operation}")
            by_operation[operation] = capability
        self._by_operation = MappingProxyType(by_operation)
        self._non_repair_runtime_operations = MappingProxyType(
            dict(non_repair_runtime_operations or {})
        )

    def contains(self, operation: str) -> bool:
        return str(operation) in self._by_operation

    def get(self, operation: str) -> SkillCapability | None:
        return self._by_operation.get(str(operation))

    def require(self, operation: str) -> SkillCapability:
        capability = self.get(operation)
        if capability is None:
            raise ValueError(f"operation_not_in_capability_registry:{operation}")
        return capability

    def all(self) -> tuple[SkillCapability, ...]:
        return tuple(self._by_operation.values())

    def non_repair_runtime_operations(self) -> Mapping[str, str]:
        return self._non_repair_runtime_operations

    def for_layer(self, skill_layer: str) -> tuple[SkillCapability, ...]:
        return tuple(
            capability for capability in self.all()
            if capability.skill_layer == str(skill_layer)
        )

    def for_module(self, module: str) -> tuple[SkillCapability, ...]:
        return tuple(
            capability for capability in self.all()
            if capability.module == str(module)
        )

    def query(
        self,
        *,
        skill_layer: str = "",
        modules: Sequence[str] = (),
        failure_shape: str = "",
        direction: str = "",
        target_field: str = "",
    ) -> tuple[SkillCapability, ...]:
        """Return capabilities that satisfy every supplied contract filter."""
        module_set = {str(value) for value in modules if str(value)}
        direction = str(direction or "")
        failure_shape = str(failure_shape or "")
        target_field = str(target_field or "")
        matches: list[SkillCapability] = []
        for capability in self.all():
            if skill_layer and capability.skill_layer != skill_layer:
                continue
            if module_set and capability.module not in module_set:
                continue
            if (
                failure_shape
                and capability.applicable_failure_shapes
                and failure_shape not in capability.applicable_failure_shapes
            ):
                continue
            if (
                direction
                and direction not in capability.supported_directions
            ):
                continue
            if target_field and target_field not in capability.operation_targets:
                continue
            matches.append(capability)
        return tuple(matches)

    def operations_for(
        self,
        *,
        skill_layer: str = "",
        modules: Sequence[str] = (),
        failure_shape: str = "",
        direction: str = "",
        target_field: str = "",
    ) -> tuple[str, ...]:
        return tuple(sorted(
            capability.executable_operation
            for capability in self.query(
                skill_layer=skill_layer,
                modules=modules,
                failure_shape=failure_shape,
                direction=direction,
                target_field=target_field,
            )
        ))

    def gap_for(
        self,
        *,
        failure_shape: str,
        owner: str,
        desired_transition: str,
    ) -> CapabilityGap:
        normalized_failure = _GAP_FAILURE_NAMES.get(
            str(failure_shape), str(failure_shape) or "unexpressible_failure"
        )
        missing = _MISSING_CAPABILITY_BY_OWNER.get(
            str(owner), "extend_skill_runtime"
        )
        specific = _MISSING_CAPABILITY_BY_FAILURE.get(str(failure_shape))
        if specific:
            missing = specific
        return CapabilityGap(
            failure=normalized_failure,
            owner=str(owner),
            desired_transition=_render_transition(desired_transition),
            missing_capability=missing,
        )


_SUPPRESSIVE_DIRECTIONS = (
    "error_to_clean",
    "error_to_unresolved",
    "unresolved_to_clean",
    "unresolved_to_decisive",
    "process_repair",
)
_LOCALIZATION_DIRECTIONS = (
    "unresolved_to_error",
    "unresolved_to_decisive",
    "process_repair",
)


_CAPABILITIES = (
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="candidate_trigger",
        executable_operation="construct_candidate",
        reads=("deterministic claim anchor", "label-free trajectory context"),
        writes=("skill_constructed_candidates", "candidate availability"),
        applicable_failure_shapes=("gold_false_negative_clean",),
        supported_directions=("clean_to_error",),
        binding_requirements=(
            "exactly one existing claim anchor", "label-free observable condition",
        ),
        input_binding_domain="claim_anchor",
        required_binding_fields=(
            "anchor_id", "source_event_id", "span_start", "span_end",
            "span_text_hash",
        ),
        cardinality="exactly_one",
        expected_local_effect=("candidate_count", "increase"),
        possible_terminal_effect=(
            "verified_clean->verified_error", "verified_clean->unresolved",
            "verified_clean->verified_clean",
        ),
        executor=L1_CONSTRUCTIVE_HARNESS_EXECUTOR,
        fitness_postcondition="candidate_materialized_for_bound_anchor",
        route_ids=("local_constructive_harness",),
        output_object="candidate",
        produces_domain="candidate",
        operation_targets=("anchor_id",),
        does_not_support=(
            "author candidate verdict", "author evidence", "emit certificate",
            "override verifier", "change terminal verdict directly",
            "read gold", "choose arbitrary text spans", "rerun full court",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="candidate_trigger",
        executable_operation="suppress_candidate",
        reads=("existing census candidate", "surviving error certificates"),
        writes=("candidate availability", "surviving_error_certificate_ids"),
        applicable_failure_shapes=(
            "gold_false_positive", "provider_artifact_malformed",
        ),
        supported_directions=_SUPPRESSIVE_DIRECTIONS,
        binding_requirements=("existing candidate_id", "matched result condition"),
        input_binding_domain="candidate",
        required_binding_fields=("candidate_id",),
        cardinality="one_or_many",
        expected_local_effect=("candidate_count", "decrease"),
        possible_terminal_effect=(
            "verified_error->unresolved", "verified_error->verified_clean",
        ),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="object_absent",
        route_ids=("post_court_l1",),
        output_object="candidate",
        operation_targets=("candidate", "candidate_id"),
        does_not_support=(
            "author candidate verdict", "modify provider candidate generation",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="evidence_obligation",
        executable_operation="keep_coverage_incomplete",
        reads=("existing prosecution coverage",),
        writes=("prosecution_coverage", "terminal state"),
        applicable_failure_shapes=(
            "gold_false_positive", "quarantine_pattern_dominant",
            "provider_artifact_malformed",
        ),
        supported_directions=_SUPPRESSIVE_DIRECTIONS,
        binding_requirements=("prosecution_coverage == complete",),
        input_binding_domain="coverage",
        required_binding_fields=("coverage_side",),
        cardinality="one_or_many",
        expected_local_effect=("prosecution_coverage", "opened"),
        possible_terminal_effect=("verified_clean->unresolved",),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="coverage_not_complete",
        route_ids=("post_court_l1",),
        output_object="error_certificate",
        operation_targets=("coverage_state",),
        does_not_support=(
            "collect missing evidence", "construct certificate",
            "verified_clean->verified_error", "modify provider reasoning",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="candidate_to_verifier_route",
        executable_operation="hold_candidate_qualification",
        reads=("existing candidate qualification", "surviving certificates"),
        writes=("surviving_error_certificate_ids",),
        applicable_failure_shapes=(
            "gold_false_positive", "provider_artifact_malformed",
        ),
        supported_directions=_SUPPRESSIVE_DIRECTIONS,
        binding_requirements=("existing candidate_id",),
        input_binding_domain="candidate",
        required_binding_fields=("candidate_id",),
        cardinality="one_or_many",
        expected_local_effect=("prosecution_certificates", "decrease"),
        possible_terminal_effect=(
            "verified_error->unresolved", "verified_error->verified_clean",
        ),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="qualification_not_positive",
        route_ids=("post_court_l1",),
        output_object="candidate_qualification",
        operation_targets=("violation_state", "actor_state", "boundary_state"),
        does_not_support=(
            "route a missing candidate", "invoke a verifier",
            "verified_clean->verified_error",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="atomic_verifier_instruction",
        executable_operation="withhold_certificate",
        reads=("existing error certificate", "surviving_error_certificate_ids"),
        writes=("surviving_error_certificate_ids", "terminal state"),
        applicable_failure_shapes=(
            "gold_false_positive", "provider_artifact_malformed",
            "quarantine_pattern_dominant",
        ),
        supported_directions=_SUPPRESSIVE_DIRECTIONS,
        binding_requirements=("existing certificate_id", "matched result condition"),
        input_binding_domain="certificate",
        required_binding_fields=("certificate_id",),
        cardinality="exactly_one",
        expected_local_effect=("surviving_certificates", "decrease"),
        possible_terminal_effect=(
            "verified_error->unresolved", "verified_error->verified_clean",
        ),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="targeted_certificate_withheld",
        route_ids=("post_court_l1",),
        output_object="verification_certificate",
        operation_targets=("certificate_id",),
        does_not_support=(
            "create missing certificate", "reverify claim",
            "verified_clean->verified_error", "modify verifier instructions",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="causal_link_test",
        executable_operation="reject_non_causal_origin",
        reads=("existing responsible origin", "surviving certificates"),
        writes=("surviving_error_certificate_ids", "responsible_event_id"),
        applicable_failure_shapes=(
            "gold_false_positive", "provider_artifact_malformed",
            "localization_temporal_fallback",
        ),
        supported_directions=_SUPPRESSIVE_DIRECTIONS,
        binding_requirements=("existing origin_event_id",),
        input_binding_domain="origin",
        required_binding_fields=("origin_event_id",),
        cardinality="exactly_one",
        expected_local_effect=("responsible_step", "removed"),
        possible_terminal_effect=(
            "verified_error->unresolved", "verified_error->verified_clean",
        ),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="origin_not_selected",
        route_ids=("post_court_l1",),
        output_object="causal_certificate_or_boundary",
        operation_targets=("responsible_event_id", "is_root_cause"),
        does_not_support=(
            "construct causal evidence", "create causal certificate",
            "modify provider causal reasoning",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="earliest_origin_tiebreak",
        executable_operation="select_earliest_origin",
        reads=("existing surviving certificates", "census temporal order"),
        writes=("responsible_event_id",),
        applicable_failure_shapes=(
            "localization_temporal_fallback", "responsible_step_missing",
        ),
        supported_directions=_LOCALIZATION_DIRECTIONS,
        binding_requirements=("at least one surviving origin",),
        input_binding_domain="origin",
        required_binding_fields=("origin_event_id",),
        cardinality="one_or_many",
        expected_local_effect=("responsible_step", "shift"),
        possible_terminal_effect=(
            "verified_error->verified_error", "unresolved->verified_error",
        ),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="responsible_origin_present",
        route_ids=("post_court_l1",),
        output_object="boundary_finding",
        operation_targets=("responsible_event_id",),
        does_not_support=(
            "create an origin", "create an error certificate",
            "verified_clean->verified_error",
        ),
    ),
    SkillCapability(
        skill_layer="l1_provider_rule",
        module="earliest_origin_tiebreak",
        executable_operation="suppress_later_origin",
        reads=("existing surviving origins", "census temporal order"),
        writes=("responsible_event_id",),
        applicable_failure_shapes=(
            "localization_temporal_fallback", "responsible_step_missing",
        ),
        supported_directions=_LOCALIZATION_DIRECTIONS,
        binding_requirements=("at least two existing origins",),
        input_binding_domain="origin",
        required_binding_fields=("origin_event_id",),
        cardinality="one_or_many",
        expected_local_effect=("responsible_step", "shift"),
        possible_terminal_effect=("verified_error->verified_error",),
        executor=L1_COURT_TRANSFORM_EXECUTOR,
        fitness_postcondition="later_origin_not_selected",
        route_ids=("post_court_l1",),
        output_object="boundary_finding",
        operation_targets=("responsible_event_id",),
        does_not_support=(
            "create an origin", "create an error certificate",
            "verified_clean->verified_error",
        ),
    ),
    SkillCapability(
        skill_layer="l2_adjudication_policy",
        module="coverage_validity_policy",
        executable_operation="downgrade_coverage",
        reads=("existing coverage state",),
        writes=("effective prosecution/defense coverage",),
        applicable_failure_shapes=(
            "hollow_complete_prosecution", "hollow_complete_defense",
            "all_candidates_quarantined",
        ),
        supported_directions=(
            "error_to_clean", "unresolved_to_clean",
            "unresolved_to_error", "unresolved_to_decisive", "process_repair",
        ),
        binding_requirements=("side", "to_state"),
        input_binding_domain="coverage",
        required_binding_fields=("coverage_side",),
        cardinality="one_or_many",
        expected_local_effect=("effective_coverage", "decrease"),
        possible_terminal_effect=("decisive->unresolved",),
        executor=L2_ADJUDICATION_POLICY_EXECUTOR,
        fitness_postcondition="policy_item_fired_and_coverage_changed",
        output_object="coverage_validity_policy",
        operation_targets=("prosecution", "defense"),
        does_not_support=(
            "create certificate", "restore pre-ledger consensus",
        ),
    ),
    SkillCapability(
        skill_layer="l2_adjudication_policy",
        module="coverage_validity_policy",
        executable_operation="invalidate_coverage_claim",
        reads=("existing coverage claim", "valid certificate count"),
        writes=("effective prosecution/defense coverage",),
        applicable_failure_shapes=(
            "hollow_complete_prosecution", "hollow_complete_defense",
            "all_candidates_quarantined",
        ),
        supported_directions=(
            "error_to_clean", "unresolved_to_clean",
            "unresolved_to_error", "unresolved_to_decisive", "process_repair",
        ),
        binding_requirements=("side",),
        input_binding_domain="coverage",
        required_binding_fields=("coverage_side",),
        cardinality="one_or_many",
        expected_local_effect=("effective_coverage", "invalidate"),
        possible_terminal_effect=("decisive->unresolved",),
        executor=L2_ADJUDICATION_POLICY_EXECUTOR,
        fitness_postcondition="policy_item_fired_and_coverage_changed",
        output_object="coverage_validity_policy",
        operation_targets=("prosecution", "defense"),
        does_not_support=("create certificate",),
    ),
    SkillCapability(
        skill_layer="l2_adjudication_policy",
        module="coverage_validity_policy",
        executable_operation="require_additional_verification",
        reads=("existing complete coverage claims",),
        writes=("effective prosecution/defense coverage", "audit telemetry"),
        applicable_failure_shapes=(
            "hollow_complete_prosecution", "hollow_complete_defense",
        ),
        supported_directions=(
            "error_to_clean", "unresolved_to_clean",
            "unresolved_to_decisive", "process_repair",
        ),
        binding_requirements=("verification_scope",),
        input_binding_domain="coverage",
        required_binding_fields=("coverage_side",),
        cardinality="one_or_many",
        expected_local_effect=("effective_coverage", "opened"),
        possible_terminal_effect=("decisive->unresolved",),
        executor=L2_ADJUDICATION_POLICY_EXECUTOR,
        fitness_postcondition="policy_item_fired_and_coverage_changed",
        output_object="coverage_validity_policy",
        operation_targets=("candidate", "certificate", "evidence_chain"),
        does_not_support=(
            "rerun provider verification", "construct evidence",
        ),
    ),
    SkillCapability(
        skill_layer="l2_adjudication_policy",
        module="boundary_consumption_policy",
        executable_operation="consume_verified_clean_boundary",
        reads=("verified-clean boundary", "terminal state", "surviving errors"),
        writes=("terminal override",),
        applicable_failure_shapes=("boundary_terminal_conflict",),
        supported_directions=(
            "unresolved_to_clean", "unresolved_to_decisive", "process_repair",
        ),
        binding_requirements=(
            "authoritative verified-clean boundary", "zero surviving errors",
        ),
        input_binding_domain="boundary",
        required_binding_fields=("boundary_id",),
        cardinality="exactly_one",
        expected_local_effect=("terminal_state", "verified_clean"),
        possible_terminal_effect=("unresolved->verified_clean",),
        executor=L2_ADJUDICATION_POLICY_EXECUTOR,
        fitness_postcondition="policy_item_fired_and_terminal_changed",
        output_object="boundary_consumption_policy",
        does_not_support=(
            "create clean boundary", "override surviving error certificate",
        ),
    ),
)


NON_REPAIR_RUNTIME_OPERATIONS = {
    "emit_telemetry_only": "telemetry_only_no_detection_effect",
    "escalate_to_appeal": "telemetry_only_no_appeal_execution",
    "mark_candidate_ambiguous": "telemetry_only_no_detection_effect",
    "restore_audited_consensus": "pre_ledger_not_skill_proposable",
    "reclassify_candidate_state": "reserved_engine_operation",
    "require_route_replay": "reserved_engine_operation",
}


SKILL_CAPABILITY_REGISTRY = SkillCapabilityRegistry(
    _CAPABILITIES,
    non_repair_runtime_operations=NON_REPAIR_RUNTIME_OPERATIONS,
)


_GAP_FAILURE_NAMES = {
    "gold_false_negative_clean": "missing_error_candidate",
    "consensus_gate_blocked": "blocked_candidate_before_occurrence_ledger",
    "appeal_pending_blocked": "appeal_transition_not_executed",
    "appeal_failed_with_errors": "appeal_transition_failed",
    "provider_artifact_missing": "missing_provider_artifact",
}

_MISSING_CAPABILITY_BY_OWNER = {
    "candidate_trigger": "construct_candidate",
    "evidence_obligation": "construct_certificate",
    "candidate_to_verifier_route": "route_candidate_to_verifier",
    "atomic_verifier_instruction": "reverify_claim",
    "causal_link_test": "construct_causal_evidence",
    "earliest_origin_tiebreak": "construct_origin_evidence",
    "consensus_gate_policy": "restore_audited_consensus",
    "appeal_mechanism_policy": "execute_appeal_transition",
}

_MISSING_CAPABILITY_BY_FAILURE = {
    "consensus_gate_blocked": "restore_audited_consensus",
    "appeal_pending_blocked": "execute_appeal_transition",
    "appeal_failed_with_errors": "execute_appeal_transition",
}


def _render_transition(direction: str) -> str:
    value = str(direction or "")
    if "_to_" in value:
        before, after = value.split("_to_", 1)
        return f"{before} -> {after}"
    return value


def render_capability_contracts(
    modules: Sequence[str],
    *,
    direction: str = "",
    failure_shape: str = "",
) -> str:
    """Render exactly the registry entries an induction prompt may select."""
    capabilities = SKILL_CAPABILITY_REGISTRY.query(
        skill_layer="l1_provider_rule",
        modules=modules,
        direction=direction,
        failure_shape=failure_shape,
    )
    if not capabilities:
        return "(no executable capability; return capability_gap)"
    lines: list[str] = []
    for capability in capabilities:
        observable, effect = capability.expected_local_effect
        lines.extend((
            f"- {capability.module}/{capability.executable_operation}",
            f"  reads: {', '.join(capability.reads)}",
            f"  writes: {', '.join(capability.writes)}",
            f"  binding_requirements: {', '.join(capability.binding_requirements)}",
            f"  input_binding_domain: {capability.input_binding_domain}",
            f"  required_binding_fields: {', '.join(capability.required_binding_fields)}",
            f"  cardinality: {capability.cardinality}",
            f"  postcondition: {capability.fitness_postcondition}",
            f"  expected_local_effect: {observable}:{effect}",
            f"  does_not_support: {', '.join(capability.does_not_support)}",
        ))
    return "\n".join(lines)


__all__ = [
    "BINDING_CARDINALITIES",
    "BINDING_DOMAINS",
    "CapabilityGap",
    "L1_COURT_TRANSFORM_EXECUTOR",
    "L1_CONSTRUCTIVE_HARNESS_EXECUTOR",
    "L2_ADJUDICATION_POLICY_EXECUTOR",
    "NON_REPAIR_RUNTIME_OPERATIONS",
    "SKILL_CAPABILITY_REGISTRY",
    "SkillCapability",
    "SkillCapabilityRegistry",
    "render_capability_contracts",
]
