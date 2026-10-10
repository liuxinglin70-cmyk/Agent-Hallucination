from __future__ import annotations

import pytest

from detector.adjudication import adjudicate
from detector.replay import validate_frozen_stages
from detector.defense import _parse_defense, _validate_defense_evidence
from detector.evidence import EvidenceError, build_snapshot, citation_error
from detector.packet import build_packet
from detector.predicates import run_predicate


def _snapshot(resolutions=()):
    return build_snapshot(
        query="ReactorAlpha capacity", derivation="task",
        task_excerpt="ReactorAlpha capacity",
        sources=[{
            "url": f"https://synthetic.invalid/{index}", "title": f"Source {index}",
            "publisher": f"Publisher {index}", "retrieved_at": "2026-10-10T00:00:00+00:00",
            "excerpt": f"{entity} capacity is {value} units.",
            "verifies": {"entity": entity, "attribute": "capacity", "value": value},
            "applicable_context": f"{entity} design",
        } for index, (entity, value) in enumerate([
            ("ReactorAlpha", "10"), ("ReactorAlpha", "20"),
            ("ReactorBeta", "30"), ("ReactorBeta", "40"),
        ], start=1)], resolutions=resolutions,
    )


def _packet():
    return build_packet({
        "question": "Report ReactorAlpha and ReactorBeta capacities.",
        "history": [{"role": "assistant", "content": "ReactorAlpha capacity is 10 units."}],
    })


@pytest.mark.parametrize("boundary", ["citation", "live", "frozen"])
@pytest.mark.parametrize("note,accepted", [
    ("E1 applies; E2 concerns an older Alpha revision.", False),
    ("E1 applies; E2 concerns an older Alpha revision. E3 applies instead of E4 for Beta.", True),
])
def test_every_cited_conflict_needs_its_own_note(boundary, note, accepted):
    snapshot = _snapshot()
    row = {
        "verdict": "substantiated", "confirmed_step": 1, "evidence_refs": ["event_001"],
        "evidence_citations": ["E1", "E3"], "conflict_note": note, "reason": "capacity proof",
    }
    if boundary == "citation":
        error = citation_error(snapshot, row["evidence_citations"], where="review",
                               require_settled=True, conflict_note=note)
        assert (error is None) is accepted
    elif boundary == "live":
        parsed, error = _parse_defense(row)
        assert parsed is not None, error
        parsed, error = _validate_defense_evidence(parsed, _packet(), {"step": 1}, snapshot)
        assert (parsed is not None) is accepted
    else:
        judge = {
            "is_hallucination": False, "hallucination_step": None, "hallucination_reason": None,
            "delivered_result": "10 units", "correct_result": "10 units", "basis": None,
            "confidence": "high", "evidence_refs": [], "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "10 units"}],
        }
        stages = {
            "judge_result": {"ok": True, "parsed": judge}, "judge_status": {"state": "ok"},
            "hearings": [{"source": "judge", "positive": {"step": 1}, "candidate_id": None,
                          "defense_result": {"ok": True, "parsed": row},
                          "defense_status": {"state": "ok"}}],
        }
        if accepted:
            validate_frozen_stages(stages, _packet(), snapshot)
        else:
            with pytest.raises(ValueError, match="evidence error"):
                validate_frozen_stages(stages, _packet(), snapshot)


def test_resolution_must_belong_to_its_entity_and_attribute():
    with pytest.raises(EvidenceError, match="entity/attribute"):
        _snapshot([{"entity": "ReactorAlpha", "attribute": "capacity", "resolved_by": "E3",
                    "reason": "source of Beta"}])


@pytest.mark.parametrize("predicate,verdict", [
    ("evidence_refs_resolve", "unresolved"), ("contract_coverage", "uncovered"),
    ("delivered_anchor_consistency", "insufficient"), ("invented_predicate", "violated"),
])
@pytest.mark.parametrize("runner", ["deterministic", ""])
def test_custom_authority_table_cannot_promote_diagnostic_or_unknown_builtin(predicate, verdict, runner):
    evidence = {
        "action": "convict_mechanical_violation", "intervention_id": "guard",
        "evidence_intervention_id": "evidence", "failure_family": "synthetic",
        "predicate": predicate, "verdict": verdict, "runner": runner,
    }
    result = adjudicate(
        packet=_packet(), judge_result={"ok": True, "parsed": {"is_hallucination": False,
                            "basis": None, "confidence": "high", "contracts": []}},
        judge_status={"state": "ok"}, guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[evidence], predicate_conviction_verdicts={
            (predicate, runner): {verdict}, predicate: {verdict},
        },
    )
    assert result["state"] != "verified_error"


def test_answer_delivery_survives_a_closing_status_tool():
    packet = build_packet({"question": "Report the capacity.", "history": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_final_answer", "arguments": {"answer": "10 units"}}],
         "tool_responses": ["ok"]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "terminate", "arguments": {"status": "success"}}],
         "tool_responses": ["success"]},
    ]})
    assert run_predicate("final_delivery_present", packet)["verdict"] == "present"
