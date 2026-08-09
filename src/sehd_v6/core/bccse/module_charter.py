"""Module charters for the eight L1 skill modules.

Modules historically carried only a stage mapping, so reflection proposals
were routinely routed to the wrong module. The charter front-loads
attribution: every module declares which provider-output surfaces it may
influence, which surfaces it must never touch, and in-scope vs out-of-scope
failure examples, so the reflection prompt can constrain proposals at
generation time instead of rejecting them at the validation gate.

Surface boundaries here mirror two code facts:
- ``genome.validate_runtime_rule`` forbids rules from touching coverage
  state, trigger source, CRS, path state, and gold/ground-truth surfaces;
  the forbidden lists below restate that ownership boundary per module.
- ``paca_tcl.skill_integration.ROUTE_MODULE_IDS`` fixes which provider
  routes each module is injected into, so the influenceable surfaces only
  cover output that those routes can actually carry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vocabulary import ALLOWED_VERDICTS
from .genome import MODULE_IDS, MODULE_STAGE


@dataclass(frozen=True)
class ModuleCharter:
    """Responsibility boundary of one L1 skill module.

    allowed_verdicts is sourced from genome.ALLOWED_VERDICTS so verdict
    permission has exactly one owner; the charter never re-declares it.
    """

    module_id: str
    stage: str
    influenceable_surfaces: tuple[str, ...]
    forbidden_surfaces: tuple[str, ...]
    allowed_verdicts: frozenset[str]
    in_scope_examples: tuple[str, ...]
    out_of_scope_examples: tuple[str, ...]


def _verdicts(module_id: str) -> frozenset[str]:
    # Single source of truth: the genome gate owns verdict permission.
    return ALLOWED_VERDICTS.get(module_id, frozenset())


MODULE_CHARTERS: dict[str, ModuleCharter] = {
    "candidate_trigger": ModuleCharter(
        module_id="candidate_trigger",
        stage=MODULE_STAGE["candidate_trigger"],
        influenceable_surfaces=(
            "candidate discovery: whether to create an error candidate at the "
            "earliest observable origin of an unsupported claim or action",
            "candidate field completeness: origin event and claimed-content "
            "fields attached to a newly created candidate",
        ),
        forbidden_surfaces=(
            "verification certificates and their quality",
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "prosecution_coverage and defense_coverage",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("candidate_trigger"),
        in_scope_examples=(
            "A hallucinated tool result that influences the final answer "
            "produces no candidate at its earliest observable origin.",
            "A candidate is created but its origin event field is empty or "
            "points to an event not observable in the trajectory.",
        ),
        out_of_scope_examples=(
            "A candidate is discovered correctly but its certificate is "
            "wrongly rejected; that failure belongs to the "
            "atomic_verifier_instruction module (stage_2_verification).",
            "A correctly verified candidate is later mis-adjudicated at the "
            "final verdict; that belongs to the abstention_rule module or "
            "the L2 adjudication policy layer.",
        ),
    ),
    "evidence_obligation": ModuleCharter(
        module_id="evidence_obligation",
        stage=MODULE_STAGE["evidence_obligation"],
        influenceable_surfaces=(
            "prosecution evidence obligation judgment: whether cited evidence "
            "proves norm, breach, agent control, impact, and counterfactual "
            "link for one error candidate",
            "prosecution coverage declaration derived from evidence "
            "sufficiency on the prosecution routes",
        ),
        forbidden_surfaces=(
            "defense certificates and defense coverage",
            "candidate discovery or candidate-to-verifier routing",
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("evidence_obligation"),
        in_scope_examples=(
            "The evidence obligation is judged satisfied although a required "
            "causal-chain field is missing from the cited evidence.",
            "An error candidate is supported although the cited evidence "
            "covers only a subset of the obligation fields.",
        ),
        out_of_scope_examples=(
            "A defense certificate is wrongly issued although the agent "
            "behavior is not clean; that failure belongs to the "
            "clean_defense_boundary module (stage_2_verification).",
            "A candidate is sent to the wrong verifier before any evidence "
            "is examined; that belongs to the candidate_to_verifier_route "
            "module.",
        ),
    ),
    "candidate_to_verifier_route": ModuleCharter(
        module_id="candidate_to_verifier_route",
        stage=MODULE_STAGE["candidate_to_verifier_route"],
        influenceable_surfaces=(
            "candidate-to-verifier routing decision: which verifier examines "
            "a candidate based on its type (claim, action, omission, or "
            "adoption)",
        ),
        forbidden_surfaces=(
            "certificate content and certificate quality",
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "prosecution_coverage and defense_coverage",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("candidate_to_verifier_route"),
        in_scope_examples=(
            "A claim-type candidate is routed to an action verifier, so the "
            "verifier tests the wrong proposition.",
            "A candidate is routed to no verifier at all and is silently "
            "dropped before verification.",
        ),
        out_of_scope_examples=(
            "A candidate reaches the correct verifier but the verification "
            "instruction is malformed; that failure belongs to the "
            "atomic_verifier_instruction module (stage_2_verification).",
        ),
    ),
    "atomic_verifier_instruction": ModuleCharter(
        module_id="atomic_verifier_instruction",
        stage=MODULE_STAGE["atomic_verifier_instruction"],
        influenceable_surfaces=(
            "single-candidate verification instruction: one atomic, "
            "trajectory-evidence-bound test per candidate",
            "certificate quality: whether the certificate cites the "
            "trajectory events that establish or refute the candidate",
        ),
        forbidden_surfaces=(
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "defense coverage declaration (owned by clean_defense_boundary)",
            "candidate discovery and candidate-to-verifier routing",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("atomic_verifier_instruction"),
        in_scope_examples=(
            "A verifier tests two candidates in one pass, so the resulting "
            "certificate cannot be attributed to a single candidate.",
            "A certificate is issued without citing the trajectory events "
            "that establish the claimed causal chain.",
        ),
        out_of_scope_examples=(
            "Certificates are all valid but the final adjudication still "
            "picks the wrong verdict; that belongs to the abstention_rule "
            "module or the L2 adjudication policy layer.",
        ),
    ),
    "clean_defense_boundary": ModuleCharter(
        module_id="clean_defense_boundary",
        stage=MODULE_STAGE["clean_defense_boundary"],
        influenceable_surfaces=(
            "defense certificate issuance for repaired, authorized, or "
            "environment-forced deviations",
            "defense coverage assessment on the defense routes",
            "verdict clean when a complete clean defense defeats every "
            "error candidate",
        ),
        forbidden_surfaces=(
            "prosecution certificates and prosecution coverage",
            "error or unresolved verdict emission",
            "coverage_state and trigger_source",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("clean_defense_boundary"),
        in_scope_examples=(
            "A tool failure fully repaired before downstream impact is "
            "still prosecuted because the defense boundary did not mark "
            "it clean.",
            "A deviation authorized by the user is attributed to the agent "
            "instead of being defended away.",
        ),
        out_of_scope_examples=(
            "The prosecution evidence obligation is wrongly judged complete; "
            "that failure belongs to the evidence_obligation module "
            "(stage_2_verification).",
            "Defense coverage is complete but the final verdict stays "
            "unresolved because the court misconsumes the boundary finding; "
            "that belongs to the L2 boundary_consumption_policy artifact.",
        ),
    ),
    "causal_link_test": ModuleCharter(
        module_id="causal_link_test",
        stage=MODULE_STAGE["causal_link_test"],
        influenceable_surfaces=(
            "causal link judgment between a candidate origin and its "
            "downstream impact, including local negation: the causal link "
            "does not hold (weakens prosecution without emitting a verdict)",
        ),
        forbidden_surfaces=(
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "prosecution_coverage and defense_coverage",
            "candidate discovery and origin selection",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("causal_link_test"),
        in_scope_examples=(
            "An error with no cited propagation to the benchmark impact is "
            "still treated as causally responsible.",
            "Correcting the origin would have prevented the incorrect "
            "result, but the origin is not treated as causal.",
        ),
        out_of_scope_examples=(
            "A causal link is correctly negated but the final verdict stays "
            "clean instead of unresolved; that belongs to the abstention_rule "
            "module or the L2 adjudication policy layer.",
        ),
    ),
    "earliest_origin_tiebreak": ModuleCharter(
        module_id="earliest_origin_tiebreak",
        stage=MODULE_STAGE["earliest_origin_tiebreak"],
        influenceable_surfaces=(
            "origin tiebreak: selecting the earliest cited event when "
            "multiple origins are causally sufficient",
        ),
        forbidden_surfaces=(
            "causal link validity itself (owned by causal_link_test)",
            "final verdict emission (clean/error/unresolved)",
            "coverage_state and trigger_source",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("earliest_origin_tiebreak"),
        in_scope_examples=(
            "Two origins are causally sufficient and the later one is "
            "selected as the responsible origin.",
            "The tiebreak selects an origin that is not cited anywhere in "
            "the trajectory.",
        ),
        out_of_scope_examples=(
            "The tiebreak picks the wrong origin because the causal link of "
            "the preferred origin was wrongly affirmed; that failure belongs "
            "to the causal_link_test module (stage_3_causal_court).",
        ),
    ),
    "abstention_rule": ModuleCharter(
        module_id="abstention_rule",
        stage=MODULE_STAGE["abstention_rule"],
        influenceable_surfaces=(
            "the boundary-route finding (state and rationale): a conditional "
            "evidence standard — when the decisive-verdict bar should lower "
            "or raise based on trajectory-observable facts — reaches the "
            "proof court as the case_boundary_finding inside NEUTRAL_EVIDENCE "
            "and constrains evidence construction; the final verdict stays "
            "with the deterministic proof court and can never be overwritten "
            "by this rule",
        ),
        forbidden_surfaces=(
            "re-verification of evidence or re-issuing certificates",
            "coverage_state and trigger_source",
            "CRS score or weight changes",
            "gold or ground-truth labels",
        ),
        allowed_verdicts=_verdicts("abstention_rule"),
        in_scope_examples=(
            "Neither a complete causal error proof nor a complete clean "
            "defense is established, yet the verdict is not unresolved.",
            "A complete clean defense defeats every error candidate, but "
            "the verdict remains error.",
        ),
        out_of_scope_examples=(
            "The verdict edge input is correct but the boundary finding is "
            "misconsumed downstream by the consensus gate or coverage "
            "validity policy; that belongs to the L2 adjudication policy "
            "layer (consensus_gate_policy / coverage_validity_policy).",
        ),
    ),
}


# Closed action-opcode vocabulary per module. Each opcode carries the
# English template the provider actually sees (rendered by ``render_rule``),
# so an action is enumerable, validatable and traceable instead of free
# text. Every generated action uses ``"<opcode>: <target>"`` so the exact
# provider output field operated on is explicit and traceable.
MODULE_ACTION_OPCODES: dict[str, tuple[tuple[str, str], ...]] = {
    "candidate_trigger": (
        ("emit_candidate", "emit a candidate"),
        ("suppress_candidate", "suppress the candidate"),
        ("emit_origin_census", "emit the origin census entry"),
    ),
    "evidence_obligation": (
        ("require_evidence_field", "require the cited evidence field"),
        ("populate_evidence_field", "populate the cited evidence field"),
        ("emit_error_certificate", "emit an error certificate"),
        ("keep_coverage_incomplete", "keep the coverage incomplete"),
    ),
    "candidate_to_verifier_route": (
        ("route_candidate_to_verifier", "route the candidate to the verifier"),
        ("qualify_candidate", "qualify the candidate for verification"),
        ("hold_candidate_qualification", "hold the candidate qualification"),
    ),
    "atomic_verifier_instruction": (
        ("verify_violation", "verify the violation against the norm"),
        ("quarantine_certificate", "quarantine the certificate"),
        ("emit_certificate", "emit the certificate"),
        ("require_evidence_field", "require the cited evidence field"),
    ),
    "clean_defense_boundary": (
        ("emit_defense_certificate", "emit a defense certificate"),
        ("emit_boundary_finding", "emit the boundary finding"),
        ("require_evidence_field", "require the cited evidence field"),
    ),
    "causal_link_test": (
        ("test_causal_link", "test the causal link"),
        ("reject_non_causal_origin", "reject the non-causal origin"),
        ("populate_impact_chain", "populate the impact and recovery chain"),
    ),
    "earliest_origin_tiebreak": (
        ("select_earliest_origin", "select the earliest responsible origin"),
        ("suppress_later_origin", "suppress the later origin"),
    ),
    "abstention_rule": (
        ("emit_boundary_finding", "emit the boundary finding"),
    ),
}

MODULE_FORBIDDEN_ACTION_TERMS: dict[str, tuple[str, ...]] = {
    "candidate_trigger": (
        "defense certificate", "prosecution certificate", "final verdict",
        "return clean", "return error", "return unresolved",
    ),
    "evidence_obligation": (
        "defense coverage", "defense certificate", "final verdict",
        "return clean", "return error", "return unresolved",
    ),
    "candidate_to_verifier_route": (
        "certificate", "coverage", "final verdict", "return clean",
        "return error", "return unresolved",
    ),
    "atomic_verifier_instruction": (
        "defense coverage", "final verdict", "return clean", "return error",
        "return unresolved",
    ),
    "clean_defense_boundary": (
        "prosecution coverage", "prosecution certificate", "return error",
        "return unresolved",
    ),
    "causal_link_test": (
        "defense coverage", "final verdict", "return clean", "return error",
        "return unresolved",
    ),
    "earliest_origin_tiebreak": (
        "certificate", "coverage", "final verdict", "return clean",
        "return error", "return unresolved",
    ),
    "abstention_rule": (
        "candidate discovery", "defense certificate", "prosecution certificate",
        "verification certificate",
    ),
}


# Per-module runtime-observable output fields. Single source of truth for
# both the action-opcode ``<target>`` validation and the reflection
# prompt's expected-effect contract (``observable_field`` must belong to the
# selected module).
MODULE_OBSERVABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "candidate_trigger": ("candidate_count",),
    "evidence_obligation": (
        "prosecution_coverage", "prosecution_certificates",
        "causal_field_coverage", "target_local_score",
    ),
    "candidate_to_verifier_route": (
        "prosecution_coverage", "prosecution_certificates",
        "target_local_score",
    ),
    "atomic_verifier_instruction": (
        "quarantined_certificates", "causal_field_coverage",
        "target_local_score",
    ),
    "clean_defense_boundary": (
        "defense_coverage", "defense_certificates", "target_local_score",
    ),
    "causal_link_test": (
        "causal_field_coverage", "localization_status", "target_local_score",
        "responsible_step",
    ),
    "earliest_origin_tiebreak": (
        "responsible_step", "localization_status", "target_local_score",
    ),
    "abstention_rule": ("state", "trigger", "target_local_score"),
}


# Executable opcode/field grammar.  Module ownership alone is insufficient:
# an output field can be present in a route schema while still being the wrong
# operand for a particular operation (for example, a certificate quarantine
# operates on certificate_id, never on evidence_refs).
MODULE_OPERATION_TARGETS: dict[str, dict[str, tuple[str, ...]]] = {
    "candidate_trigger": {
        "emit_candidate": ("candidate",),
        "suppress_candidate": ("candidate", "candidate_id"),
        "emit_origin_census": (
            "origin_hypothesis_event_id", "trigger_event_id", "observation",
        ),
    },
    "evidence_obligation": {
        "require_evidence_field": (
            "consumption_evidence_refs", "impact_evidence_refs",
            "recovery_evidence_refs", "norm_evidence_refs",
            "violation_evidence_refs", "agent_control_evidence_refs",
            "benchmark_causal_evidence_refs", "counterfactual_evidence_refs",
        ),
        "populate_evidence_field": (
            "consumption_evidence_refs", "impact_evidence_refs",
            "recovery_evidence_refs", "norm_evidence_refs",
            "violation_evidence_refs", "agent_control_evidence_refs",
            "benchmark_causal_evidence_refs", "counterfactual_evidence_refs",
        ),
        "emit_error_certificate": ("certificate_id",),
        "keep_coverage_incomplete": ("coverage_state",),
    },
    "candidate_to_verifier_route": {
        "route_candidate_to_verifier": (
            "violation_state", "actor_state", "boundary_state",
        ),
        "qualify_candidate": (
            "violation_state", "actor_state", "boundary_state",
        ),
        "hold_candidate_qualification": (
            "violation_state", "actor_state", "boundary_state",
        ),
    },
    "atomic_verifier_instruction": {
        "verify_violation": (
            "coverage_state", "breach_type", "benchmark_causal_status",
            "violation_evidence_refs", "agent_control_evidence_refs",
            "benchmark_causal_evidence_refs", "counterfactual_evidence_refs",
        ),
        "quarantine_certificate": ("certificate_id",),
        "emit_certificate": ("certificate_id",),
        "require_evidence_field": (
            "positive_evidence_refs", "actor_evidence_refs",
            "boundary_evidence_refs", "norm_evidence_refs",
            "violation_evidence_refs", "agent_control_evidence_refs",
            "impact_evidence_refs", "recovery_evidence_refs",
            "benchmark_causal_evidence_refs", "counterfactual_evidence_refs",
            "evidence_refs",
        ),
    },
    "clean_defense_boundary": {
        "emit_defense_certificate": ("certificate_id",),
        "emit_boundary_finding": ("state", "responsible_event_id"),
        "require_evidence_field": (
            "evidence_refs", "target_candidate_id", "target_origin_event_id",
        ),
    },
    "causal_link_test": {
        "test_causal_link": (
            "benchmark_causal_evidence_refs", "causal_sufficiency_score",
            "is_root_cause",
        ),
        "reject_non_causal_origin": ("responsible_event_id", "is_root_cause"),
        "populate_impact_chain": (
            "consumption_evidence_refs", "impact_evidence_refs",
            "recovery_evidence_refs", "benchmark_causal_evidence_refs",
        ),
    },
    "earliest_origin_tiebreak": {
        "select_earliest_origin": ("responsible_event_id",),
        "suppress_later_origin": ("responsible_event_id",),
    },
    "abstention_rule": {
        "emit_boundary_finding": (
            "state", "responsible_event_id", "evidence_refs",
        ),
    },
}


def validate_action_surface(module_id: str, action: str) -> tuple[bool, str]:
    """Check that a slot action names a closed opcode of the target module.

    The action must be ``"<opcode>: <target>"``. The opcode belongs to the
    module's ``MODULE_ACTION_OPCODES`` and the target belongs to that exact
    operation in ``MODULE_OPERATION_TARGETS``. Module-level schema ownership
    alone is not enough. Forbidden surface terms still fail first.
    """
    lowered = " ".join(str(action or "").lower().split())
    for marker in MODULE_FORBIDDEN_ACTION_TERMS.get(module_id, ()):
        if marker in lowered:
            return False, f"forbidden_action_surface:{marker}"
    opcodes = MODULE_ACTION_OPCODES.get(module_id, ())
    opcode_part, sep, target_part = lowered.partition(":")
    opcode_part = opcode_part.strip()
    if not any(opcode_part == opcode for opcode, _template in opcodes):
        return False, f"action_opcode_unowned:{opcode_part or 'missing'}"
    target = target_part.strip()
    operation_targets = MODULE_OPERATION_TARGETS.get(module_id, {}).get(
        opcode_part, (),
    )
    if not sep and operation_targets:
        return False, f"action_target_required:{opcode_part}"
    if sep and not target:
        return False, "action_target_unowned:empty"
    if sep:
        if target not in operation_targets:
            return False, (
                f"action_target_not_owned_by_operation:{opcode_part}:{target}"
            )
    return True, ""


def charter_for(module_id: str) -> ModuleCharter:
    """Return the charter for a registered module, else raise ValueError."""
    charter = MODULE_CHARTERS.get(module_id)
    if charter is None:
        raise ValueError(f"unknown module_id: {module_id}")
    return charter


def render_charter_text(module_id: str) -> str:
    """Render one charter as an English text block for prompt injection."""
    charter = charter_for(module_id)
    verdicts = ", ".join(sorted(charter.allowed_verdicts)) or "none"
    lines = [
        f"MODULE: {charter.module_id}",
        f"STAGE: {charter.stage}",
        "INFLUENCEABLE SURFACES:",
    ]
    lines.extend(f"  - {surface}" for surface in charter.influenceable_surfaces)
    lines.append("FORBIDDEN SURFACES:")
    lines.extend(f"  - {surface}" for surface in charter.forbidden_surfaces)
    lines.append(f"ALLOWED VERDICTS: {verdicts}")
    lines.append("IN-SCOPE FAILURE EXAMPLES:")
    lines.extend(f"  - {example}" for example in charter.in_scope_examples)
    lines.append("OUT-OF-SCOPE FAILURE EXAMPLES:")
    lines.extend(f"  - {example}" for example in charter.out_of_scope_examples)
    return "\n".join(lines)


def render_all_charters() -> str:
    """Render all charters in MODULE_IDS order for the reflection prompt."""
    return "\n\n".join(render_charter_text(mid) for mid in MODULE_IDS)
