"""Machine-readable audit of what the current evolution runtime can change."""

from __future__ import annotations

from typing import Any

from detector.interventions import (
    FACT_NAMES,
    GUARD_ACTIONS,
    HOOKS,
    INTERVENTION_TYPES,
    SYSTEM_HOOKS,
    runner_declarations,
)
from detector.packet import PACKET_FEATURE_NAMES
from detector.predicate_dsl import (
    ALL_OPERATORS,
    FIELD_TYPES,
    MAX_CONDITIONS,
)
from detector.predicates import (
    PREDICATE_CONVICTION_VERDICTS,
    PREDICATE_VERDICTS,
)
from evolution.metamorphic import metamorphic_report
from evolution.sandbox import (
    DEFAULT_LIMITS,
    HARD_LIMITS,
    IMPORT_WHITELIST,
    admission_blockers,
    sandbox_capabilities,
)


def capability_surface_report() -> dict[str, Any]:
    """Describe the executable search surface, not the proposal prose space.

    The runtime now has one first-class capability-creation channel: an
    ``evidence_check`` may carry an inline typed-DSL predicate program. The
    program is validated at registry load and executed by the fixed DSL
    interpreter, so the observation vocabulary can grow during evolution
    without allowing arbitrary code execution. Stage hooks, guard actions and
    packet-feature routing remain closed until later capability channels are
    added.
    """
    direct_terminal_actions = sorted(
        set(GUARD_ACTIONS) - {"require_full_view_defense"}
    )
    fact_atoms = sum(len(values) for values in FACT_NAMES.values())
    typed_program_space = len(FIELD_TYPES) * len(ALL_OPERATORS)
    return {
        "schema_version": "evolution.capability_surface.v2",
        "classification": (
            "typed_program_synthesis_with_runtime_registration_and_control_flow_policy"
        ),
        "open_ended_self_evolution": False,
        "reason": (
            "the runtime can register new typed-DSL predicate programs (with "
            "AND-conjoined conditions) as evidence observations and as "
            "recall.trigger control-flow policies at admission time, and it "
            "can bind a caller-injected learned executor (typed_semantic) "
            "under an enforced evidence-only contract; it still cannot create "
            "LLM stage hooks, arbitrary sandboxed code, execution-graph "
            "rewrites, or weight updates, and it cannot itself produce a "
            "learned executor"
        ),
        "executable_inventory": {
            "intervention_types": sorted(INTERVENTION_TYPES),
            "hooks": sorted(HOOKS),
            # Every executor an intervention may name, with its declared
            # powers. Reported here because "what can the framework execute"
            # is exactly the question this report exists to answer, and the
            # runner space used to be invisible to it.
            "runners": runner_declarations(),
            # The admission criterion, executed rather than declared: each
            # registered runner's §15.2 invariances are actually run here, so
            # this section reports a verdict instead of a promise. It is the
            # other half of ``runners`` -- that one says what a runner may do,
            # this one says what it must not notice.
            "runner_admission": metamorphic_report(),
            # What the Level B sandbox can actually enforce on this platform,
            # declared. The *probed* reading (which makes a child violate each
            # limit) belongs in a report, not in a per-call audit: it launches
            # processes. ``admission_blockers`` is reported because an empty
            # list is the difference between "components may run" and "they
            # must be refused".
            "sandbox": {
                "capabilities": sandbox_capabilities(verify=False),
                "admission_blockers": admission_blockers(),
                "soft_defaults": dict(DEFAULT_LIMITS),
                "hard_ceiling": dict(HARD_LIMITS),
                "import_whitelist": list(IMPORT_WHITELIST),
                "component_kinds_served": [
                    "field parsing", "argument binding comparison",
                    "structured contract checking", "event dependency extraction",
                ],
            },
            "system_prompt_hooks": sorted(SYSTEM_HOOKS),
            "packet_features": sorted(PACKET_FEATURE_NAMES),
            "nonempty_packet_feature_conjunctions": (
                2 ** len(PACKET_FEATURE_NAMES) - 1
            ),
            "static_fact_names": len(FACT_NAMES),
            "static_fact_atoms": fact_atoms,
            "static_predicates": sorted(PREDICATE_VERDICTS),
            "static_predicate_count": len(PREDICATE_VERDICTS),
            "conviction_predicates": sorted(PREDICATE_CONVICTION_VERDICTS),
            "guard_actions": sorted(GUARD_ACTIONS),
            "direct_terminal_actions": direct_terminal_actions,
        },
        "capability_creation": {
            "inline_predicate_programs": True,
            "predicate_program_schema": "detector.predicate_program.v1",
            "predicate_program_runner": "typed_dsl",
            "typed_fields": sorted(FIELD_TYPES),
            "typed_field_count": len(FIELD_TYPES),
            "typed_operators": sorted(ALL_OPERATORS),
            "typed_operator_count": len(ALL_OPERATORS),
            "typed_condition_conjunction": True,
            "max_conditions_per_rule": MAX_CONDITIONS,
            "coarse_cartesian_program_space": typed_program_space,
            "new_predicate_names": "unbounded_within_evo_identifier_regex",
            "new_fact_types": (
                "one fact per evolved predicate, values from its declared verdicts"
            ),
            "new_executor_runtime_registration": True,
            "hybrid_proposer_typed_dsl_fallback": True,
        },
        "generative_freedom": {
            "prompt_directive_text": True,
            "prompt_not_when_text": True,
            "new_predicate_program": True,
            "new_executor": True,
            # Distinct from ``new_executor``: the framework can now *name* a
            # learned executor (typed_semantic) and enforce its constraints,
            # but it does not contain one. An executor is injected by the
            # caller, so this is a capability to bind, not one to generate.
            "name_a_learned_executor": True,
            "generate_a_learned_executor": False,
            "new_fact_type": True,
            "new_hook": False,
            "new_guard_action": False,
            "new_control_flow_policy": True,
            "typed_dsl_template_proposer": True,
            "execution_graph_rewrite": False,
            # P2.2: bounded, not open. A component is a pure function with a
            # fixed parameter schema, a whitelisted import set, no filesystem
            # or network, and OS-enforced CPU/time/memory limits -- and it is
            # evidence-only. Reporting this as ``True`` alongside those limits
            # is the honest reading; reporting it as ``False`` would understate
            # what can now run, and reporting it without them would overstate
            # what it can do.
            "sandboxed_code_generation": True,
            "sandboxed_code_generation_scope": (
                "pure function, fixed parameter schema, import whitelist, no "
                "filesystem or network access, OS-enforced cpu/time/memory "
                "limits, evidence-only (may never convict)"
            ),
            "model_weight_update": False,
            # P2.3. Distinct from ``new_executor``: the framework can refuse an
            # executor, which is a different power from being able to run one.
            "new_executor_admission_criterion": True,
        },
        "causal_limits": {
            "evidence_check_changes_terminal_alone": False,
            "text_boundary_effect": "indirect_through_existing_llm_stage",
            "decision_guard_effect": "closed_action_vocabulary",
            "recall_trigger_effect": "forces_existing_llm_recall_net",
            "capability_gap_can_expand_runtime": True,
            "capability_gap_can_expand_observation": True,
            "capability_gap_can_expand_terminal_effects": True,
        },
        # §23.7's checklist, with each item's status and where it was closed.
        # A bare list of strings could not distinguish "outstanding" from
        # "satisfied", and this report is quoted by an audit: three of these
        # were closed by P2.1/P2.2 and the flat form made them read as open.
        "required_for_open_ended_evolution": [
            {
                "requirement": "typed declarative predicate programs",
                "satisfied": True,
                "closed_by": "inline detector.predicate_program.v1 (typed_dsl)",
            },
            {
                "requirement": "typed control-flow policy artifacts",
                "satisfied": True,
                "closed_by": "recall_trigger carrying an inline typed-DSL program",
            },
            {
                "requirement": "sandboxed new executors with property and "
                               "metamorphic tests",
                "satisfied": True,
                "closed_by": (
                    "P2.2 evolution.sandbox (Level B) + P2.3 "
                    "evolution.metamorphic admission gate"
                ),
            },
            {
                "requirement": "typed execution-graph artifacts",
                "satisfied": False,
                "closed_by": "",
            },
            {
                "requirement": "immutable capability artifact store",
                "satisfied": False,
                "closed_by": "",
            },
        ],
        "open_ended_evolution_blockers": [
            "typed execution-graph artifacts",
            "immutable capability artifact store",
        ],
    }


__all__ = ["capability_surface_report"]
