import json

from detector.config import validate_causal_config
from detector.tests.test_next_action import failure_record
from evolution.causal_failures import build_action_failure_triplets, build_causal_failure_record, causal_failure_prompt_view


def test_missed_detection_archives_observed_triplet_without_a_positive_hearing():
    policy = validate_causal_config({}, base_dir=".")
    row = {"gold": True, "prediction": False, "authority": "authoritative",
           "result": {"runtime_valid": True}}
    failure = build_causal_failure_record(row, failure_record(), policy=policy)
    assert failure["classification"] == "missed_detection"
    triplet = failure["action_triplets"][0]
    assert triplet["predecessor_risk"]["risk_pattern"] == "observed_tool_failure"
    assert triplet["next_action"]["action_state"] == "divergent"
    assert triplet["terminal_delivery"]["deviation_status"] == "unverified"
    assert triplet["decision_authority"] == triplet["localization_authority"] == "none"
    view = causal_failure_prompt_view(failure)
    conditions = view["action_rule_conditions"]
    assert conditions[0]["divergence_pattern"] == "success_claim_without_observed_recovery"
    encoded = json.dumps(conditions)
    for literal in ("event_001", "event_002", "launch", "The operation was completed"):
        assert literal not in encoded


def test_compliant_normal_neighbor_retains_matching_risk_and_distinct_action():
    policy = validate_causal_config({}, base_dir=".")
    bad = build_action_failure_triplets(failure_record(), policy=policy)[0]
    good = build_action_failure_triplets(failure_record("The tool failed, so I cannot complete it."), policy=policy)[0]
    assert good["predecessor_risk"] == bad["predecessor_risk"]
    assert good["next_action"]["action_state"] == "compliant"
    assert bad["next_action"]["action_state"] == "divergent"


def test_triplet_budget_and_candidate_capacity_are_observed():
    policy = validate_causal_config({"top_k": 1}, base_dir=".")
    record = failure_record()
    record["history"][1]["content"] += "x" * 5000
    triplets = build_action_failure_triplets(record, policy=policy)
    assert len(triplets) == 1
    delivery = triplets[0]["terminal_delivery"]
    assert len(delivery["excerpt"]) == 1024
    assert delivery["truncated"] is True
