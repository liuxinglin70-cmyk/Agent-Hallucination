"""``terminal_authority`` is enforced at run time, not only declared.

``RUNNER_REGISTRY`` declares ``typed_semantic`` and ``sandboxed_component`` as
``evidence_only``: they may report, and they may never be the thing that
convicts. Nothing read that field while a batch was running -- enforcement was
by construction (hooks and intervention kinds) plus a conviction table keyed by
*predicate name*, so a batch that paired a typed-DSL program with an
evidence-only entry reusing its predicate name let the semantic verdict
authorize ``convict_mechanical_violation`` and produced a terminal
``verified_error / authoritative / mechanical_violation``.

These tests pin both halves of the fix:

* the table is keyed by ``(predicate, runner)``, and
* a row whose runner is not conviction-authorized can never satisfy the guard's
  mechanical-evidence binding, whatever its verdict.

The typed-DSL path is pinned unchanged alongside: the fix must not weaken the
conviction route the framework legitimately has.
"""

from __future__ import annotations

from detector.adjudication import (
    AUTHORITY_AUTHORITATIVE,
    STATE_CLEAN,
    STATE_ERROR,
    adjudicate,
)
from detector.interventions import (
    SemanticExecutor,
    build_intervention_registry,
    compile_runtime_plan,
    conviction_verdict_table,
    conviction_verdicts_for,
    runner_declarations,
)
from detector.packet import build_packet


PROGRAM_NAME = "evo_authority_probe"


def _program():
    return {
        "schema_version": "detector.predicate_program.v1",
        "name": PROGRAM_NAME,
        "description": "observe a value mismatch in the final answer",
        "verdicts": ["contradicted", "not_applicable"],
        "conviction_verdicts": ["contradicted"],
        "default_verdict": "not_applicable",
        "rules": [{
            "field": "question_text",
            "operator": "contains",
            # Never present in the probe packet, so the typed-DSL program
            # itself observes nothing and only the semantic entry speaks.
            "value": "zzz-never-present-zzz",
            "verdict": "contradicted",
            "detail": "rule that cannot fire on the probe packet",
        }],
    }


def _evidence_entry(entry_id: str, runner: str):
    body = {
        "predicate": PROGRAM_NAME,
        "runner": runner,
        "output_verdicts": ["contradicted", "not_applicable"],
    }
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": entry_id,
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": body,
        "claim": {
            "expected_behavior": "observe the mismatch",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-authority",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    if runner == "typed_dsl":
        entry["body"]["predicate_definition"] = _program()
    elif runner == "typed_semantic":
        entry["body"]["output_verdicts"] = [
            "supported", "contradicted", "abstained",
        ]
        entry["artifact"] = {"id": "exec-1", "digest": "d" * 64}
    return entry


def _guard_entry():
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-GUARD",
        "type": "decision_guard",
        "hook": "adjudicate.guard",
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": [
                f"{PROGRAM_NAME}=contradicted",
                "judge_positive=false",
                "positive_source=none",
            ],
        },
        "body": {
            "action": "convict_mechanical_violation",
            "evidence_predicate": PROGRAM_NAME,
            "action_not_when": "never when nothing was observed",
            "action_not_when_facts": [f"{PROGRAM_NAME}=not_applicable"],
        },
        "claim": {
            "expected_behavior": "convict on the mechanical observation",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-authority",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }


def _probe_packet():
    return build_packet({
        "question": "Summarize the report. Use the search tool.",
        "history": [{
            "step": 1,
            "role": "assistant",
            "content": "The report says nothing relevant.",
            "tool_calls": [{"name": "search", "arguments": {"query": "report"}}],
            "tool_responses": [{"results": []}],
        }],
        "is_hallucination": True,
        "hallucination_step": 1,
        "true_answer": "gold answer",
    })


class _AlwaysContradicted(SemanticExecutor):
    """A learned runner that answers ``contradicted`` on everything."""

    name = "probe"

    def __init__(self) -> None:
        super().__init__(artifact_id="exec-1", artifact_digest="d" * 64)

    def judge(self, predicate, packet, context):
        return {"verdict": "contradicted", "detail": "model says contradicted"}


def _facts_row():
    return [{
        "judge_positive": "false",
        "positive_source": "none",
        "defense_verdict": "none",
        "defense_view_degraded": "false",
        "upgrade_degraded": "false",
        "recall_upgrade": "none",
    }]


def _run(entries, *, semantic_executor=None):
    registry = build_intervention_registry(entries)
    plan = compile_runtime_plan(
        registry, semantic_executor=semantic_executor,
    )
    packet = _probe_packet()
    rows = plan.evidence_for("judge.post", packet, {
        "contracts": [], "evidence_refs": [],
        "delivered_result": "", "quote": "",
    })
    actions = plan.guard_actions(packet, fact_rows=_facts_row())
    return plan, packet, rows, actions


def test_declared_authority_by_runner():
    """The declaration the rest of this module is about."""
    authority = {
        item["name"]: item["terminal_authority"]
        for item in runner_declarations()
    }
    assert authority["typed_dsl"] == "conviction"
    assert authority["deterministic"] == "conviction"
    assert authority["typed_semantic"] == "evidence_only"
    assert authority["sandboxed_component"] == "evidence_only"


def test_conviction_table_is_keyed_by_predicate_and_runner():
    table = conviction_verdict_table({PROGRAM_NAME: _program()})
    assert table[(PROGRAM_NAME, "typed_dsl")] == frozenset({"contradicted"})
    # The built-in predicates are keyed to their own executor.
    assert ("required_action_present", "deterministic") in table
    assert "required_action_present" not in table


def test_evidence_only_runner_yields_no_conviction_verdicts():
    table = conviction_verdict_table({PROGRAM_NAME: _program()})
    # A semantic row that reuses the program's name reads as having no
    # conviction authority at all, even though the name is in the table.
    assert conviction_verdicts_for(table, PROGRAM_NAME, "typed_semantic") == frozenset()
    assert conviction_verdicts_for(
        table, PROGRAM_NAME, "sandboxed_component",
    ) == frozenset()
    # An unregistered runner is refused too.
    assert conviction_verdicts_for(table, PROGRAM_NAME, "not-a-runner") == frozenset()
    # And the conviction runner keeps its verdicts.
    assert conviction_verdicts_for(
        table, PROGRAM_NAME, "typed_dsl",
    ) == frozenset({"contradicted"})


def test_typed_dsl_and_typed_semantic_same_name_only_the_program_convicts():
    """The defect's exact batch: same predicate name, two runners.

    The semantic entry is the only one whose verdict is ``contradicted``
    (the program's rule cannot fire on this packet). Before the fix that row
    authorized the conviction.
    """
    plan, packet, rows, actions = _run(
        [_evidence_entry("IV-DSL", "typed_dsl"),
         _evidence_entry("IV-SEM", "typed_semantic"),
         _guard_entry()],
        semantic_executor=_AlwaysContradicted(),
    )
    by_id = {row["intervention_id"]: row for row in rows}
    assert by_id["IV-SEM"]["verdict"] == "contradicted"
    assert by_id["IV-SEM"]["runner"] == "typed_semantic"
    assert by_id["IV-DSL"]["runner"] == "typed_dsl"
    assert by_id["IV-DSL"]["verdict"] == "not_applicable"

    # The semantic row may not convict...
    assert actions == []
    assert plan.mechanical_guard_evidence == []
    # ...and the typed-DSL program keeps its standing: a program that *does*
    # observe the verdict still convicts.
    assert conviction_verdicts_for(
        plan.predicate_conviction_verdicts, PROGRAM_NAME, "typed_dsl",
    ) == frozenset({"contradicted"})

    proof = adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": [], "upgraded_candidate": None},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=actions,
        mechanical_evidence=plan.mechanical_guard_evidence,
        hearings=[],
        hearing_facts=[],
        predicate_conviction_verdicts=plan.predicate_conviction_verdicts,
    )
    assert proof["state"] == STATE_CLEAN
    assert proof["terminal_basis"]["decisive_facts"].get("positive_source") != (
        "mechanical_violation"
    )


def test_evidence_only_row_forged_into_mechanical_evidence_cannot_convict():
    """The adjudication half: a hand-built evidence row is checked too.

    A caller that reaches ``adjudicate`` directly with a mechanical evidence
    row produced by an evidence-only runner must not be able to convict with
    it -- the second gate has to hold on its own, not only because the plan
    refused to emit the row.
    """
    packet = _probe_packet()
    verdicts = conviction_verdict_table({PROGRAM_NAME: _program()})
    proof = adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": [], "upgraded_candidate": None},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "action": "convict_mechanical_violation",
            "intervention_id": "IV-GUARD",
            "evidence_intervention_id": "IV-SEM",
            "failure_family": "FAM-fn-authority",
            "predicate": PROGRAM_NAME,
            "runner": "typed_semantic",
            "verdict": "contradicted",
        }],
        hearings=[],
        hearing_facts=[],
        predicate_conviction_verdicts=verdicts,
    )
    assert proof["state"] == STATE_CLEAN

    # The same row with the conviction-authorized runner does convict, so the
    # assertion above is about the runner and not about the row's shape.
    proof = adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": [], "upgraded_candidate": None},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "action": "convict_mechanical_violation",
            "intervention_id": "IV-GUARD",
            "evidence_intervention_id": "IV-DSL",
            "failure_family": "FAM-fn-authority",
            "predicate": PROGRAM_NAME,
            "runner": "typed_dsl",
            "verdict": "contradicted",
        }],
        hearings=[],
        hearing_facts=[],
        predicate_conviction_verdicts=verdicts,
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["terminal_basis"]["decisive_facts"]["positive_source"] == (
        "mechanical_violation"
    )


def test_built_in_predicate_row_without_a_runner_still_convicts(synthetic_conviction_calibration):
    """Legacy rows (no ``runner`` field) keep the predicate-only semantics.

    A direct ``adjudicate`` call passes ``PREDICATE_CONVICTION_VERDICTS``, which
    is keyed by name; rows that name no runner must keep working, or the fix
    would break every existing caller rather than closing the bypass.
    """
    packet = _probe_packet()
    proof = adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": [], "upgraded_candidate": None},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[{
            "action": "convict_mechanical_violation",
            "intervention_id": "IV-GUARD",
            "evidence_intervention_id": "IV-E",
            "failure_family": "FAM-fn-authority",
            "predicate": "required_action_present",
            "verdict": "violated",
        }],
        hearings=[],
        hearing_facts=[],
    )
    assert proof["state"] == STATE_ERROR


def test_program_that_really_observes_the_verdict_still_convicts():
    """The typed-DSL conviction path, end to end and unchanged.

    The program's rule fires here, so the guard binds its row and the terminal
    verdict is a mechanical conviction.
    """
    program = _program()
    entry = _evidence_entry("IV-DSL", "typed_dsl")
    entry["body"]["predicate_definition"] = {
        **program,
        # The packet does carry a search call, so a rule keyed on "search is
        # present" fires; the point is that a *firing* typed-DSL program still
        # convicts after the authority change.
        "rules": [{
            "field": "tool_names",
            "operator": "any_contains",
            "value": "search",
            "verdict": "contradicted",
            "detail": "the delivered answer contradicts the retrieved report",
        }],
    }
    plan, packet, rows, actions = _run([entry, _guard_entry()])
    assert rows[0]["verdict"] == "contradicted"
    assert actions == ["convict_mechanical_violation"]
    assert plan.mechanical_guard_evidence[0]["runner"] == "typed_dsl"
    assert plan.mechanical_guard_evidence[0]["predicate"] == PROGRAM_NAME

    proof = adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": [], "upgraded_candidate": None},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=actions,
        mechanical_evidence=plan.mechanical_guard_evidence,
        hearings=[],
        hearing_facts=[],
        predicate_conviction_verdicts=plan.predicate_conviction_verdicts,
    )
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["terminal_basis"]["decisive_facts"]["positive_source"] == (
        "mechanical_violation"
    )
