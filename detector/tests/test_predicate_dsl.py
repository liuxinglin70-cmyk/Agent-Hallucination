from __future__ import annotations

import pytest
import time

from detector.interventions import (
    InterventionPlan,
    build_intervention_registry,
    validate_intervention_batch,
)
from detector.packet import build_packet
from detector.predicate_dsl import (
    PROGRAM_SCHEMA,
    run_predicate_program,
    validate_predicate_program,
)


def _program(**overrides):
    program = {
        "schema_version": PROGRAM_SCHEMA,
        "name": "evo_missing_search",
        "description": "missing retrieval tool",
        "verdicts": ["satisfied", "violated", "not_applicable"],
        "conviction_verdicts": ["violated"],
        "default_verdict": "not_applicable",
        "rules": [
            {
                "field": "tool_names",
                "operator": "none_contains",
                "value": "search",
                "verdict": "violated",
                "detail": "no retrieval tool was called",
            }
        ],
    }
    program.update(overrides)
    return program


def _packet():
    return build_packet({
        "question": "Use search to find X.",
        "history": [{
            "role": "assistant",
            "content": "ok",
            "tool_calls": [{"name": "python", "arguments": "{}"}],
        }],
    })


@pytest.mark.parametrize("operator", ["matches", "not_matches"])
@pytest.mark.parametrize("pattern,text", [
    (r"^(a?){30}a{30}$", "a" * 30),
    (r"^a+a+a+a+a+b$", "a" * 4096),
])
def test_regex_execution_is_bounded_without_authorizing_an_unknown_match(operator, pattern, text):
    import subprocess
    import sys
    import json

    program = _program(rules=[{
        "field": "question_text", "operator": operator,
        "value": pattern, "verdict": "violated",
    }])
    code = (
        "import json,sys; from detector.predicate_dsl import run_predicate_program; "
        "request=json.load(sys.stdin); "
        "print(json.dumps(run_predicate_program(*request)))"
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps([program, {"question": text, "events": []}]),
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert row["verdict"] == "not_applicable"
    assert row["execution_status"] == "resource_exhausted"
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("stop_kind", ["deadline", "cancel", "budget"])
def test_regex_worker_is_reaped_on_every_stop_path(monkeypatch, stop_kind):
    import subprocess
    import threading
    from detector import predicate_dsl as dsl
    from detector.runtime import CaseDeadlineExceeded, RunControl, RunStopped, deadline_scope, run_scope

    processes = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(dsl.subprocess, "Popen", capture)
    monkeypatch.setattr(dsl, "_REGEX_WORKER", "import time; time.sleep(10)")
    monkeypatch.setattr(dsl, "MAX_REGEX_SECONDS", 0.1 if stop_kind == "budget" else 2.0)
    control = RunControl()
    timer = threading.Timer(0.1, lambda: control.stop({"message": "test cancellation"}))
    program = _program(rules=[{
        "field": "question_text", "operator": "not_matches", "value": "x", "verdict": "violated",
    }])
    try:
        with run_scope(control), deadline_scope(0.1 if stop_kind == "deadline" else 3):
            if stop_kind == "cancel":
                timer.start()
            if stop_kind == "budget":
                result = run_predicate_program(program, _packet())
                assert result["execution_status"] == "resource_exhausted"
            else:
                error = CaseDeadlineExceeded if stop_kind == "deadline" else RunStopped
                with pytest.raises(error):
                    run_predicate_program(program, _packet())
    finally:
        timer.cancel()
        if timer.ident is not None:
            timer.join()
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_unmeasurable_regex_cannot_feed_facts_guards_or_recall(monkeypatch):
    from detector import predicate_dsl as dsl

    monkeypatch.setattr(dsl, "_REGEX_WORKER", "import time; time.sleep(10)")
    monkeypatch.setattr(dsl, "MAX_REGEX_SECONDS", 0.05)
    program = _program(
        default_verdict="satisfied", conviction_verdicts=["not_applicable"],
        rules=[{"field": "question_text", "operator": "not_matches", "value": "x",
                "verdict": "not_applicable"}],
    )
    evidence = _entry("IV-E", "evidence")
    evidence["body"]["predicate_definition"] = program
    guard = _entry("IV-G", "guard")
    guard["trigger"]["facts"][0] = "evo_missing_search=not_applicable"
    plan = InterventionPlan.from_registry(build_intervention_registry([evidence, guard]))
    rows = plan.evidence_for("judge.post", _packet(), {})
    assert rows[0]["execution_status"] == "resource_exhausted"
    assert plan.facts == {}
    assert plan.guard_actions(_packet(), {"evo_missing_search": "not_applicable",
                              "judge_positive": "false", "positive_source": "none"}) == []
    trigger = _trigger_entry()
    trigger["body"]["predicate_definition"] = program
    trigger["body"]["trigger_verdicts"] = ["not_applicable"]
    plan = InterventionPlan.from_registry(build_intervention_registry([trigger]))
    assert plan.recall_triggered(_packet(), judge_positive=False, judge_parsed={}) is False


def test_program_normalization_preserves_feature_and_fact_conditions():
    program = _program(rules=[{
        "field": "question_text", "operator": "contains", "value": "search",
        "verdict": "violated", "when": {
            "packet_features": ["has_tool_calls"], "facts": ["judge_positive=false"],
        },
    }])
    normalized = validate_predicate_program(program)
    assert validate_predicate_program(normalized) == normalized
    no_tools = build_packet({"question": "Use search", "history": [{"role": "assistant", "content": "ok"}]})
    for spec in (program, normalized):
        assert run_predicate_program(spec, no_tools, {"judge_positive": False})["verdict"] == "not_applicable"
        assert run_predicate_program(spec, _packet(), {"judge_positive": True})["verdict"] == "not_applicable"
        assert run_predicate_program(spec, _packet(), {"judge_positive": False})["verdict"] == "violated"


@pytest.mark.parametrize("fact", ["gold=secret", "judge_parsed=secret", "context_gold=secret"])
def test_when_facts_cannot_read_fields_outside_the_typed_vocabulary(fact):
    with pytest.raises(ValueError, match="typed field vocabulary"):
        validate_predicate_program(_program(rules=[{
            "field": "question_text", "operator": "contains", "value": "search",
            "verdict": "violated", "when": {"facts": [fact]},
        }]))


def test_when_facts_are_subject_to_hook_input_permissions():
    from detector.predicate_context import validate_program_hook

    program = validate_predicate_program(_program(rules=[{
        "field": "question_text", "operator": "contains", "value": "search",
        "verdict": "violated", "when": {"facts": ["judge_positive=false"]},
    }]))
    with pytest.raises(ValueError, match="unavailable"):
        validate_program_hook(program, "upgrade.pre")
    entry = _entry("IV-WHEN", "evidence")
    entry["hook"] = "upgrade.pre"
    entry["body"]["predicate_definition"] = program
    with pytest.raises(ValueError, match="unavailable"):
        validate_intervention_batch([entry])


@pytest.mark.parametrize("field,value", [
    ("when_features", ["has_tool_calls"]), ("when_facts", [["gold", "secret"]]),
    ("extra_condition", "uninterpreted constraint"),
])
def test_rule_rejects_uninterpreted_or_retired_constraint_fields(field, value):
    program = _program(rules=[{
        "field": "question_text", "operator": "contains", "value": "search",
        "verdict": "violated", field: value,
    }])
    with pytest.raises(ValueError, match="unknown fields"):
        validate_predicate_program(program)


def test_regex_conditions_share_one_program_budget(monkeypatch):
    from detector import predicate_dsl as dsl

    monkeypatch.setattr(dsl, "MAX_PROGRAM_SECONDS", 0.7)
    monkeypatch.setattr(dsl, "MAX_REGEX_SECONDS", 1.0)
    monkeypatch.setattr(dsl, "_REGEX_WORKER", "import time; time.sleep(0.4); print('true')")
    program = _program(rules=[{
        "conditions": [
            {"field": "question_text", "operator": "matches", "value": "x"},
            {"field": "question_text", "operator": "matches", "value": "x"},
        ], "verdict": "violated",
    }])
    result = run_predicate_program(program, _packet())
    assert result["verdict"] == "not_applicable"
    assert result["execution_status"] == "resource_exhausted"


def _entry(entry_id: str, evidence_type: str):
    if evidence_type == "evidence":
        return {
            "schema_version": "detector.intervention.v1",
            "intervention_id": entry_id,
            "type": "evidence_check",
            "hook": "judge.post",
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "predicate": "evo_missing_search",
                "runner": "typed_dsl",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
                "predicate_definition": _program(),
            },
            "claim": {
                "expected_behavior": "append evidence",
                "target_polarity": "recall",
            },
            "provenance": {
                "cycle": 1,
                "failure_family": "FAM-fn-test",
                "failure_cases": ["A/000.json", "A/001.json"],
            },
        }
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": entry_id,
        "type": "decision_guard",
        "hook": "adjudicate.guard",
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": [
                "evo_missing_search=violated",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        "body": {
            "action": "convict_mechanical_violation",
            "evidence_predicate": "evo_missing_search",
            "action_not_when": "Never when retrieval exists.",
            "action_not_when_facts": ["evo_missing_search=satisfied"],
        },
        "claim": {
            "expected_behavior": "convict missing retrieval",
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-test",
            "failure_cases": ["A/000.json", "A/001.json"],
        },
    }


def test_typed_dsl_program_executes_deterministically():
    packet = _packet()
    result = run_predicate_program(_program(), packet, {})
    assert result["predicate"] == "evo_missing_search"
    assert result["verdict"] == "violated"
    assert result["runner"] == "typed_dsl"
    assert result["definition_hash"]


def test_typed_dsl_rejects_unknown_field_and_bad_operator():
    with pytest.raises(ValueError, match="outside the typed field vocabulary"):
        validate_predicate_program(_program(rules=[{
            "field": "labels",
            "operator": "contains",
            "value": "x",
            "verdict": "violated",
        }]))
    with pytest.raises(ValueError, match="not valid for"):
        validate_predicate_program(_program(rules=[{
            "field": "event_count",
            "operator": "contains",
            "value": "x",
            "verdict": "violated",
        }]))


def test_inline_program_registers_new_fact_and_conviction():
    evidence = _entry("IV-E", "evidence")
    guard = _entry("IV-G", "guard")
    validate_intervention_batch([evidence, guard], require_executable_bounds=True)
    registry = build_intervention_registry([evidence, guard])
    plan = InterventionPlan.from_registry(registry)
    packet = _packet()
    rows = plan.evidence_for(
        "judge.post", packet,
        {"contracts": [], "evidence_refs": [], "delivered_result": "", "quote": ""},
    )
    assert rows[0]["verdict"] == "violated"
    assert plan.facts["evo_missing_search"] == "violated"
    actions = plan.guard_actions(packet, fact_rows=[{
        "judge_positive": "false",
        "positive_source": "none",
        "defense_verdict": "none",
        "defense_view_degraded": "false",
        "recall_upgrade": "none",
    }])
    assert actions == ["convict_mechanical_violation"]
    assert plan.mechanical_guard_evidence[0]["predicate"] == "evo_missing_search"
    # The table is keyed by the pair, not the predicate name alone: conviction
    # authority belongs to the runner that produced the verdict, so an entry
    # that reuses this name under an evidence-only runner cannot collect it.
    assert ("evo_missing_search", "typed_dsl") in plan.predicate_conviction_verdicts
    assert "evo_missing_search" not in plan.predicate_conviction_verdicts


def test_cross_entry_binding_rejects_guard_without_program():
    guard = _entry("IV-G", "guard")
    with pytest.raises(ValueError, match="unknown trigger fact"):
        validate_intervention_batch([guard], require_executable_bounds=True)


def test_duplicate_name_with_different_program_is_rejected():
    first = _entry("IV-E1", "evidence")
    second = _entry("IV-E2", "evidence")
    second["body"]["predicate_definition"] = _program(
        rules=[{
            "field": "tool_names",
            "operator": "any_contains",
            "value": "python",
            "verdict": "violated",
        }]
    )
    with pytest.raises(ValueError, match="redefined with a different program"):
        validate_intervention_batch([first, second])



def test_condition_conjunction_requires_every_condition():
    program = _program(rules=[{
        "conditions": [
            {
                "field": "question_text",
                "operator": "contains",
                "value": "use search",
            },
            {
                "field": "tool_names",
                "operator": "none_contains",
                "value": "search",
            },
        ],
        "verdict": "violated",
        "detail": "question requires search but no search tool",
    }])
    assert run_predicate_program(program, _packet(), {})["verdict"] == "violated"

    with_search = build_packet({
        "question": "Use search to find X.",
        "history": [{
            "role": "assistant",
            "content": "ok",
            "tool_calls": [{"name": "search", "arguments": "{}"}],
        }],
    })
    assert run_predicate_program(
        program, with_search, {},
    )["verdict"] == "not_applicable"


def test_condition_conjunction_rejects_ambiguous_rule_and_too_many_conditions():
    with pytest.raises(ValueError, match="not both"):
        validate_predicate_program(_program(rules=[{
            "field": "tool_names",
            "operator": "none_contains",
            "value": "search",
            "conditions": [{"field": "tool_names", "operator": "missing"}],
            "verdict": "violated",
        }]))
    with pytest.raises(ValueError, match="limit is 4"):
        validate_predicate_program(_program(rules=[{
            "conditions": [
                {"field": "tool_names", "operator": "missing"},
                {"field": "prose_texts", "operator": "missing"},
                {"field": "event_texts", "operator": "missing"},

                {"field": "interaction_texts", "operator": "missing"},
                {"field": "question_text", "operator": "exists"},
            ],
            "verdict": "violated",
        }]))


def _trigger_entry():
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-RT",
        "type": "recall_trigger",
        "hook": "recall.trigger",
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": ["judge_positive=false"],
        },
        "body": {
            "predicate": "evo_missing_search",
            "runner": "typed_dsl",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
            "trigger_verdicts": ["violated"],
            "require_judge_negative": True,
            "predicate_definition": _program(),
        },
        "claim": {
            "expected_behavior": "force recall for missing retrieval",
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-test",
            "failure_cases": ["A/000.json", "A/001.json"],
        },
    }


def test_recall_trigger_forces_recall_net_for_matching_judge_negative():
    entry = _trigger_entry()
    validate_intervention_batch([entry], require_executable_bounds=True)
    plan = InterventionPlan.from_registry(build_intervention_registry([entry]))
    assert plan.recall_triggered(
        _packet(), judge_positive=False, judge_parsed={},
    ) is True
    assert any(
        item.get("event") == "recall_triggered"
        for item in plan.deployment_summary()["fired"]
    )


def test_recall_trigger_stays_closed_without_matching_verdict_or_judge_positive():
    entry = _trigger_entry()
    plan = InterventionPlan.from_registry(build_intervention_registry([entry]))
    with_search = build_packet({
        "question": "Use search to find X.",
        "history": [{
            "role": "assistant",
            "content": "ok",
            "tool_calls": [{"name": "search", "arguments": "{}"}],
        }],
    })
    assert plan.recall_triggered(
        with_search, judge_positive=False, judge_parsed={},
    ) is False
    assert plan.recall_triggered(
        _packet(), judge_positive=True,
        judge_parsed={"is_hallucination": True},
    ) is False



def test_judge_context_scalar_fields():
    program = _program(rules=[{
        "field": "context_judge_positive",
        "operator": "equals",
        "value": "false",
        "verdict": "violated",
    }])
    assert run_predicate_program(
        program, _packet(), {"judge_parsed": {"is_hallucination": False}},
    )["verdict"] == "violated"
    assert run_predicate_program(
        program, _packet(), {"judge_parsed": {"is_hallucination": True}},
    )["verdict"] == "not_applicable"


def test_default_verdict_may_not_be_a_conviction_verdict():
    """A conviction needs a matching rule, not the absence of one.

    ``default_verdict`` fires exactly when no rule matched, so authorising it
    as a conviction verdict would let "nothing matched" become mechanical
    evidence for a terminal conviction.
    """
    with pytest.raises(ValueError, match="must not be a conviction verdict"):
        validate_predicate_program(_program(default_verdict="violated"))


def test_aggregate_comparison_literal_is_validated_for_every_operator():
    """Validation and execution agree on aggregate thresholds.

    Only the ordering operators used to be int-checked, so an ``equals``
    program with a non-integer literal passed registry validation and then
    raised at runtime, aborting the whole batch.
    """
    for operator in ("equals", "not_equals", "greater_than", "less_than"):
        with pytest.raises(ValueError, match="must be an integer"):
            validate_predicate_program(_program(rules=[{
                "conditions": [{
                    "field": "event_count",
                    "operator": operator,
                    "value": "abc",
                }],
                "verdict": "violated",
            }]))


def test_aggregate_zero_literal_is_expressible():
    """``tool_call_count equals 0`` must be writable.

    ``_require_text`` collapsed ``0``/``False`` to ``""`` and rejected them.
    """
    program = _program(rules=[{
        "conditions": [{
            "field": "tool_call_count", "operator": "equals", "value": 0,
        }],
        "verdict": "violated",
        "detail": "no tool calls",
    }])
    spec = validate_predicate_program(program)
    assert run_predicate_program(spec, _packet())["verdict"] == "not_applicable"
    no_tools = build_packet({
        "question": "Compute the sum.",
        "history": [{"role": "assistant", "content": "4"}],
    })
    assert run_predicate_program(spec, no_tools)["verdict"] == "violated"


def test_regex_backreferences_and_ambiguous_quantifiers_are_rejected():
    """Refuse the ReDoS shapes ``re`` cannot be time-limited against."""
    for pattern in [r"(a)\1", "(a+)+", "(a|aa)+", r"(\d{2})+", "(a+){2,}"]:
        with pytest.raises(ValueError):
            validate_predicate_program(_program(rules=[{
                "field": "question_text",
                "operator": "matches",
                "value": pattern,
                "verdict": "violated",
            }]))


def test_regex_is_case_insensitive_like_other_operators():
    """``matches`` must not be a silent case-sensitivity exception."""
    program = _program(rules=[{
        "field": "question_text",
        "operator": "matches",
        "value": "^use .*search",
        "verdict": "violated",
    }])
    packet = build_packet({
        "question": "Use the SEARCH tool.",
        "history": [{"role": "assistant", "content": "ok"}],
    })
    assert run_predicate_program(program, packet)["verdict"] == "violated"


def test_context_judge_positive_treats_string_false_as_false():
    """A stringly ``"false"`` must not read as truthy."""
    from detector.predicate_dsl import _extract_view

    assert _extract_view(
        "context_judge_positive", _packet(), {"judge_positive": "false"},
    ) == "false"
    assert _extract_view(
        "context_judge_positive", _packet(), {"judge_positive": "true"},
    ) == "true"


def test_recall_gate_context_matches_live_context():
    from evolution.gates import _family_contexts
    entry = _trigger_entry()
    program = entry["body"]["predicate_definition"]
    program["rules"] = [{"conditions": [
        {"field": "context_judge_positive", "operator": "equals", "value": "false"},
        {"field": "context_judge_confidence", "operator": "equals", "value": "high"},
        {"field": "context_judge_hallucination_reason", "operator": "missing"},
    ], "verdict": "violated"}]
    judge = {"is_hallucination": False, "confidence": "high", "hallucination_reason": None}
    rows = [{"case": "A/000.json", "result": {"proof": {"judge": judge}}}]
    context = _family_contexts(["A/000.json"], rows)["A/000.json"]
    assert run_predicate_program(program, _packet(), context)["verdict"] == "violated"
    plan = InterventionPlan.from_registry(build_intervention_registry([entry]))
    assert plan.recall_triggered(_packet(), judge_positive=False, judge_parsed=judge)
    assert run_predicate_program(program, _packet(), {})["verdict"] == "not_applicable"


@pytest.mark.parametrize("hook,field", [
    ("defense.pre", "context_judge_positive"),
    ("defense.pre", "context_judge_hallucination_reason"),
    ("upgrade.pre", "context_contract_kinds"),
])
def test_unavailable_hook_fields_rejected_before_deployment(hook, field):
    entry = _entry("IV-CONTEXT", "evidence")
    entry["hook"] = hook
    entry["body"]["predicate_definition"]["rules"] = [{
        "field": field, "operator": "exists", "verdict": "violated",
    }]
    with pytest.raises(ValueError, match="context fields unavailable"):
        build_intervention_registry([entry])


def test_defense_can_use_hearing_evidence_without_judge_access():
    entry = _entry("IV-CONTEXT", "evidence")
    entry["hook"] = "defense.pre"
    entry["body"]["predicate_definition"]["rules"] = [{
        "field": "context_quote", "operator": "exists", "verdict": "violated",
    }]
    plan = InterventionPlan.from_registry(build_intervention_registry([entry]))
    evidence = plan.evidence_for("defense.pre", _packet(), {"quote": "observed claim"})
    assert evidence[0]["verdict"] == "violated"


def test_missing_recorded_judge_is_unmeasured_not_a_false_predicate(tmp_path):
    from evolution.gates import structural_gate
    from evolution.engine import terminal_classification
    from evolution.config import load_config
    entry = _trigger_entry()
    entry["body"]["predicate_definition"]["rules"] = [{
        "field": "context_judge_positive", "operator": "equals", "value": "false",
        "verdict": "violated",
    }]
    family = {"failure_family": "FAM-fn-test", "failure_cases": ["A/000.json", "A/001.json"]}
    verdict = structural_gate(entry, active_entries=[], config=load_config(),
        dataset_root=str(tmp_path), diagnosed_families=[family], patrol_rows=[])
    assert verdict["evaluable"] is False
    assert "missing recorded judge inputs" in verdict["reason"]
    assert terminal_classification({"candidate": entry, "structural": verdict}, admitted=False) == "not_evaluated"
