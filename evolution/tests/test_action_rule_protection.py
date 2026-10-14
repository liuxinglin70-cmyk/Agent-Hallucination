from copy import deepcopy

import pytest

from detector.config import validate_causal_config
from detector.tests.test_next_action import failure_record
from evolution.causal_failures import shared_action_rule_conditions
from evolution.development import check_l0
from evolution.proposer import HybridProposer
from evolution.tests.test_development import mechanism


def action_family():
    conditions = [{"risk_pattern": "observed_tool_failure", "action_state": "divergent",
                   "divergence_pattern": "success_claim_without_observed_recovery"}]
    return {"failure_family": "FAM-loc-late-content_step", "failure_cases": ["A/001.json", "B/002.json"],
            "diagnostics": {"member_summaries": [
                {"causal_failure": {"action_rule_conditions": deepcopy(conditions)}} for _ in range(2)]}}


def normal(case, *, retry=False):
    record = failure_record("The operation was completed." if retry else "The tool failed; I cannot complete it.",
                            retry={"success": True} if retry else None)
    return {"case": case, "gold": False, "prediction": False, "authority": "authoritative",
            "result": {"runtime_valid": True}, "record": record}


def action_candidate():
    return HybridProposer._action_localization_candidate(action_family(), 1)


def action_mechanism():
    value = mechanism(stage="step_tracer", operation="repair_localization_source")
    value["action_boundary_patterns"] = shared_action_rule_conditions(action_family())
    return value


def protection_check(neighbors, *, replay=None, policy=True):
    result = check_l0(action_candidate(), mechanism=action_mechanism(), normal_neighbors=neighbors,
                      causal_policy=validate_causal_config({}, base_dir=".") if policy else None,
                      replay_neighbor_cases=replay, target_kind="localization")
    return next(check for check in result["checks"] if check["check"] == "action_normal_neighbor_coverage")


def test_compound_template_is_tracing_only_and_requires_shared_both_conditions():
    family = action_family()
    assert shared_action_rule_conditions(family) == [{"risk_pattern": "observed_tool_failure",
                                                    "divergence_pattern": "success_claim_without_observed_recovery"}]
    candidate = action_candidate()
    assert candidate["hook"] == "tracer.system"
    assert candidate["claim"]["target_kind"] == "localization"
    assert " AND " in candidate["body"]["directive"]
    assert "honest failure report" in candidate["body"]["not_when"]
    family["diagnostics"]["member_summaries"][1]["causal_failure"]["action_rule_conditions"][0]["action_state"] = "compliant"
    assert shared_action_rule_conditions(family) == []


@pytest.mark.parametrize("mode", ["missing", "only_one", "wrong_action", "unhealthy", "not_replayed", "no_policy"])
def test_compound_rule_cannot_pass_without_measured_matching_normals(mode):
    neighbors = [normal("normal-a"), normal("normal-b", retry=True)]
    replay = [neighbor["case"] for neighbor in neighbors]
    if mode == "missing":
        neighbors = []
    elif mode == "only_one":
        neighbors.pop()
    elif mode == "wrong_action":
        neighbors[0]["record"] = failure_record()
    elif mode == "unhealthy":
        neighbors[0]["result"]["runtime_valid"] = False
    elif mode == "not_replayed":
        replay = ["normal-a"]
    assert protection_check(neighbors, replay=replay, policy=mode != "no_policy")["passed"] is False


def test_recovery_and_honest_failure_neighbors_satisfy_bounded_protection():
    neighbors = [normal("normal-a"), normal("normal-b", retry=True)]
    assert protection_check(neighbors, replay=["normal-a", "normal-b"])["passed"] is True


def test_family_diagnosis_and_engine_replay_targets_include_matching_normal_library(tmp_path):
    import json
    from evolution.engine import EvolutionEngine
    from evolution.worksheet import diagnose_failure_families
    family = action_family()
    family["mechanism"] = action_mechanism()
    rows = []
    for case in family["failure_cases"]:
        path = tmp_path / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failure_record()), encoding="utf-8")
        rows.append({"case": case, "gold": True, "prediction": True, "authority": "authoritative",
                     "responsible_step": 2, "result": {"runtime_valid": True}})
    for row in [normal("normal-a.json"), normal("normal-b.json", retry=True)]:
        record = row.pop("record")
        (tmp_path / row["case"]).write_text(json.dumps(record), encoding="utf-8")
        rows.append(row)
    diagnosed = diagnose_failure_families([family], rows, dataset_root=str(tmp_path),
                                          causal_policy=validate_causal_config({}, base_dir="."))[0]
    assert {row["case"] for row in diagnosed["normal_action_neighbors"]} == {"normal-a.json", "normal-b.json"}
    engine = EvolutionEngine.__new__(EvolutionEngine)
    targets, skipped = engine._development_targets([diagnosed])
    assert skipped == []
    assert {"normal-a.json", "normal-b.json"} <= set(targets[0]["neighbor_cases"])
    assert targets[0]["mechanism"]["action_boundary_patterns"]


def test_target_localization_gain_cannot_freeze_a_rule_that_harms_a_matching_normal(tmp_path):
    from evolution.development import CandidateDeveloper, DevelopmentBudget
    from evolution.tests.test_development import row, fired
    candidate = action_candidate()
    normal_rows = [normal("normal-a"), normal("normal-b", retry=True)]
    seen = []
    def replay(cases, **kwargs):
        seen.append(set(cases))
        measured = [row("A/001.json", arm="baseline", gold=True, prediction=True, step=2),
                    row("A/001.json", arm="candidate", gold=True, prediction=True, step=1,
                        proof=fired(candidate["intervention_id"]))]
        for case in ("normal-a", "normal-b"):
            measured.extend([row(case, arm="baseline", gold=False, prediction=False),
                             row(case, arm="candidate", gold=False, prediction=case == "normal-a",
                                 step=1 if case == "normal-a" else None, proof=fired(candidate["intervention_id"]))])
        return measured
    developer = CandidateDeveloper(output_dir=tmp_path, budget=DevelopmentBudget(),
                                   mechanism=action_mechanism(), target_cases=["A/001.json"],
                                   neighbor_cases=["normal-a", "normal-b"], normal_neighbors=normal_rows,
                                   causal_policy=validate_causal_config({}, base_dir="."),
                                   target_kind="localization", gold_steps={"A/001.json": 1}, l1_runner=replay)
    result = developer.develop(lambda *args: [candidate], cycle=1, max_versions=1)
    assert seen == [{"A/001.json", "normal-a", "normal-b"}]
    assert result["versions"][0]["status"] == "failed_l1"
    with pytest.raises(ValueError):
        developer.freeze("v001")
