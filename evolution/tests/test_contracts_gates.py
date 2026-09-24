from __future__ import annotations

import pytest

from evolution.config import load_config
from evolution.contracts import (
    candidate_contains_case_id,
    make_intervention,
    make_registry,
)
from evolution.gates import (
    defect_candidates,
    requires_fresh_evaluation,
    structural_gate,
    harmlessness_gate,
)
from detector.interventions import validate_registry
from evolution.worksheet import failure_family, refine_failure_families


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


def test_structural_rejects_case_id_leak():
    config = load_config()
    leak = _candidate()
    leak.pop("content_hash")
    leak["body"]["directive"] = "Only apply to BFCL/001.json and answer false."
    verdict = structural_gate(leak, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "case id" in verdict["reason"]


def test_structural_rejects_unregistered_executor():
    config = load_config()
    candidate = _candidate()
    candidate.pop("content_hash")
    candidate["hook"] = "does_not_exist.system"
    verdict = structural_gate(candidate, active_entries=[], config=config)
    assert verdict["passed"] is False
    assert "schema/executor" in verdict["reason"]


def test_structural_rejects_redundant_active_text():
    config = load_config()
    first = _candidate("IV-1-001")
    duplicate = _candidate("IV-1-002")
    verdict = structural_gate(
        duplicate,
        active_entries=[first],
        config=config,
    )
    assert verdict["passed"] is False
    assert "redundant" in verdict["reason"]


def test_llm_proposer_diversifies_across_mixed_families(monkeypatch):
    """One drafting call covers both an L1 evidence row and an L2 guard."""
    import evolution.proposer as proposer

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
    import evolution.proposer as proposer

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
            "proposer": {"max_output_tokens": 2048, "max_repair_calls": 1},
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
    assert calls[0]["route"]["max_tokens"] == 2048
    assert "extra_body" not in calls[0]["route"]
    assert "A/001.json" not in calls[0]["user"]
    assert "IV-0-001" in calls[0]["user"]  # active registry digest is in prompt
    assert batch.candidates == []
    assert batch.notes[0]["usage"]["total_tokens"] == 9
    rejections = {note.get("intervention_id"): note for note in batch.notes[1:]}
    assert rejections["IV-3-002"]["rejected"] == "invalid_contract"
    assert rejections["IV-3-003"]["rejected"] == "echo_of_active"


def test_llm_proposer_thinking_level_override(monkeypatch):
    import evolution.proposer as proposer

    monkeypatch.setattr(
        proposer,
        "load_detector_config",
        lambda _path: {"judge_route": {"endpoint": "https://example.test/v1", "model": "m"}},
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        proposer, "provider_call",
        lambda **kwargs: (calls.append(kwargs) or {"interventions": []},
                          {"purpose": "evolution_proposer", "model": "m",
                           "attempts": 1, "usage": {}}),
    )
    # Default and "" both omit the field: GLM-5.3-flash rejects any explicit
    # thinking value on a large fraction of replicas.
    for config_level, expected in (
        (None, None),
        ("", None),
        ("high", {"thinking": {"type": "high"}}),
    ):
        calls.clear()
        block = {"max_output_tokens": 4096}
        if config_level is not None:
            block["thinking_level"] = config_level
        llm = proposer.LLMProposer({
            "detector_config": "ignored.json",
            "budget": {"llm_calls": 1},
            "proposer": block,
        })
        llm.propose([{
            "failure_family": "FAM-fn-test", "count": 2,
            "failure_cases": ["A/001.json", "A/002.json"], "sources": ["A"],
        }], cycle=1)
        assert calls[0]["route"].get("extra_body") == expected, config_level


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


def test_harmlessness_gate_is_pure_over_paired_rows(tmp_path):
    """HARMLESSNESS scores precomputed rows; reroll accounting lives in the caller."""
    config = load_config()
    # This test exercises the paired-delta logic on a 1 TP / 1 TN sentinel, so
    # it opts out of the (higher) default coverage floor explicitly. The floor
    # itself is covered by test_harmlessness_floor_blocks_thin_sentinels.
    config["gates"]["harmlessness_min_protected_tp"] = 1
    config["gates"]["harmlessness_min_protected_tn"] = 1
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
    verdict = harmlessness_gate(
        baseline_rows=baseline,
        candidate_rows=candidate_rows,
        config=config,
    )
    assert verdict["passed"] is False
    assert "new_fn=1" in verdict["reason"]
    assert verdict["reroll_control"] is None

    reroll_note = {"new_fn": 1}
    verdict = harmlessness_gate(
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
    verdict = harmlessness_gate(
        baseline_rows=baseline,
        candidate_rows=identical,
        config=config,
    )
    assert verdict["passed"] is True


def test_harmlessness_protects_only_baseline_correct_rows():
    config = load_config()
    # Tests the protected-subset logic, not the coverage floor.
    config["gates"]["harmlessness_min_protected_tp"] = 1
    config["gates"]["harmlessness_min_protected_tn"] = 1
    baseline = [
        {"case": "tp", "gold": True, "prediction": True,
         "authority": "authoritative"},
        {"case": "tn", "gold": False, "prediction": False,
         "authority": "authoritative"},
        {"case": "fn", "gold": True, "prediction": False,
         "authority": "authoritative"},
        {"case": "u", "gold": True, "prediction": None,
         "authority": "invalid"},
    ]
    candidate = [
        {"case": "tp", "gold": True, "prediction": True,
         "authority": "authoritative"},
        {"case": "tn", "gold": False, "prediction": False,
         "authority": "authoritative"},
        {"case": "fn", "gold": True, "prediction": True,
         "authority": "authoritative"},
        {"case": "u", "gold": True, "prediction": False,
         "authority": "authoritative"},
    ]
    verdict = harmlessness_gate(
        baseline_rows=baseline, candidate_rows=candidate, config=config,
    )
    assert verdict["passed"] is True
    manifest = verdict["sentinel_manifest"]
    assert manifest["protected"] == 2
    assert manifest["protected_tp"] == 1
    assert manifest["protected_tn"] == 1
    assert {item["case"] for item in manifest["excluded"]} == {"fn", "u"}


def test_harmlessness_rejects_duplicate_candidate_rows_before_filtering():
    config = load_config()
    baseline = [
        {"case": "tp", "gold": True, "prediction": True,
         "authority": "authoritative"},
        {"case": "tn", "gold": False, "prediction": False,
         "authority": "authoritative"},
    ]
    with pytest.raises(ValueError, match="duplicate active"):
        harmlessness_gate(
            baseline_rows=baseline,
            candidate_rows=[dict(baseline[0]), dict(baseline[0]), dict(baseline[1])],
            config=config,
        )


def test_llm_proposer_rejects_duplicate_in_batch(monkeypatch):
    import evolution.proposer as proposer

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
    assert batch.candidates == []
    rejections = [note for note in batch.notes[1:]]
    assert rejections[1]["rejected"] == "duplicate_in_batch"
    assert any(
        note.get("rejected") == "missing_terminal_partner"
        for note in batch.notes
    )


def test_llm_proposer_budget_is_per_cycle(monkeypatch):
    import evolution.proposer as proposer
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
    import evolution.proposer as proposer

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
        "gate": "HARMLESSNESS",
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
    import evolution.proposer as proposer

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
        "gate": "STRUCTURAL",
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


def test_harmlessness_floor_blocks_thin_sentinels_by_default():
    """The default floor is 2 TP + 2 TN, not 1 + 1.

    With a floor of one, a 6-case sentinel could be satisfied by a single
    protected case of each class, leaving every other baseline-correct
    sentinel case free to regress.
    """
    config = load_config()
    assert config["gates"]["harmlessness_min_protected_tp"] == 2
    assert config["gates"]["harmlessness_min_protected_tn"] == 2
    baseline = [
        {"case": "tp", "gold": True, "prediction": True,
         "authority": "authoritative"},
        {"case": "tn", "gold": False, "prediction": False,
         "authority": "authoritative"},
    ]
    verdict = harmlessness_gate(
        baseline_rows=baseline,
        candidate_rows=[dict(row) for row in baseline],
        config=config,
    )
    assert verdict["passed"] is False
    assert "insufficient baseline-correct coverage" in verdict["reason"]
    assert verdict["evaluable"] is False
    from evolution.engine import terminal_classification
    assert terminal_classification({"harmlessness": verdict}, admitted=False) == "not_evaluated"


def test_thin_sentinel_does_not_hide_observed_harm():
    from evolution.engine import terminal_classification
    baseline = [
        {"case": "tp", "gold": True, "prediction": True, "authority": "authoritative"},
        {"case": "tn", "gold": False, "prediction": False, "authority": "authoritative"},
    ]
    candidate = [dict(row) for row in baseline]
    candidate[0]["prediction"] = False
    verdict = harmlessness_gate(baseline_rows=baseline, candidate_rows=candidate, config=load_config())
    assert verdict["delta"]["new_fn"] == 1
    assert verdict["coverage_sufficient"] is False
    assert verdict["evaluable"] is True
    assert terminal_classification({"harmlessness": verdict}, admitted=False) == "rejected"


def _delta_row(case, gold, base, active):
    return {
        "case": case, "gold": gold,
        "baseline_prediction": base, "active_prediction": active,
    }


def test_boundary_polarity_requires_a_decisive_correction():
    """Boundary claims must fix a real decision, not just decide an undecided one.

    ``marginal_cases`` counts unresolved -> correct as a gain, so a candidate
    that merely decides a previously-unresolved clean case could satisfy the
    weakest polarity with no actual error correction.
    """
    from evolution.gates import _decisive_correction

    # calibration only: U->F on a clean case. No decisive decision changed.
    calibration = {"rows": [_delta_row("a", False, None, False)]}
    assert _decisive_correction(calibration) == (0, 0)

    # a genuine correction: wrong decisive decision -> right one.
    correction = {"rows": [_delta_row("a", True, False, True)]}
    assert _decisive_correction(correction) == (1, 0)

    # a correction that breaks another decisive decision is not free.
    mixed = {"rows": [
        _delta_row("a", True, False, True),   # corrected
        _delta_row("b", False, False, True),  # regressed (TN -> FP)
    ]}
    assert _decisive_correction(mixed) == (1, 1)


# --------------------------------------------------------------------------
# P1.4: executable identity must determine behaviour
# --------------------------------------------------------------------------
def _learned_entry(*, artifact=None, runner="typed_semantic", predicate="semantic_match"):
    """An evidence_check whose runner reads an external artifact.

    The verdict vocabulary matches the registered semantic runner, so a test
    that omits the artifact fails on *that* and not on an unrelated vocabulary
    mismatch.
    """
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": predicate,
            "runner": runner,
            "output_verdicts": ["supported", "contradicted", "abstained"],
        },
        "claim": {
            "expected_behavior": "evidence about the claim",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1,
            "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    if artifact is not None:
        entry["artifact"] = dict(artifact)
    return entry


def test_two_different_artifacts_are_not_the_same_intervention():
    """§23.6: two models must never be judged the same intervention.

    Before this, the structured key stopped at the predicate name and the
    runner name, so two entries backed by different weights produced byte-equal
    keys -- the redundancy check would have called the second model a duplicate
    of the first.
    """
    from evolution.contracts import structured_key

    model_a = _learned_entry(artifact={"id": "classifier-a", "digest": "a" * 64})
    model_b = _learned_entry(artifact={"id": "classifier-b", "digest": "b" * 64})
    assert structured_key(model_a) != structured_key(model_b)

    # Same artifact under a different path is the same behaviour: the digest is
    # the identity, not the path.
    relocated = _learned_entry(
        artifact={"id": "classifier-a", "digest": "a" * 64,
                  "path": "/somewhere/else/model.bin"},
    )
    assert structured_key(relocated) == structured_key(model_a)


def test_a_learned_runner_without_a_pinned_artifact_has_no_identity():
    """An unpinned learned entry is refused, not silently compared.

    "Cannot tell whether these are the same" must not pass as "different":
    the redundancy check and every later reading would be scoring an identity
    that does not determine behaviour.
    """
    from evolution.contracts import identity_incompleteness, structured_key

    assert identity_incompleteness(_learned_entry()) != ""
    assert "no artifact.id" in identity_incompleteness(_learned_entry())
    assert "without a digest" in identity_incompleteness(
        _learned_entry(artifact={"id": "classifier-a"}),
    )
    assert identity_incompleteness(
        _learned_entry(artifact={"id": "classifier-a", "digest": "a" * 64}),
    ) == ""
    # A deterministic runner needs no artifact and is complete by construction.
    deterministic = _learned_entry(
        runner="deterministic", predicate="required_action_present",
        artifact=None,
    )
    deterministic["body"]["output_verdicts"] = [
        "satisfied", "violated", "not_applicable",
    ]
    assert identity_incompleteness(deterministic) == ""
    assert structured_key(deterministic) is not None


def test_deterministic_keys_carry_no_artifact_component():
    """A deterministic entry's identity does not depend on any artifact.

    The key grew a trailing element in P1.4, so it is not byte-identical to
    the old form -- but every deterministic entry gets the same empty suffix,
    so entries still compare equal to exactly the ones they matched before.
    That equivalence, not the tuple's shape, is what the redundancy check
    consumes.
    """
    from evolution.contracts import structured_key

    entry = _learned_entry(runner="deterministic", predicate="required_action_present")
    entry["body"]["output_verdicts"] = ["satisfied", "violated", "not_applicable"]
    key = structured_key(entry)
    assert key == (
        "evidence_check", "judge.post", ("has_tool_calls",),
        "required_action_present", "deterministic", "", ("", "", ""),
    )
    # And two entries that differ only in an unspecified artifact still match:
    # a deterministic runner has no artifact to differ on.
    twin = dict(entry)
    twin["artifact"] = {"id": "noise", "digest": "zzz"}
    assert structured_key(twin) == key


def test_a_learned_runner_is_refused_without_an_artifact_binding():
    """Now that ``typed_semantic`` is registered, validation reaches further.

    The identity check is no longer defense-in-depth: a learned entry with no
    pinned artifact is refused by the schema/executor stage itself, because the
    runner's declaration says an artifact is required.
    """
    config = load_config()
    verdict = structural_gate(
        _learned_entry(),
        active_entries=[],
        config=config,
        allowed_failure_cases=["A/001.json", "A/002.json"],
        diagnosed_families=[{
            "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        }],
    )
    assert verdict["passed"] is False
    assert "artifact" in verdict["reason"]


def _family_arguments():
    return {
        "allowed_failure_cases": ["A/001.json", "A/002.json"],
        "diagnosed_families": [{
            "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        }],
    }


def test_structural_gate_refuses_an_unpinned_runner_once_it_is_registered(
    monkeypatch,
):
    """The identity check fires the moment the runner becomes executable.

    P2.1's ``RUNNER_REGISTRY`` is what makes ``typed_semantic`` valid; this
    simulates exactly that one fact so the wiring added by P1.4 is exercised
    rather than merely present.
    """
    import evolution.gates as gates

    monkeypatch.setattr(gates, "validate_intervention", lambda *a, **k: None)
    verdict = structural_gate(
        _learned_entry(), active_entries=[], config=load_config(),
        **_family_arguments(),
    )
    assert verdict["passed"] is False
    assert "incomplete executable identity" in verdict["reason"]


def test_structural_gate_does_not_call_two_models_redundant(monkeypatch):
    """A second model must not be rejected as a duplicate of the first."""
    import evolution.gates as gates

    monkeypatch.setattr(gates, "validate_intervention", lambda *a, **k: None)
    deployed = _learned_entry(artifact={"id": "classifier-a", "digest": "a" * 64})
    candidate = _learned_entry(artifact={"id": "classifier-b", "digest": "b" * 64})
    candidate["intervention_id"] = "IV-1-002"
    verdict = structural_gate(
        candidate, active_entries=[deployed], config=load_config(),
        **_family_arguments(),
    )
    assert verdict["passed"] is True, verdict.get("reason")


def test_l0_refuses_a_learned_runner_without_an_artifact(monkeypatch):
    """The development loop rejects it before spending a measurement."""
    import evolution.development as development

    monkeypatch.setattr(
        development, "validate_intervention", lambda *a, **k: None,
    )
    from evolution.development import check_l0
    from evolution.mechanisms import infer_mechanism

    mechanism = infer_mechanism(
        "FAM-fn-x", member_cases=["A/001.json", "A/002.json"], count=2,
    )
    verdict = check_l0(_learned_entry(), mechanism=mechanism)
    assert verdict["passed"] is False
    failed = {item["check"]: item for item in verdict["failed"]}
    assert "structured_identity" in failed
    assert "artifact" in failed["structured_identity"]["detail"]
