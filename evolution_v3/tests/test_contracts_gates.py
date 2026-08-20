from __future__ import annotations

import pytest

from evolution_v3.config import load_config
from evolution_v3.contracts import (
    candidate_contains_case_id,
    empty_registry,
    make_intervention,
    make_registry,
)
from evolution_v3.gates import (
    defect_candidates,
    requires_fresh_evaluation,
    v1_structural,
    v2_harmlessness,
)
from detector_v2.interventions import validate_registry
from evolution_v3.worksheet import failure_family, refine_failure_families


def _write_cases(root, cases):
    import json as _json

    for index, case in enumerate(cases):
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps({
                "question": f"task {index}",
                "history": [
                    {"step": 1, "role": "assistant", "content": "answer"}
                ],
                "is_hallucination": True,
                "hallucination_step": 1,
                "true_answer": "gold",
            }),
            encoding="utf-8",
        )


def _candidate(intervention_id="IV-1-001"):
    return make_intervention(
        intervention_id=intervention_id,
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report every explicit required action without a tool call.",
            "not_when": "Never apply when all required actions have tool calls.",
        },
        expected_behavior="recall scanner gains required-action candidates",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-test",
        failure_cases=["A/001.json", "A/002.json"],
    )


def test_make_intervention_and_registry_roundtrip():
    candidate = _candidate()
    registry = make_registry([candidate])
    validate_registry(registry)
    assert registry["interventions"][0]["content_hash"]


def test_v1_rejects_case_id_leak():
    config = load_config()
    leak = _candidate()
    leak.pop("content_hash")
    leak["body"]["directive"] = "Only apply to BFCL/001.json and answer false."
    verdict = v1_structural(leak, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "case id" in verdict["reason"]


def test_v1_rejects_unregistered_executor():
    config = load_config()
    candidate = _candidate()
    candidate.pop("content_hash")
    candidate["hook"] = "does_not_exist.system"
    verdict = v1_structural(candidate, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "schema/executor" in verdict["reason"]


def test_v1_rejects_redundant_active_text():
    config = load_config()
    first = _candidate("IV-1-001")
    duplicate = _candidate("IV-1-002")
    verdict = v1_structural(
        duplicate,
        active_entries=[first],
        config=config,
    )
    assert verdict["passed"] is False
    assert "redundant" in verdict["reason"]


def test_llm_proposer_diversifies_across_mixed_families(monkeypatch):
    """One drafting call covers both an L1 evidence row and an L2 guard."""
    import evolution_v3.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {"endpoint": "https://example.test/v1", "model": "m"}},
    )

    def fake_provider_call(**kwargs):
        return {
            "interventions": [
                {
                    "type": "evidence_check",
                    "hook": "judge.post",
                    "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                    "body": {
                        "predicate": "required_action_present",
                        "runner": "deterministic",
                        "output_verdicts": [
                            "satisfied", "violated", "not_applicable"
                        ],
                    },
                    "claim": {"expected_behavior": "append mechanical evidence", "target_polarity": "recall"},
                    "provenance": {"failure_family": "FAM-fn-judge_false_no_recall_candidates"},
                },
                {
                    "type": "decision_guard",
                    "hook": "adjudicate.guard",
                    "trigger": {
                        "packet_features": ["has_tool_calls"],
                        "facts": [
                            "required_action_present=violated",
                            "judge_positive=false",
                            "positive_source=none",
                        ],
                    },
                    "body": {
                        "action": "convict_mechanical_violation",
                        "evidence_predicate": "required_action_present",
                        "action_not_when": "Never when the predicate is satisfied.",
                        "action_not_when_facts": [
                            "required_action_present=satisfied"
                        ],
                    },
                    "claim": {"expected_behavior": "convict the mechanical violation", "target_polarity": "recall"},
                    "provenance": {"failure_family": "FAM-fn-judge_false_no_recall_candidates"},
                },
                {
                    "type": "decision_guard",
                    "hook": "adjudicate.guard",
                    "trigger": {
                        "packet_features": ["has_tool_calls"],
                        "facts": ["positive_source=judge"],
                    },
                    "body": {
                        "action": "withhold_conviction",
                        "action_not_when": "Never when defense is degraded.",
                        "action_not_when_facts": [
                            "defense_view_degraded=true"
                        ],
                    },
                    "claim": {"expected_behavior": "withhold the unjustified positive", "target_polarity": "precision"},
                    "provenance": {"failure_family": "FAM-fp-judge_conviction"},
                },
            ]
        }, {"purpose": "evolution_proposer", "model": "m", "attempts": 1, "usage": {"total_tokens": 9}}

    monkeypatch.setattr(proposer, "provider_call", fake_provider_call)
    batch = proposer.make_proposer({
        "detector_config": "ignored.json",
        "budget": {"llm_calls": 1},
    }).propose(
        [
            {
                "failure_family": "FAM-fn-judge_false_no_recall_candidates",
                "count": 2,
                "failure_cases": ["A/001.json", "A/002.json"],
                "sources": ["A"],
            },
            {
                "failure_family": "FAM-fp-judge_conviction",
                "count": 2,
                "failure_cases": ["A/003.json", "A/004.json"],
                "sources": ["A"],
            },
        ],
        cycle=1,
        max_proposals=3,
    )
    assert [c["type"] for c in batch.candidates] == [
        "evidence_check", "decision_guard", "decision_guard",
    ]
    assert any(
        str(c.get("hook") or "").startswith("scanner.") is False
        and c["type"] == "evidence_check"
        for c in batch.candidates
    )
    # The guard consuming the evidence row's predicate is auto-paired with it.
    assert batch.joint_groups == [["IV-1-001", "IV-1-002"]]


def test_llm_proposer_binds_provenance_locally_and_records_usage(monkeypatch):
    import evolution_v3.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {"endpoint": "https://example.test/v1", "model": "m"}},
    )
    calls = []

    def fake_provider_call(**kwargs):
        calls.append(kwargs)
        return {
            "interventions": [
                {
                    "type": "evidence_check",
                    "hook": "judge.post",
                    "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                    "body": {
                        "predicate": "required_action_present",
                        "runner": "deterministic",
                    "output_verdicts": [
                        "satisfied", "violated", "not_applicable"
                    ],
                    },
                    "claim": {"expected_behavior": "append mechanical evidence", "target_polarity": "boundary"},
                    "provenance": {"failure_family": "FAM-fn-test"},
                },
                {
                    "type": "text_boundary",
                    "hook": "scanner.system",
                    "provenance": {"failure_family": "FAM-fn-test"},
                },
                {
                    "type": "evidence_check",
                    "hook": "judge.post",
                    "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                    "body": {
                        "predicate": "verbatim_fidelity",
                        "runner": "deterministic",
                    "output_verdicts": [
                        "satisfied", "violated", "not_applicable"
                    ],
                    },
                    "claim": {"expected_behavior": "another mechanical row", "target_polarity": "boundary"},
                    "provenance": {"failure_family": "FAM-fn-test"},
                },
            ]
        }, {"purpose": "evolution_proposer", "model": "m", "attempts": 1, "usage": {"total_tokens": 9}}

    monkeypatch.setattr(proposer, "provider_call", fake_provider_call)
    active = make_intervention(
        intervention_id="IV-0-001",
        intervention_type="evidence_check",
        hook="judge.post",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "verbatim_fidelity",
            "runner": "deterministic",
            "output_verdicts": [
                "satisfied", "violated", "not_applicable"
            ],
        },
        expected_behavior="append another mechanical row",
        target_polarity="boundary",
        cycle=0,
        failure_family="FAM-fn-test",
        failure_cases=["A/001.json", "A/002.json"],
    )
    batch = proposer.make_proposer(
        {
            "detector_config": "ignored.json",
            "budget": {"llm_calls": 1},
        },
        active_entries=[active],
    ).propose(
        [{
            "failure_family": "FAM-fn-test",
            "count": 2,
            "failure_cases": ["A/001.json", "A/002.json"],
            "sources": ["A"],
        }],
        cycle=3,
    )

    assert len(calls) == 1
    assert calls[0]["route"]["max_tokens"] == 4096
    assert calls[0]["route"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "A/001.json" not in calls[0]["user"]
    assert "IV-0-001" in calls[0]["user"]  # active registry digest is in prompt
    assert [candidate["type"] for candidate in batch.candidates] == ["evidence_check"]
    assert batch.candidates[0]["provenance"] == {
        "cycle": 3,
        "failure_family": "FAM-fn-test",
        "failure_cases": ["A/001.json", "A/002.json"],
    }
    assert batch.notes[0]["usage"]["total_tokens"] == 9
    rejections = {note.get("intervention_id"): note for note in batch.notes[1:]}
    assert rejections["IV-3-002"]["rejected"] == "invalid_contract"
    assert rejections["IV-3-003"]["rejected"] == "echo_of_active"


def test_defect_candidates_have_expected_shapes():
    candidates = defect_candidates()
    assert len(candidates) == 2
    leak, noise = candidates
    assert candidate_contains_case_id(leak) == "BFCL/001.json"
    assert candidate_contains_case_id(noise) is None
    assert noise["type"] == "evidence_check"
    assert noise["hook"] == "judge.post"
    assert noise["body"]["predicate"] == "evidence_refs_resolve"
    validate_registry(make_registry(candidates))


def test_v2_harmlessness_is_pure_over_paired_rows(tmp_path):
    """V2 scores precomputed rows; reroll accounting lives in the caller."""
    config = load_config()
    sentinel = ["A/001.json", "A/002.json"]
    _write_cases(tmp_path, sentinel)
    gold_map = {"A/001.json": True, "A/002.json": False}
    baseline = [
        {"case": "A/001.json", "gold": True, "prediction": True,
         "state": "verified_error", "authority": "authoritative"},
        {"case": "A/002.json", "gold": False, "prediction": False,
         "state": "verified_clean", "authority": "authoritative"},
    ]
    candidate_rows = [
        {"case": "A/001.json", "gold": True, "prediction": False,
         "state": "verified_clean", "authority": "authoritative"},
        {"case": "A/002.json", "gold": False, "prediction": False,
         "state": "verified_clean", "authority": "authoritative"},
    ]
    verdict = v2_harmlessness(
        baseline_rows=baseline,
        candidate_rows=candidate_rows,
        config=config,
    )
    assert verdict["passed"] is False
    assert "new_fn=1" in verdict["reason"]
    assert verdict["reroll_control"] is None

    reroll_note = {"new_fn": 1}
    verdict = v2_harmlessness(
        baseline_rows=baseline,
        candidate_rows=candidate_rows,
        config=config,
        reroll_control=reroll_note,
    )
    # The gate itself never subtracts reroll noise; the engine decides which
    # control arm the rows came from.
    assert verdict["passed"] is False
    assert verdict["reroll_control"] == reroll_note

    identical = [dict(row) for row in baseline]
    verdict = v2_harmlessness(
        baseline_rows=baseline,
        candidate_rows=identical,
        config=config,
    )
    assert verdict["passed"] is True


def test_llm_proposer_rejects_duplicate_in_batch(monkeypatch):
    import evolution_v3.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {"endpoint": "https://example.test/v1", "model": "m"}},
    )

    def fake_provider_call(**kwargs):
        return {
            "interventions": [
                {
                    "type": "evidence_check",
                    "hook": "judge.post",
                    "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                    "body": {
                        "predicate": "evidence_refs_resolve",
                        "runner": "deterministic",
                        "output_verdicts": [
                            "resolved", "unresolved", "not_applicable"
                        ],
                    },
                    "claim": {"expected_behavior": "first draft", "target_polarity": "boundary"},
                    "provenance": {"failure_family": "FAM-fn-test"},
                },
                {
                    "type": "evidence_check",
                    "hook": "judge.post",
                    "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                    "body": {
                        "predicate": "evidence_refs_resolve",
                        "runner": "deterministic",
                        "output_verdicts": [
                            "resolved", "unresolved", "not_applicable"
                        ],
                    },
                    "claim": {"expected_behavior": "same mechanism restated", "target_polarity": "boundary"},
                    "provenance": {"failure_family": "FAM-fn-test"},
                },
            ]
        }, {"purpose": "evolution_proposer", "model": "m", "attempts": 1, "usage": {}}

    monkeypatch.setattr(proposer, "provider_call", fake_provider_call)
    batch = proposer.make_proposer({
        "detector_config": "ignored.json",
        "budget": {"llm_calls": 1},
    }).propose(
        [{
            "failure_family": "FAM-fn-test",
            "count": 2,
            "failure_cases": ["A/001.json", "A/002.json"],
            "sources": ["A"],
        }],
        cycle=5,
    )
    assert len(batch.candidates) == 1
    rejections = [note for note in batch.notes[1:]]
    assert rejections[1]["rejected"] == "duplicate_in_batch"


def test_llm_proposer_budget_is_per_cycle(monkeypatch):
    import evolution_v3.proposer as proposer
    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {"endpoint": "https://example.test/v1", "model": "m"}},
    )
    calls = []
    monkeypatch.setattr(
        proposer,
        "provider_call",
        lambda **kwargs: (calls.append(kwargs) or ({"interventions": []}, {"purpose": "evolution_proposer"})),
    )
    instance = proposer.make_proposer({
        "detector_config": "ignored.json", "budget": {"llm_calls": 1},
    })
    families = [{
        "failure_family": "FAM-fn-test", "count": 2,
        "failure_cases": ["A/001.json", "A/002.json"],
    }]
    instance.propose(families, cycle=1)
    same_cycle = instance.propose(families, cycle=1)
    assert same_cycle.notes[0]["provider_called"] is False
    next_cycle = instance.propose(families, cycle=2)
    assert len(calls) == 2
    assert next_cycle.notes[0]["provider_called"] is True


def test_llm_proposer_uses_rejection_feedback_and_blocks_echo(monkeypatch):
    import evolution_v3.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {
            "endpoint": "https://example.test/v1", "model": "m",
        }},
    )
    rejected = _candidate("IV-0-001")

    def fake_provider_call(**kwargs):
        assert "new_fp" in kwargs["user"]
        assert "A/001.json" not in kwargs["user"]
        return {"interventions": [{
            "type": rejected["type"],
            "hook": rejected["hook"],
            "trigger": rejected["trigger"],
            "body": rejected["body"],
            "claim": rejected["claim"],
            "provenance": {"failure_family": "FAM-fn-test"},
        }]}, {"purpose": "evolution_proposer", "attempts": 1}

    monkeypatch.setattr(proposer, "provider_call", fake_provider_call)
    instance = proposer.make_proposer({
        "detector_config": "ignored.json", "budget": {"llm_calls": 1},
    })
    instance.set_rejection_feedback([{
        "candidate": rejected,
        "type": rejected["type"],
        "hook": rejected["hook"],
        "gate": "V2",
        "reason": "sentinel harmlessness violation: new_fp=1",
        "delta": {"new_fp": 1},
    }])
    batch = instance.propose([{
        "failure_family": "FAM-fn-test",
        "count": 2,
        "failure_cases": ["A/001.json", "A/002.json"],
    }], cycle=2)
    assert batch.candidates == []
    assert batch.notes[-1]["rejected"] == "echo_of_rejected"


def test_rejection_feedback_redacts_case_ids(monkeypatch):
    import evolution_v3.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {
            "endpoint": "https://example.test/v1", "model": "m",
        }},
    )

    def fake_provider_call(**kwargs):
        assert "BFCL/001.json" not in kwargs["user"]
        assert "[case]" in kwargs["user"]
        return {"interventions": []}, {"purpose": "evolution_proposer"}

    monkeypatch.setattr(proposer, "provider_call", fake_provider_call)
    instance = proposer.make_proposer({
        "detector_config": "ignored.json", "budget": {"llm_calls": 1},
    })
    instance.set_rejection_feedback([{
        "gate": "V1",
        "reason": "case id leak in directive: BFCL/001.json",
    }])
    instance.propose([{
        "failure_family": "FAM-fn-test",
        "count": 2,
        "failure_cases": ["A/001.json", "A/002.json"],
    }], cycle=2)


def test_failure_family_keys_execution_path_not_contract_kinds():
    def row(*, recall_state, contracts):
        return {
            "gold": True,
            "prediction": False,
            "result": {
                "proof": {
                    "judge": {
                        "is_hallucination": False,
                        "confidence": "high",
                        "contracts": [
                            {"kind": kind, "requirement": kind}
                            for kind in contracts
                        ],
                    },
                    "recall_candidates": [],
                    "terminal_basis": {"decisive_facts": {}},
                },
                "stage_status": {
                    "judge": {"state": "ok"},
                    "recall_scanner": {"state": recall_state},
                    "causal_upgrade": {"state": "not_needed"},
                },
                "providers": {"judge": {"vote": {
                    "cast": 3, "positives": 0, "tie": False,
                }}},
            },
        }

    skipped = failure_family(
        row(recall_state="not_needed", contracts=["exact_value"])
    )
    scanned = failure_family(
        row(recall_state="ok", contracts=["exact_value"])
    )
    required_action = failure_family(
        row(recall_state="not_needed", contracts=["required_action"])
    )
    # Different execution path (recall ran vs skipped) is a different family.
    assert skipped != scanned
    # Contract kinds are instance-level detail: they must NOT split a family,
    # or every heterogeneous patrol degenerates into singleton families.
    assert skipped == required_action
    assert skipped.startswith("FAM-fn-judge_false_no_recall_candidates-")


def test_only_upstream_guard_action_requires_fresh_evaluation():
    guard = {
        "type": "decision_guard",
        "hook": "adjudicate.guard",
        "body": {"action": "convict_mechanical_violation"},
    }
    assert requires_fresh_evaluation([guard]) is False
    guard["body"]["action"] = "require_full_view_defense"
    assert requires_fresh_evaluation([guard]) is True


def test_predicate_profiles_refine_execution_family():
    family = {
        "failure_family": "FAM-fn-x-signature",
        "failure_cases": ["A/001.json", "A/002.json", "A/003.json"],
        "count": 3,
        "diagnostics": {
            "member_summaries": [
                {"predicates": {"required_action_present": "violated"}},
                {"predicates": {"required_action_present": "violated"}},
                {"predicates": {"required_action_present": "not_applicable"}},
            ],
        },
    }
    refined = refine_failure_families([family])
    assert len(refined) == 1
    assert refined[0]["failure_cases"] == ["A/001.json", "A/002.json"]
    assert refined[0]["diagnostics"]["predicate_signals"][
        "required_action_present"
    ] == {"applicable": 2, "violations": 2}


def test_refine_keeps_heterogeneous_family_instead_of_dropping():
    family = {
        "failure_family": "FAM-fn-x-signature",
        "failure_cases": ["A/001.json", "A/002.json", "A/003.json"],
        "count": 3,
        "diagnostics": {
            "member_summaries": [
                {"predicates": {"required_action_present": "violated"}},
                {"predicates": {"verbatim_fidelity": "violated"}},
                {"predicates": {"final_delivery_present": "missing"}},
            ],
        },
    }
    refined = refine_failure_families([family])
    assert len(refined) == 1
    assert refined[0]["failure_cases"] == ["A/001.json", "A/002.json", "A/003.json"]
    assert refined[0]["profile_homogeneous"] is False
