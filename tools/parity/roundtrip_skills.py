"""Round-trip and cross-package equivalence checks for the skills layer.

Phase 2 parity harness: the new ``sehd_rise.skills`` package must be
serialization-compatible and judgment-identical with the frozen
``src.sehd_v6.core.bccse`` vocabulary/genome modules. Checks:

1. Registry: ``L2_REGISTRY.to_dict()`` / ``content_hash`` identical;
   ``from_dict(to_dict(x))`` round-trip preserves the payload.
2. L1 genome: baseline and slot-contract genomes serialize identically;
   ``from_dict(to_dict(x))`` round-trip preserves content and execution
   hashes; ``apply_edit`` produces identical lineage on both sides.
3. L2 policy genome: payloads (items + pending queue + evolved registry)
   round-trip; ``validate_policy_genome_items`` agrees on legal/illegal
   payloads.
4. Judgment parity: ``validate_edit`` / ``validate_policy_edit`` return the
   same (ok, reason) on a shared set of legal and illegal samples; registry
   edit application produces identical audit rows.

Run from the repo root: python tools/parity/roundtrip_skills.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sehd_v6.core.bccse import (
    genome as old_genome,
    policy_genome as old_policy,
    rule_contract as old_rule,
    vocabulary_registry as old_registry,
)
from sehd_rise.skills import (
    action as new_action,
    genome as new_genome,
    policy as new_policy,
    rule as new_rule,
)
from sehd_rise.skills.vocabulary import registry as new_registry

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ok   {name}")
    else:
        message = f"FAIL {name}" + (f" — {detail}" if detail else "")
        print(f"  {message}")
        FAILURES.append(message)


def equal(name: str, left: Any, right: Any) -> None:
    check(name, left == right, f"\n    old={left!r}\n    new={right!r}")


# ============================================================
# 1. Registry
# ============================================================
print("registry:")
equal("L2_REGISTRY.to_dict", old_registry.L2_REGISTRY.to_dict(), new_registry.L2_REGISTRY.to_dict())
equal("L2_REGISTRY.content_hash", old_registry.L2_REGISTRY.content_hash, new_registry.L2_REGISTRY.content_hash)
old_round = old_registry.VocabularyRegistry.from_dict(old_registry.L2_REGISTRY.to_dict())
new_round = new_registry.VocabularyRegistry.from_dict(new_registry.L2_REGISTRY.to_dict())
equal("registry round-trip to_dict", old_round.to_dict(), new_round.to_dict())
equal("registry round-trip content_hash", old_round.content_hash, new_round.content_hash)

# Evolved registry: apply the same audited edits on both sides and compare.
evolved_old = old_registry.L2_REGISTRY
evolved_new = new_registry.L2_REGISTRY
for dsl_target in ("quarantined_error_count_nonzero",):
    evolved_old = old_registry.apply_registry_edit(
        evolved_old, operation="register_predicate", target=dsl_target,
        dsl="quarantined_error_count > 0", reason_codes=("parity",),
    )
    evolved_new = new_registry.apply_registry_edit(
        evolved_new, operation="register_predicate", target=dsl_target,
        dsl="quarantined_error_count > 0", reason_codes=("parity",),
    )
evolved_old = old_registry.apply_registry_edit(
    evolved_old, operation="register_action", target="mark_candidate_ambiguous_instance",
    operator="mark_candidate_ambiguous", reason_codes=("parity",),
)
evolved_new = new_registry.apply_registry_edit(
    evolved_new, operation="register_action", target="mark_candidate_ambiguous_instance",
    operator="mark_candidate_ambiguous", reason_codes=("parity",),
)
equal("evolved registry to_dict", evolved_old.to_dict(), evolved_new.to_dict())
equal("evolved registry audit", [edit.to_dict() for edit in evolved_old.audit],
      [edit.to_dict() for edit in evolved_new.audit])

# ============================================================
# 2. L1 genome
# ============================================================
print("l1 genome:")
old_baseline = old_genome.create_baseline_genome()
new_baseline = new_genome.create_baseline_genome()
equal("baseline genome to_dict", old_baseline.to_dict(), new_baseline.to_dict())
equal("baseline content_hash", old_baseline.content_hash, new_baseline.content_hash)
equal("baseline execution_hash", old_baseline.execution_hash, new_baseline.execution_hash)

# Round-trip both through their own from_dict and compare the results.
old_rt = old_genome.SkillGenome.from_dict(old_baseline.to_dict())
new_rt = new_genome.SkillGenome.from_dict(new_baseline.to_dict())
equal("baseline round-trip to_dict", old_rt.to_dict(), new_rt.to_dict())
equal("baseline round-trip content_hash", old_rt.content_hash, new_rt.content_hash)

# Slot-contract genome: build one canonical payload, hydrate on both sides.
condition_spec = {
    "schema_version": "sehd.condition_spec.v2",
    "feature_source": "provider_input",
    "feature": "candidate_kind",
    "operator": "contains",
    "value_type": "string",
    "value": "unsupported_claim",
    "scope": "action_spec_routes",
    "evidence_binding": "pre_call_projection",
    "combinator": "atom",
    "binding": "route_scoped",
}
slots = {
    "condition": "an unsupported claim reaches the impact events without cited evidence",
    "action": "reject_non_causal_origin: responsible_event_id",
    "evidence_anchor": "causal link rejected on missing impact evidence",
}
spec_old = old_rule.RuleSpec(
    condition=slots["condition"], action=slots["action"],
    evidence_anchor=slots["evidence_anchor"],
    module_id="causal_link_test", direction="deny_causal",
)
rule_text = old_rule.render_rule(spec_old)
action_spec = old_genome.compile_action_spec(
    "causal_link_test", slots["action"],
).to_dict() if hasattr(old_genome, "compile_action_spec") else new_action.compile_action_spec(
    "causal_link_test", slots["action"],
).to_dict()
old_slot_edit = old_genome.SkillEdit(
    edit_id="edit_p2_slot", target_module="causal_link_test",
    operation=old_genome.SkillOp.ADD_RULE, new_content=rule_text,
    rationale="parity", source_case_id="case_p2",
    feature_description="causal link rejected when impact evidence missing",
    hallucination_type="unsupported_claim", evolution_direction="process_repair",
    type_confidence=0.9, routing_allowed_modules=("causal_link_test",),
    expected_effect="responsible_step must removed when the intervention is enforced",
    expected_effect_contract={
        "observable_field": "responsible_step", "expected_direction": "removed",
        "expected_scope": "all_cases",
    },
    rule_slots=dict(slots), action_spec=action_spec, condition_spec=condition_spec,
)
new_slot_edit = new_genome.SkillEdit(
    edit_id="edit_p2_slot", target_module="causal_link_test",
    operation=new_genome.SkillOp.ADD_RULE, new_content=rule_text,
    rationale="parity", source_case_id="case_p2",
    feature_description="causal link rejected when impact evidence missing",
    hallucination_type="unsupported_claim", evolution_direction="process_repair",
    type_confidence=0.9, routing_allowed_modules=("causal_link_test",),
    expected_effect="responsible_step must removed when the intervention is enforced",
    expected_effect_contract={
        "observable_field": "responsible_step", "expected_direction": "removed",
        "expected_scope": "all_cases",
    },
    rule_slots=dict(slots), action_spec=action_spec, condition_spec=condition_spec,
)
check(
    "slot edit is legal (old)", old_genome.validate_edit(old_slot_edit, old_baseline) == (True, ""),
    str(old_genome.validate_edit(old_slot_edit, old_baseline)),
)
check(
    "slot edit is legal (new)", new_genome.validate_edit(new_slot_edit, new_baseline) == (True, ""),
    str(new_genome.validate_edit(new_slot_edit, new_baseline)),
)

old_child = old_genome.apply_edit(old_baseline, old_slot_edit)
new_child = new_genome.apply_edit(new_baseline, new_slot_edit)
# genome_id embeds a uuid4; normalize it so the rest of the lineage is comparable.
normalize: Callable[[Any], dict[str, Any]] = lambda genome: {
    **genome.to_dict(), "genome_id": "<normalized>",
}
equal("slot edit apply_edit lineage", normalize(old_child), normalize(new_child))
equal("slot edit content_hash", old_child.content_hash, new_child.content_hash)
equal("slot edit execution_hash", old_child.execution_hash, new_child.execution_hash)

# Round-trip the evolved genome payload on both sides (genome_id embeds a
# fresh uuid4 per package; normalize it like the lineage comparison above).
old_evolved_rt = old_genome.SkillGenome.from_dict(old_child.to_dict())
new_evolved_rt = new_genome.SkillGenome.from_dict(new_child.to_dict())
equal("evolved genome round-trip", normalize(old_evolved_rt), normalize(new_evolved_rt))
equal("evolved genome round-trip hashes",
      (old_evolved_rt.content_hash, old_evolved_rt.execution_hash),
      (new_evolved_rt.content_hash, new_evolved_rt.execution_hash))

# ============================================================
# 3. L2 policy genome
# ============================================================
print("l2 policy genome:")
policy_payload = {
    "genome_id": "policy_parity_v1",
    "items": [
        {
            "item_id": "item_1",
            "precondition": "all_candidates_quarantined",
            "action": "downgrade_coverage",
            "params": {"side": "prosecution", "to_state": "incomplete"},
            "rationale": "parity baseline item",
            "source_case_id": "case_p2",
            "is_baseline": False,
        }
    ],
    "item_meta": [
        {"fitness_status": "weak", "source_case_id": "case_p2", "created_at_generation": 1}
    ],
    "parent_hash": None,
    "generation": 1,
    "edits_applied": [
        {
            "edit_id": "edit_p1", "operation": "add_item", "item_index": None,
            "new_item": {
                "item_id": "item_1", "precondition": "all_candidates_quarantined",
                "action": "downgrade_coverage",
                "params": {"side": "prosecution", "to_state": "incomplete"},
                "rationale": "parity", "source_case_id": "case_p2", "is_baseline": False,
            },
            "rationale": "parity", "source_case_id": "case_p2",
        }
    ],
    "metadata": {"parity": True},
    "pending_queue": [
        {
            "subject_id": "quarantined_defense_count_nonzero",
            "layer": "l2_registry",
            "proposal": {"operation": "register_predicate", "target": "quarantined_defense_count_nonzero"},
            "registry_dependencies": [],
            "source_evidence": {"case_id": "case_p2"},
            "required_validation": "registry_enable",
            "created_sequence": 0,
            "status": "pending",
            "observed_case_ids": ["case_p2"],
        }
    ],
    "registry": evolved_old.to_dict(),
}
old_policy_genome = old_policy.PolicyGenome.from_dict(policy_payload)
new_policy_genome = new_policy.PolicyGenome.from_dict(policy_payload)
equal("policy genome to_dict", old_policy_genome.to_dict(), new_policy_genome.to_dict())
equal("policy content_hash", old_policy_genome.content_hash, new_policy_genome.content_hash)
equal("policy round-trip", old_policy.PolicyGenome.from_dict(old_policy_genome.to_dict()).to_dict(),
      new_policy.PolicyGenome.from_dict(new_policy_genome.to_dict()).to_dict())

# ============================================================
# 4. Judgment parity
# ============================================================
print("judgment parity:")
samples_old: list[tuple[str, Callable[[], tuple[bool, str]]]] = []
samples_new: list[tuple[str, Callable[[], tuple[bool, str]]]] = []


def judge_samples(
    name: str,
    old_fn: Callable[[], tuple[bool, str]],
    new_fn: Callable[[], tuple[bool, str]],
) -> None:
    old_result = old_fn()
    new_result = new_fn()
    equal(name, old_result, new_result)


# --- validate_edit samples ---
def free_add(module_id: str, text: str, baseline_old: Any, baseline_new: Any) -> None:
    judge_samples(
        f"validate_edit free add [{module_id}]",
        lambda: old_genome.validate_edit(
            old_genome.SkillEdit(edit_id="e", target_module=module_id,
                                 operation=old_genome.SkillOp.ADD_RULE, new_content=text),
            baseline_old,
        ),
        lambda: new_genome.validate_edit(
            new_genome.SkillEdit(edit_id="e", target_module=module_id,
                                 operation=new_genome.SkillOp.ADD_RULE, new_content=text),
            baseline_new,
        ),
    )


judge_samples(
    "validate_edit invalid module",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="nope", operation=old_genome.SkillOp.ADD_RULE,
                             new_content="When x, y."), old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="nope", operation=new_genome.SkillOp.ADD_RULE,
                             new_content="When x, y."), new_baseline,
    ),
)
judge_samples(
    "validate_edit invalid operation",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule", operation="nuke",
                             new_content="When x, y."), old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule", operation="nuke",
                             new_content="When x, y."), new_baseline,
    ),
)
judge_samples(
    "validate_edit verdict not allowed",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="candidate_trigger",
                             operation=old_genome.SkillOp.ADD_RULE,
                             new_content="When a claim is supported, return clean."), old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="candidate_trigger",
                             operation=new_genome.SkillOp.ADD_RULE,
                             new_content="When a claim is supported, return clean."), new_baseline,
    ),
)
judge_samples(
    "validate_edit duplicate rule",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.ADD_RULE,
                             new_content=old_genome.BASELINE_RULE_TEXT["abstention_rule"][0]),
        old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.ADD_RULE,
                             new_content=new_genome.BASELINE_RULE_TEXT["abstention_rule"][0]),
        new_baseline,
    ),
)
judge_samples(
    "validate_edit no if-then",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.ADD_RULE, new_content="return unresolved"),
        old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.ADD_RULE, new_content="return unresolved"),
        new_baseline,
    ),
)
judge_samples(
    "validate_edit immutable baseline modify",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.MODIFY_RULE, rule_index=0,
                             new_content="When evidence is weak, return unresolved."), old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.MODIFY_RULE, rule_index=0,
                             new_content="When evidence is weak, return unresolved."), new_baseline,
    ),
)
judge_samples(
    "validate_edit invalid condition_spec",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.ADD_RULE,
                             new_content="When a complete clean defense is established, return clean.",
                             condition_spec={"schema_version": "sehd.condition_spec.v2",
                                             "feature": "bogus_feature", "operator": "equals",
                                             "value": "x", "feature_source": "provider_input",
                                             "value_type": "string", "scope": "action_spec_routes",
                                             "evidence_binding": "pre_call_projection",
                                             "combinator": "atom", "binding": "route_scoped"}),
        old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.ADD_RULE,
                             new_content="When a complete clean defense is established, return clean.",
                             condition_spec={"schema_version": "sehd.condition_spec.v2",
                                             "feature": "bogus_feature", "operator": "equals",
                                             "value": "x", "feature_source": "provider_input",
                                             "value_type": "string", "scope": "action_spec_routes",
                                             "evidence_binding": "pre_call_projection",
                                             "combinator": "atom", "binding": "route_scoped"}),
        new_baseline,
    ),
)
judge_samples(
    "validate_edit expected_effect without contract",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.ADD_RULE,
                             new_content="When a complete clean defense is established, return clean.",
                             expected_effect="state must shift"), old_baseline,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.ADD_RULE,
                             new_content="When a complete clean defense is established, return clean.",
                             expected_effect="state must shift"), new_baseline,
    ),
)

# --- protected rule modify (evolved genome with strong fitness) ---
old_protected_payload = old_baseline.to_dict()
old_protected_payload["modules"]["abstention_rule"]["rule_meta"][0] = {"is_baseline": False, "fitness_status": "strong"}
old_protected = old_genome.SkillGenome.from_dict(old_protected_payload)
new_protected_payload = new_baseline.to_dict()
new_protected_payload["modules"]["abstention_rule"]["rule_meta"][0] = {"is_baseline": False, "fitness_status": "strong"}
new_protected = new_genome.SkillGenome.from_dict(new_protected_payload)
judge_samples(
    "validate_edit protected rule modify",
    lambda: old_genome.validate_edit(
        old_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=old_genome.SkillOp.MODIFY_RULE, rule_index=0,
                             new_content="When evidence is weak, return unresolved."), old_protected,
    ),
    lambda: new_genome.validate_edit(
        new_genome.SkillEdit(edit_id="e", target_module="abstention_rule",
                             operation=new_genome.SkillOp.MODIFY_RULE, rule_index=0,
                             new_content="When evidence is weak, return unresolved."), new_protected,
    ),
)

# --- validate_policy_genome_items ---
judge_samples(
    "validate_policy_genome_items legal",
    lambda: old_policy.validate_policy_genome_items(old_policy_genome),
    lambda: new_policy.validate_policy_genome_items(new_policy_genome),
)
illegal_payload = dict(policy_payload)
illegal_payload["items"] = [{
    "item_id": "bad", "precondition": "no_such_predicate", "action": "downgrade_coverage",
    "params": {"side": "prosecution", "to_state": "incomplete"},
    "rationale": "", "source_case_id": "", "is_baseline": False,
}]
illegal_old = old_policy.PolicyGenome.from_dict(illegal_payload)
illegal_new = new_policy.PolicyGenome.from_dict(illegal_payload)
judge_samples(
    "validate_policy_genome_items unknown precondition",
    lambda: old_policy.validate_policy_genome_items(illegal_old),
    lambda: new_policy.validate_policy_genome_items(illegal_new),
)
bad_params_payload = dict(policy_payload)
bad_params_payload["items"] = [{
    "item_id": "bad2", "precondition": "all_candidates_quarantined", "action": "downgrade_coverage",
    "params": {"side": "prosecution", "to_state": "bogus_state"},
    "rationale": "", "source_case_id": "", "is_baseline": False,
}]
judge_samples(
    "validate_policy_genome_items invalid params",
    lambda: old_policy.validate_policy_genome_items(old_policy.PolicyGenome.from_dict(bad_params_payload)),
    lambda: new_policy.validate_policy_genome_items(new_policy.PolicyGenome.from_dict(bad_params_payload)),
)

# --- validate_policy_edit ---
judge_samples(
    "validate_policy_edit duplicate item",
    lambda: old_policy.validate_policy_edit(
        old_policy.PolicyEdit(edit_id="e", operation=old_policy.PolicyOp.ADD_ITEM.value,
                              new_item=old_policy_genome.items[0]), old_policy_genome,
    ),
    lambda: new_policy.validate_policy_edit(
        new_policy.PolicyEdit(edit_id="e", operation=new_policy.PolicyOp.ADD_ITEM.value,
                              new_item=new_policy_genome.items[0]), new_policy_genome,
    ),
)
judge_samples(
    "validate_policy_edit full genome",
    lambda: old_policy.validate_policy_edit(
        old_policy.PolicyEdit(edit_id="e", operation=old_policy.PolicyOp.ADD_ITEM.value,
                              new_item=old_policy.PolicyItem(
                                  item_id="x", precondition="all_candidates_quarantined",
                                  action="emit_telemetry_only", params={})),
        old_policy.PolicyGenome(genome_id="full", items=[old_policy.PolicyItem(
            item_id=str(i), precondition="all_candidates_quarantined",
            action="emit_telemetry_only", params={}, is_baseline=False,
        ) for i in range(12)]),
    ),
    lambda: new_policy.validate_policy_edit(
        new_policy.PolicyEdit(edit_id="e", operation=new_policy.PolicyOp.ADD_ITEM.value,
                              new_item=new_policy.PolicyItem(
                                  item_id="x", precondition="all_candidates_quarantined",
                                  action="emit_telemetry_only", params={})),
        new_policy.PolicyGenome(genome_id="full", items=[new_policy.PolicyItem(
            item_id=str(i), precondition="all_candidates_quarantined",
            action="emit_telemetry_only", params={}, is_baseline=False,
        ) for i in range(12)]),
    ),
)

# --- registry evolution decisions (plan/select/enqueue/promote) ---
from src.sehd_v6.core.bccse.registry_reflection import (
    plan_registry_candidates as old_plan,
    select_registry_candidate as old_select,
    build_registry_selection_prompt as old_prompt,
)
from src.sehd_v6.core.bccse.registry_promotion import (
    enqueue_registry_validation as old_enqueue,
    promote_pending_registry_entries as old_promote,
    REGISTRATION_REASON_NO_ACTION as old_no_action,
)
from sehd_rise.skills.vocabulary.evolution import (
    plan_registry_candidates as new_plan,
    select_registry_candidate as new_select,
    build_registry_selection_prompt as new_prompt,
    enqueue_registry_validation as new_enqueue,
    promote_pending_registry_entries as new_promote,
    REGISTRATION_REASON_NO_ACTION as new_no_action,
)

old_attribution = old_policy.FailureAttribution(
    layer="l2_adjudication_policy", artifact="consensus_gate_policy",
    reason_code=old_no_action, evidence={},
) if hasattr(old_policy, "FailureAttribution") else None
if old_attribution is None:
    from src.sehd_v6.core.bccse.v6_contracts import FailureAttribution as OldAttribution
    old_attribution = OldAttribution(
        layer="l2_adjudication_policy", artifact="consensus_gate_policy",
        reason_code=old_no_action, evidence={},
    )


class _NewAttribution:
    """Shape-compatible attribution for the new package (Protocol)."""
    layer = "l2_adjudication_policy"
    artifact = "consensus_gate_policy"
    reason_code = new_no_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer, "artifact": self.artifact,
            "reason_code": self.reason_code, "evidence": {},
        }


new_attribution = _NewAttribution()
old_candidates = old_plan(attribution=old_attribution, registry=evolved_old)
new_candidates = new_plan(attribution=new_attribution, registry=evolved_new)
equal("plan_registry_candidates", [c.to_dict() for c in old_candidates],
      [c.to_dict() for c in new_candidates])
old_system, old_payload = old_prompt(attribution=old_attribution, facts_summary={"terminal_state": "unresolved"},
                                     candidates=old_candidates)
new_system, new_payload = new_prompt(attribution=new_attribution, facts_summary={"terminal_state": "unresolved"},
                                     candidates=new_candidates)
equal("registry prompt system", old_system, new_system)
equal("registry prompt payload", old_payload, new_payload)
old_selected, old_reason = old_select({"candidate_index": 0}, old_candidates, evolved_old)
new_selected, new_reason = new_select({"candidate_index": 0}, new_candidates, evolved_new)
equal("select_registry_candidate", (old_selected.to_dict() if old_selected else None, old_reason),
      (new_selected.to_dict() if new_selected else None, new_reason))

old_facts = old_registry.AdjudicationFacts(quarantined_error_count=2)
new_facts = new_registry.AdjudicationFacts(quarantined_error_count=2)
old_entry = old_enqueue(genome=old_policy_genome, operation="register_predicate",
                        target="quarantined_error_count_nonzero", facts=old_facts,
                        case_id="case_p2", intended_edit=None)
new_entry = new_enqueue(genome=new_policy_genome, operation="register_predicate",
                        target="quarantined_error_count_nonzero", facts=new_facts,
                        case_id="case_p2", intended_edit=None)
equal("enqueue_registry_validation", old_entry.to_dict(), new_entry.to_dict())

# Promotion after three distinct observations.
for case in ("case_p2", "case_p3", "case_p4"):
    old_promote(genome=old_policy_genome, facts=old_facts, case_id=case, reason_code=old_no_action)
    new_promote(genome=new_policy_genome, facts=new_facts, case_id=case, reason_code=new_no_action)
old_rows = old_promote(genome=old_policy_genome, facts=old_facts, case_id="case_p5", reason_code=old_no_action)
new_rows = new_promote(genome=new_policy_genome, facts=new_facts, case_id="case_p5", reason_code=new_no_action)
equal("promote_pending_registry_entries", old_rows, new_rows)
equal("promoted genome registry hash", old_policy_genome.registry.content_hash,
      new_policy_genome.registry.content_hash)

# ============================================================
print()
if FAILURES:
    print(f"roundtrip_skills: {len(FAILURES)} FAILURE(S)")
    sys.exit(1)
print("roundtrip_skills: all checks passed")
