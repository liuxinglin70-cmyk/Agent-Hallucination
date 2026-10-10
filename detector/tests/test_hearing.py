from detector.hearing import hearing_target, judge_target, recall_target


def test_recall_target_cannot_carry_candidate_injected_judge_anchors():
    candidate = {"candidate_id": "second", "corrected_step": 2, "claim": "candidate claim",
                 "evidence_refs": ["event_002"], "evidence_citations": ["E1"], "quote": "observed",
                 "delivered_result": "private judge delivery", "correct_result": "private answer",
                 "confidence": "high", "is_hallucination": True, "referent": "private referent"}
    contracts = [{"kind": "exact_value", "requirement": "task obligation"}]
    target = recall_target(candidate, contracts)
    assert target["candidate_id"] == "second" and target["step"] == 2
    assert target["delivered_result"] == target["correct_result"] == target["confidence"] == target["referent"] == ""
    assert "is_hallucination" not in target
    target["contracts"][0]["requirement"] = "changed"
    target["evidence_refs"].clear()
    assert contracts[0]["requirement"] == "task obligation"
    assert candidate["evidence_refs"] == ["event_002"]


def test_hearing_projection_keeps_consumed_inputs_and_excludes_stage_outputs():
    parsed = {"hallucination_step": 1, "hallucination_reason": "claim", "confidence": "low",
              "contracts": [{"kind": "exact_value", "requirement": "4"}],
              "root_cause": {"quote": "5", "referent": "4"}, "evidence_refs": ["event_001"],
              "delivered_result": "5", "correct_result": "4"}
    target = judge_target(parsed)
    projected = hearing_target({**target, "defense_result": {"ok": True}, "tracer_status": {"state": "ok"}})
    assert projected == target
    projected["contracts"].clear()
    assert target["contracts"] == parsed["contracts"]
    assert target["confidence"] == "low" and target["referent"] == "4"
