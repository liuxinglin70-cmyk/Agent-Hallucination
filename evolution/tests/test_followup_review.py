"""Recovery identity, proposal allowance ownership and admission accounting."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from evolution.candidate_versions import CandidateVersionGraph, canonical_hash
from evolution.development_adapter import make_development_drafter
from evolution.engine import EvolutionEngine
from evolution.gates import arm_has_infrastructure_failure
from evolution.metrics import evaluate_row_state, EvaluationState, gain_opportunity, paired_localization_delta
from evolution.proposer import HybridProposer, ProposalBatch
from evolution.backends import MockDetectorBackend
from evolution.splits import EvolutionSplit
from evolution.memory import episodic_attempts
from evolution.tests.test_development import candidate
from evolution.tests.test_engine_smoke import (
    _evidence_candidate, _mechanical_guard_candidate, _config, _make_dataset,
)
from evolution.tests.test_hybrid_proposer import _capture_prompt, _llm_config, _fn_family


@pytest.mark.parametrize("remove_hash", [False, True])
def test_recovery_refuses_changed_candidate_with_stale_certification(tmp_path, remove_hash):
    graph = CandidateVersionGraph(tmp_path)
    graph.record("recall_directive", "v001", candidate(), parent=None,
                 diagnosis=None, draft_notes=[], evaluation=None, verdict=None,
                 status="ready_for_freeze")
    path = tmp_path / "v001" / "candidate.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["candidate"]["body"]["directive"] = "changed without new L1 evidence"
    record["candidate"].pop("content_hash", None)
    if remove_hash:
        record.pop("candidate_hash")
    else:
        record["candidate_hash"] = canonical_hash(record["candidate"])
    path.write_text(json.dumps(record), encoding="utf-8")
    restored = CandidateVersionGraph(tmp_path)
    with pytest.raises(ValueError, match="identity|hash|content|certif"):
        restored.load_existing()
        EvolutionEngine._recover_certified_version(SimpleNamespace(graph=restored))


@pytest.mark.parametrize("initial_allowance", [None, 7])
def test_development_allowance_does_not_leak_to_later_cycle(monkeypatch, initial_allowance):
    _capture_prompt(monkeypatch)
    proposer = HybridProposer(_llm_config())
    proposer.set_provider_call_budget(initial_allowance)
    family = _fn_family()
    draft = make_development_drafter(proposer=proposer, cycle=1, family=family,
                                    mechanism_id="M", identifier="IV-D-001",
                                    provider_budget=lambda: 1)
    draft({}, {}, None, None, 1)
    assert proposer.llm.provider_call_budget == initial_allowance
    proposer.propose([family], cycle=2, max_proposals=1)
    assert proposer.llm.calls_by_cycle[2] == 1


def test_unbindable_family_does_not_spend_request_allowance(monkeypatch):
    _capture_prompt(monkeypatch)
    proposer = HybridProposer(_llm_config())
    proposer.set_provider_call_budget(1)
    proposer.llm.propose([{"failure_family": "FAM-fn-one", "failure_cases": ["A"]}], cycle=1)
    assert proposer.llm.provider_call_budget == 1
    assert proposer.llm.calls_by_cycle == {}


@pytest.mark.parametrize("max_admits,duplicate", [(0, False), (1, False), (1, True)])
def test_joint_unit_budget_exclusion_reports_every_member(max_admits, duplicate):
    evidence, guard = _evidence_candidate(), _mechanical_guard_candidate()
    unit = {"intervention_id": guard["intervention_id"], "candidate": guard,
            "partners": [evidence]}
    engine = object.__new__(EvolutionEngine)
    engine.config = {"budget": {"max_admits": max_admits}}
    qualified = [unit, deepcopy(unit)] if duplicate else [unit]
    selection = engine._check_combination(qualified, active_entries=[],
                                         active_rows=[], fresh_control_rows=None,
                                         arms=None)
    assert selection["entries"] == []
    assert {entry["intervention_id"] for entry in selection["budget_unselected_entries"]} == {
        evidence["intervention_id"], guard["intervention_id"],
    }


@pytest.mark.parametrize("stage", ["tracer_status", "defense_status"])
def test_failed_later_hearing_is_not_a_healthy_unresolved_arm(stage):
    row = {"case": "A", "gold": True, "prediction": None, "authority": "authoritative",
           "result": {"schema_version": "detector.detection_result.v1", "state": "unresolved",
                      "runtime_valid": True, "decision_authority": "authoritative",
                      "stage_status": {}, "proof": {"hearings": [{stage: {"state": "provider_error"}}]}}}
    assert evaluate_row_state(row) == EvaluationState.INFRASTRUCTURE_FAILURE
    assert arm_has_infrastructure_failure([row])


@pytest.mark.parametrize("authority", ["invalid", ""])
@pytest.mark.parametrize("with_result", [True, False])
def test_unresolved_result_needs_authority_even_if_outer_row_claims_it(authority, with_result):
    row = {"case": "A", "gold": True, "prediction": None, "authority": "authoritative",
           "result": {"schema_version": "detector.detection_result.v1", "state": "unresolved",
                      "runtime_valid": True, "decision_authority": authority, "stage_status": {}}}
    if not with_result:
        row.pop("result")
        row["authority"] = authority
    assert evaluate_row_state(row) != EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY
    assert gain_opportunity([row])["resolution"] == 0
    assert arm_has_infrastructure_failure([row])


def test_non_authoritative_result_cannot_earn_localization_credit():
    base = {"case": "A", "gold": True, "prediction": True, "authority": "authoritative",
            "responsible_step": 1,
            "result": {"runtime_valid": True, "decision_authority": "authoritative"}}
    active = deepcopy(base)
    active["responsible_step"] = 2
    active["result"]["decision_authority"] = "invalid"
    delta = paired_localization_delta([base], [active], gold_steps={"A": 2})
    assert delta["joint_improved"] == 0


@pytest.mark.parametrize("max_admits", [0, 1, 2])
def test_joint_budget_outcome_reaches_every_members_memory(tmp_path, max_admits):
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["budget"]["max_admits"] = max_admits
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)

    class Proposer:
        name = "controlled_joint"

        def propose(self, families, *, cycle, max_proposals):
            family = next(f for f in families if f["failure_family"].startswith("FAM-fn-"))
            entries = [_evidence_candidate(), _mechanical_guard_candidate()]
            for entry in entries:
                entry["provenance"] = {"cycle": cycle, "failure_family": family["failure_family"],
                                       "failure_cases": family["failure_cases"]}
                entry.pop("content_hash", None)
            return ProposalBatch(candidates=entries, joint_groups=[["IV-1-001", "IV-1-002"]])

    backend = MockDetectorBackend(base_predictions={case: False for case in
                                  ["A/004.json", "A/012.json", "A/014.json"]},
                                  overrides={"IV-1-002": {"A/004.json": True}})
    with EvolutionEngine(config, backend=backend, split=split, mini=True, proposer=Proposer()) as engine:
        report = engine.run_cycle(1)
        attempts = {r["intervention_id"]: r for r in episodic_attempts(engine.memory_root)}
    assert len(report["gates"]) == 2
    expected = "admitted" if max_admits == 2 else "budget_unselected"
    assert {attempts[key]["outcome"] for key in ("IV-1-001", "IV-1-002")} == {expected}
    assert not any(attempts[key]["falsified"] for key in ("IV-1-001", "IV-1-002"))
