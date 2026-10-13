from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from detector.causal_contracts import seal
from detector.tests.test_claim_lineage import context, lineage_record
from evolution.causal_failures import build_causal_failure_record, causal_failure_prompt_view
from evolution.worksheet import localization_repair_viability
from evolution.worksheet import stable_localization_families, diagnose_failure_families, mechanism_subgroups
from evolution.proposer import HybridProposer
from evolution.mechanisms import mechanism_key


def example():
    _, cfg, target, ctx = context()
    row = {"case": "synthetic.json", "gold": True, "prediction": True,
           "authority": "authoritative", "responsible_step": 3,
           "result": {"runtime_valid": True, "localization_authority": "authoritative", "proof": {"hearings": [{
               "positive": target, "tracer_result": {"ok": True,
                   "parsed": {"step": 3},
                   "hearing_target": target, "causal_context_digest": ctx["digest"],
                   "causal_context": ctx}, "tracer_status": {"state": "ok"},
               "defense_result": {"ok": True, "parsed": {"verdict": "substantiated"}},
               "defense_status": {"state": "ok"}}]}}}
    return row, lineage_record(), cfg["causal_audit"]


def test_verified_same_claim_enters_localization_viability():
    row, record, policy = example()
    failure = build_causal_failure_record(row, record, policy=policy)
    assert failure["classification"] == "late"
    assert failure["evidence_state"] == "verified"
    assert failure["first_event_id"] == "event_001"
    assert failure["first_position"] == 1
    assert failure["first_source_step_id"] is None
    assert failure["repair_operation"] == "trace_first_error_event"
    assert localization_repair_viability(row, record, gold_steps={row["case"]: 1},
                                         causal_policy=policy)[0] == "verified_same_claim"


@pytest.mark.parametrize("change", ["resealed", "target", "record", "policy", "multiple", "refuted", "unhealthy",
                                    "stale_tracer", "failed_defense", "localization_conflict"])
def test_invalid_or_ambiguous_evidence_cannot_admit_a_localization_repair(change):
    row, record, policy = example()
    hearing = row["result"]["proof"]["hearings"][0]
    if change == "resealed":
        ctx = hearing["tracer_result"]["causal_context"]
        ctx["task_certificate"]["earliest_supported_event_id"] = "event_001"
        ctx["task_certificate"] = seal({k: v for k, v in ctx["task_certificate"].items() if k != "digest"})
        ctx = seal({k: v for k, v in ctx.items() if k != "digest"})
        hearing["tracer_result"].update(causal_context=ctx, causal_context_digest=ctx["digest"])
    elif change == "target":
        hearing["positive"] = {**hearing["positive"], "reason": "different claim"}
    elif change == "record":
        record["question"] = "Calculate 3+3"
    elif change == "policy":
        policy = {**policy, "max_dependency_depth": policy["max_dependency_depth"] + 1}
    elif change == "multiple":
        row["result"]["proof"]["hearings"].append(deepcopy(hearing))
    elif change == "refuted":
        hearing["defense_result"]["parsed"]["verdict"] = "refuted"
    elif change == "stale_tracer":
        hearing["tracer_result"]["parsed"]["step"] = 2
    elif change == "failed_defense":
        hearing["defense_result"]["ok"] = False
    elif change == "localization_conflict":
        row["result"]["localization_authority"] = "provisional"
    else:
        row["result"]["runtime_valid"] = False
    failure = build_causal_failure_record(row, record, policy=policy)
    assert failure["evidence_state"] != "verified"
    assert localization_repair_viability(row, record, gold_steps={row["case"]: 2},
                                         causal_policy=policy)[0] == "needs_review"


def test_certificate_does_not_follow_an_incompatible_annotation():
    row, record, policy = example()
    assert localization_repair_viability(row, record, gold_steps={row["case"]: 2},
                                         causal_policy=policy)[0] == "needs_review"


def test_proposer_projection_has_no_target_answer_or_case_step_literals():
    row, record, policy = example()
    failure = build_causal_failure_record(row, record, policy=policy)
    projected = causal_failure_prompt_view(failure)
    assert projected["classification"] == "late"
    assert projected["repair_operation"] == "trace_first_error_event"
    assert set(projected) == {"schema_version", "classification", "evidence_state", "scope",
                              "materiality", "first_introduction", "repair_status", "repair_operation", "reasons"}


def test_actual_family_diagnosis_mechanism_and_hybrid_input_consume_evidence(tmp_path, monkeypatch):
    row, record, policy = example()
    rows = [row, {**deepcopy(row), "case": "second.json"}]
    records = {item["case"]: record for item in rows}
    gold_steps = {item["case"]: 1 for item in rows}
    for case in records:
        (tmp_path / case).write_text(json.dumps({**record, "hallucination_step": 999,
                                               "hallucination_reason": "GOLD_SECRET"}), encoding="utf-8")
    families = stable_localization_families(rows, records=records, gold_steps=gold_steps, causal_policy=policy)
    assert len(families) == 1
    from evolution.engine import EvolutionEngine
    engine = EvolutionEngine.__new__(EvolutionEngine)
    engine.backend = SimpleNamespace(detector=SimpleNamespace(config={"causal_audit": policy}))
    engine.config = {"dataset_root": str(tmp_path)}
    engine.gold_step_map = gold_steps
    assert engine._all_families(rows) == families
    families = diagnose_failure_families(families, rows, dataset_root=str(tmp_path), causal_policy=policy)
    assert all(member["causal_failure"]["classification"] == "late"
               for member in families[0]["diagnostics"]["member_summaries"])
    families, _ = mechanism_subgroups(families)
    mechanism = families[0]["mechanism"]
    assert mechanism["detector_failure"]["failure_mode"] == "same_claim_late"
    assert mechanism["repair_hypothesis"]["operation"] == "repair_localization_source"
    other = {**deepcopy(mechanism), "member_cases": ["unrelated.json"]}
    assert mechanism_key(mechanism) == mechanism_key(other)
    candidate = HybridProposer._localization_candidates(families[0], 1)[0]
    assert "without evidence for the same" in candidate["body"]["not_when"]
    assert "identifies as the delivery" not in candidate["body"]["not_when"]
    assert "GOLD_SECRET" not in json.dumps(families)
    from evolution.tests.test_hybrid_proposer import _capture_prompt, _config
    captured = _capture_prompt(monkeypatch)
    config = _config()
    config["budget"]["llm_calls"] = 1
    HybridProposer(config).propose(families, cycle=1, max_proposals=1)
    assert captured
    assert '"causal_failure"' in captured["user"]
    assert '"same_claim_late"' in captured["user"]
    assert "GOLD_SECRET" not in captured["user"]


@pytest.mark.parametrize("step,classification", [(1, "aligned"), (2, "late")])
def test_position_comparison_does_not_invent_a_source_step_id(step, classification):
    row, record, policy = example()
    row["responsible_step"] = step
    row["result"]["proof"]["hearings"][0]["tracer_result"]["parsed"]["step"] = step
    assert build_causal_failure_record(row, record, policy=policy)["classification"] == classification


@pytest.mark.parametrize("gold,prediction,classification", [
    (True, False, "missed_detection"), (False, True, "false_positive"),
    (True, None, "runtime_unmeasurable"),
])
def test_development_outcome_classes_do_not_need_or_leak_target_answers(gold, prediction, classification):
    row, record, policy = example()
    row.update(gold=gold, prediction=prediction)
    failure = build_causal_failure_record(row, record, policy=policy)
    assert failure["classification"] == classification
    assert failure["materiality"] == "unknown"
    assert failure["first_event_id"] is None


def test_no_runtime_policy_does_not_accept_a_certificate():
    row, record, _ = example()
    assert build_causal_failure_record(row, record)["evidence_state"] == "missing"
