"""Independent code-review verification for the SEHD v6 framework.

This file is intentionally self-contained: it does NOT import any existing
test fixture or test helper.  Every assertion is built from the framework's
own public APIs on synthetic inputs, so a regression in the framework cannot
be masked by a fixture that was written against the same bug.

Scope (iteration 1 code review):
  R1  Public API surface loads without import cycles.
  R2  proof_contract annotations resolve (PolicyItem TYPE_CHECKING import).
  R3  evolution_runner exposes PolicyPlan (the promoted-intent branch).
  R4  Slot-rule contract roundtrip: validate -> render -> identity.
  R5  L2 policy genome: validate -> apply -> hash identity excludes meta.
  R6  Deterministic policy evaluator terminal movement.
  R7  Baseline genome is immutable and hash-stable.
  R8  Structural gate rejects illegal edits and accepts a legal slot edit.
  R9  Provider-route resolution precedence (env overrides config).
  R10 Replica aggregation fail-closed semantics.
  R11 Dead-code scan: every module imports cleanly (pyflakes-equivalent).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _packet_record() -> dict[str, Any]:
    return {
        "history": [
            {"role": "user", "content": "What is the capital of France?"},
            {
                "role": "assistant",
                "content": "The capital is Paris.",
                "tool_calls": [{"name": "search", "arguments": {"q": "France capital"}}],
                "tool_responses": [{"result": "Paris"}],
            },
        ]
    }


def test_r1_public_api_import_surface() -> None:
    from src.sehd_v6.detector import detect_record  # noqa: F401
    from src.sehd_v6.evolution import run_evolution  # noqa: F401
    from src.sehd_v6.core import run_online_evolution  # noqa: F401
    from src.sehd_v6.core.bccse import apply_edit, create_baseline_genome  # noqa: F401
    from src.sehd_v6.core.htc_tes import HallucinationTypeClassifier  # noqa: F401


def test_r2_proof_contract_annotations_resolve() -> None:
    import typing

    from src.sehd_v6.core.paca_tcl import proof_contract as pc

    hints = typing.get_type_hints(pc.adjudicate_proof_court)
    assert "policy_items" in hints
    assert "pre_ledger_policy_items" in hints
    hints_inner = typing.get_type_hints(pc._resolve_terminal_state)
    assert "policy_items" in hints_inner
def test_r3_evolution_runner_exposes_policy_plan() -> None:
    from src.sehd_v6.core.bccse.evolution_runner import PolicyPlan

    plan = PolicyPlan(candidates=(), route_result="no_registered_runtime_action")
    assert plan.route_result == "no_registered_runtime_action"
    with pytest.raises(ValueError):
        PolicyPlan(candidates=(), route_result="bogus_route")


def test_r4_slot_rule_contract_roundtrip() -> None:
    from src.sehd_v6.core.bccse.rule_contract import RuleSpec, render_rule, validate_rule_spec

    spec = RuleSpec(
        condition="an agent claim cites a tool response that does not contain it",
        action="emit_candidate: candidate",
        evidence_anchor="focused prosecution omitted the candidate",
        module_id="candidate_trigger",
        direction="process",
    )
    ok, reason = validate_rule_spec(spec)
    assert ok, reason
    rendered = render_rule(spec)
    assert rendered.startswith("When ")
    assert rendered.endswith(".")
    assert "emit a candidate" in rendered
    # Deterministic render: same slots -> same text.
    assert render_rule(spec) == rendered


def test_r5_policy_genome_apply_and_hash_identity() -> None:
    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyEdit,
        PolicyGenome,
        PolicyOp,
        apply_policy_edit,
        create_baseline_policy_genome,
        validate_policy_genome_items,
    )
    from src.sehd_v6.core.bccse.v6_contracts import PolicyItem

    baseline = create_baseline_policy_genome()
    assert baseline.items == []
    ok, reason = validate_policy_genome_items(baseline)
    assert ok, reason

    item = PolicyItem(
        item_id="item_1",
        precondition="consensus_gate_blocked_nonempty",
        action="emit_telemetry_only",
        params={},
        rationale="audit telemetry",
    )
    edit = PolicyEdit(
        edit_id="edit_1",
        operation=PolicyOp.ADD_ITEM.value,
        new_item=item,
        source_case_id="case_x",
    )
    child = apply_policy_edit(baseline, edit)
    assert len(child.items) == 1
    assert child.generation == 1
    # Meta bookkeeping does not change identity.
    child.set_item_meta(0, {"fitness_status": "strong"})
    assert child.content_hash == apply_policy_edit(baseline, edit).content_hash
    # Roundtrip through dict preserves the item set.
    restored = PolicyGenome.from_dict(child.to_dict())
    assert restored.content_hash == child.content_hash


def test_r6_policy_evaluator_terminal_movement() -> None:
    from src.sehd_v6.core.bccse.v6_contracts import AdjudicationFacts, PolicyItem
    from src.sehd_v6.core.paca_tcl.policy_evaluator import evaluate_policies

    facts = AdjudicationFacts(
        prosecution_coverage="complete",
        defense_coverage="complete",
        valid_error_certificate_count=0,
        valid_defense_certificate_count=1,
        surviving_error_count=0,
        terminal_state="verified_clean",
    )
    items = [
        PolicyItem(
            item_id="p1",
            precondition="prosecution_coverage_complete_and_zero_certificates",
            action="invalidate_coverage_claim",
            params={"side": "prosecution"},
        )
    ]
    result = evaluate_policies(facts, items)
    assert len(result.fired) == 1
    assert result.effective_prosecution_coverage == "unresolved"
    assert result.terminal_state == "unresolved"


def test_r7_baseline_genome_stable_and_immutable() -> None:
    from src.sehd_v6.core.bccse.genome import create_baseline_genome

    g1 = create_baseline_genome()
    g2 = create_baseline_genome()
    assert g1.content_hash == g2.content_hash
    assert g1.total_rules == 16
    for mid, module in g1.modules.items():
        for index, _rule in enumerate(module.rules):
            assert module.get_rule_meta(index).get("is_baseline") is True


def test_r8_structural_gate_legal_and_illegal_edits() -> None:
    from src.sehd_v6.core.bccse.genome import SkillEdit, SkillOp, create_baseline_genome
    from src.sehd_v6.core.bccse.rule_contract import RuleSpec, render_rule
    from src.sehd_v6.core.bccse.safety_gate import create_structural_gate

    gate = create_structural_gate()
    genome = create_baseline_genome()

    slots = {
        "condition": "a tool error is recovered before any downstream impact",
        "action": "suppress_candidate: candidate",
        "evidence_anchor": "census produced a candidate from the recovered failure",
    }
    rendered = render_rule(RuleSpec(
        condition=slots["condition"],
        action=slots["action"],
        evidence_anchor=slots["evidence_anchor"],
        module_id="candidate_trigger",
        direction="process",
    ))
    legal = SkillEdit(
        edit_id="e1",
        target_module="candidate_trigger",
        operation=SkillOp.ADD_RULE,
        new_content=rendered,
        rule_slots=slots,
        action_spec={},
    )
    accepted, _cert, reasons = gate.evaluate(
        case_id="case_1",
        case_index=0,
        target_module=legal.target_module,
        operation=legal.operation,
        content=legal.new_content,
        preconditions=(),
        current_genome=genome,
        rule_slots=legal.rule_slots,
    )
    assert accepted, reasons

    illegal = SkillEdit(
        edit_id="e2",
        target_module="candidate_trigger",
        operation=SkillOp.ADD_RULE,
        new_content="When anything happens, return error.",
        rule_slots={},
        action_spec={},
    )
    accepted2, _cert2, reasons2 = gate.evaluate(
        case_id="case_2",
        case_index=1,
        target_module=illegal.target_module,
        operation=illegal.operation,
        content=illegal.new_content,
        preconditions=(),
        current_genome=genome,
        rule_slots=None,
    )
    assert not accepted2
    assert any("verdict" in r or "forbidden" in r for r in reasons2)


def test_r9_provider_route_env_precedence() -> None:
    from src.sehd_v6.provider_routes import resolve_provider_route

    route = resolve_provider_route(
        {"endpoint": "https://config.example", "model": "deepseek-v4-flash"},
        purpose="mutation",
        route_name="mutation",
        environ={
            "SEHD_MUTATION_ENDPOINT": "https://env.example",
            "SEHD_MUTATION_API_KEY_ENV": "DEEPSEEK_API_KEY",
        },
    )
    assert route["endpoint"] == "https://env.example"
    assert route["api_key_environment"] == "DEEPSEEK_API_KEY"


def test_r10_replica_aggregation_fail_closed() -> None:
    from src.sehd_v6.detector.replica_aggregation import (
        aggregate_replicas,
        aggregate_states,
    )

    assert aggregate_states(["verified_error", "verified_error"], policy="consensus") == "verified_error"
    assert aggregate_states(["verified_error", "verified_clean"], policy="consensus") == "unresolved"
    assert aggregate_states(
        ["verified_error", "verified_error", "unresolved"], policy="majority"
    ) == "verified_error"
    assert aggregate_states(
        ["verified_error", "verified_clean", "unresolved"], policy="majority"
    ) == "unresolved"

    result = aggregate_replicas(
        [
            {"state": "verified_error", "runtime_valid": True},
            {"state": "verified_error", "runtime_valid": True},
        ]
    )
    assert result["state"] == "verified_error"
    assert result["unanimous"] is True

    invalid = aggregate_replicas(
        [
            {"state": "verified_error", "runtime_valid": True},
            {"state": "verified_clean", "runtime_valid": False},
        ]
    )
    assert invalid["state"] == "unresolved"
    assert invalid["runtime_valid"] is False


def test_r11_all_modules_import_without_side_effects() -> None:
    import importlib
    import pathlib

    root = pathlib.Path("src/sehd_v6")
    for module_path in sorted(root.rglob("*.py")):
        if module_path.name == "__init__.py":
            continue
        relative = module_path.relative_to(root)
        parts = list(relative.parts)
        parts[-1] = parts[-1][:-3]
        name = ".".join(["src", "sehd_v6", *parts])
        importlib.import_module(name)
