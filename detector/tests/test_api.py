from __future__ import annotations

import pytest
from detector.api import Detector, _judge_negative_needs_recall

CONFIG = {
    "causal_audit": {"mode": "off"},
    "judge_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "defense_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
}


@pytest.mark.parametrize("frozen_argument", ["frozen_stages", "frozen_judge"])
@pytest.mark.parametrize("envelope", ["complete", "hearings_only"])
def test_full_view_guard_refuses_external_frozen_local_defense(monkeypatch, frozen_argument, envelope):
    from detector.interventions import compile_runtime_plan
    from detector.packet import build_packet
    from detector.tests.test_causal_audit import record
    from evolution.backends import LiveDetectorBackend
    from evolution.contracts import make_intervention, make_registry

    calls = []
    defense_inputs = []

    def transport(**kwargs):
        purpose = kwargs["purpose"]
        calls.append(purpose)
        if purpose == "judge":
            parsed = {"is_hallucination": True, "hallucination_step": 1,
                      "hallucination_reason": "wrong arithmetic", "confidence": "high",
                      "contracts": [{"kind": "exact_value", "requirement": "calculate expression"}],
                      "delivered_result": "5", "correct_result": "4",
                      "root_cause": {"step": 1, "quote": "5", "referent": "4"},
                      "evidence_refs": ["event_001"]}
        elif purpose == "step_tracer":
            parsed = {"step": 1, "quote": "5", "referent": "4",
                      "reason": "wrong arithmetic", "evidence_refs": ["event_001"]}
        elif purpose == "defense":
            defense_inputs.append(kwargs["user"])
            parsed = {"verdict": "substantiated", "confirmed_step": 1,
                      "evidence_refs": ["event_001"], "reason": "verified"}
        else:
            raise AssertionError(purpose)
        return parsed, {"attempts": 1}

    monkeypatch.setattr("detector.provider._provider_call", transport)
    detector = Detector(CONFIG, require_credentials=False)
    current = record()
    current["history"].extend([
        {"role": "assistant", "content": f"intermediate note {index}"}
        for index in range(30)
    ])
    baseline = detector.detect(current)
    assert baseline["state"] == "verified_error"
    assert baseline["proof"]["hearings"][0]["defense_result"]["view_meta"]["omitted_event_ids"]
    guard = make_intervention(
        intervention_id="IV-FULL", intervention_type="decision_guard",
        hook="adjudicate.guard", packet_features=["has_tool_calls"], facts=["positive_source=judge"],
        body={"action": "require_full_view_defense", "action_not_when": "Never when confidence is low",
              "action_not_when_facts": ["judge_confidence=low"]},
        expected_behavior="defense sees the complete case", target_polarity="precision", cycle=1,
        failure_family="FAM-fp", failure_cases=["A/1.json", "A/2.json"],
    )
    registry = make_registry([guard])
    actions = compile_runtime_plan(registry).guard_actions(
        build_packet(current), {"positive_source": "judge", "judge_confidence": "high",
                                "defense_view_degraded": "false"}, record=False)
    assert "require_full_view_defense" in actions
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    if envelope == "hearings_only":
        frozen = {"hearings": frozen["hearings"], "packet_hash": frozen["packet_hash"]}
    calls.clear()
    with pytest.raises(ValueError, match="frozen stage replay"):
        detector.detect(current, interventions=registry, **{frozen_argument: frozen})
    assert calls == []
    defense_inputs.clear()
    fresh = detector.detect(current, interventions=registry)
    assert calls[0] == "judge"
    assert sorted(calls[1:]) == ["defense", "step_tracer"]
    assert not fresh["provenance"].get("frozen_judge_replay")
    assert all(f"intermediate note {index}" in defense_inputs[0] for index in range(30))
    assert not fresh["proof"]["hearings"][0]["defense_result"]["view_meta"].get("omitted_event_ids")


def test_unanimous_high_confidence_negative_skips_recall():
    result = {
        "parsed": {"is_hallucination": False, "confidence": "high"},
        "provider": {"vote": {
            "votes": 3, "cast": 3, "positives": 0,
            "winner": False, "tie": False,
        }},
    }
    config = {"judge_route": {"votes": 3}}
    assert _judge_negative_needs_recall(result, config) is False


def test_dissenting_or_incomplete_negative_runs_recall():
    config = {"judge_route": {"votes": 3}}
    for cast, positives in ((3, 1), (2, 0)):
        result = {
            "parsed": {"is_hallucination": False, "confidence": "high"},
            "provider": {"vote": {
                "votes": 3, "cast": cast, "positives": positives,
                "winner": False, "tie": False,
            }},
        }
        assert _judge_negative_needs_recall(result, config) is True


def test_single_vote_negative_keeps_recall_safety_net():
    result = {
        "parsed": {"is_hallucination": False, "confidence": "high"},
        "provider": {"model": "m"},
    }
    assert _judge_negative_needs_recall(result, CONFIG) is True


def test_detect_end_to_end_clean(sample_record, monkeypatch):
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "4",
            "correct_result": "4",
            "basis": None,
            "confidence": "high",
            "evidence_refs": [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        return {"ok": True, "candidates": []}, {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        }

    def fake_upgrade(*, packet, candidates, config, plan):
        return {"ok": True, "upgrades": [], "upgraded_candidate": None}, {
            "stage": "causal_upgrade", "state": "not_needed", "attempts": 0,
            "view": "not_rendered", "warnings": [],
        }

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    detector = Detector(CONFIG, require_credentials=False)
    result = detector.detect(sample_record, case_id="x")
    assert result["state"] == "verified_clean"
    assert result["decision_authority"] == "authoritative"


def test_detect_recall_upgrade_path(sample_record, monkeypatch):
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "5",
            "correct_result": "4",
            "basis": None,
            "confidence": "high",
            "evidence_refs": [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        return {
            "ok": True,
            "candidates": [{
                "candidate_id": "c1",
                "claim": "wrote 5 instead of 4",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
                "quote": "5",
            }],
        }, {"stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": []}

    def fake_upgrade(*, packet, candidates, config, plan):
        return {
            "ok": True,
            "upgrades": [],
            "upgraded_candidate": {
                "candidate_id": "c1", "content_error": True, "causal": True,
                "corrected_step": 2, "reason": "r",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
                "claim": "wrote 5 instead of 4", "quote": "5",
            },
        }, {"stage": "causal_upgrade", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": []}

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan, positive, intervention_prompt=None):
        assert positive.get("source") in {"judge", "recall_upgrade"}
        return {"ok": True, "parsed": {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }}, {"stage": "step_tracer", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    def fake_defense(*, packet, positive, config, plan):
        return {"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 2,
            "evidence_refs": ["event_002"], "reason": "real and causal",
        }}, {"stage": "defense", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector.hearing.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector.hearing.run_defense", fake_defense)
    detector = Detector(CONFIG, require_credentials=False)
    result = detector.detect(sample_record, case_id="x")
    assert result["state"] == "verified_error"
    assert result["responsible_step"] == 2
    assert result["proof"]["terminal_basis"]["decisive_facts"]["positive_source"] == "recall_upgrade"



@pytest.mark.parametrize("replay", [False, True])
def test_recall_trigger_forces_recall_net_for_unanimous_clean_judge(
    sample_record, monkeypatch, replay, replay_inputs_for,
):
    """A typed recall.trigger overrides the high-confidence skip decision."""
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "4",
            "correct_result": "4",
            "basis": None,
            "confidence": "high",
            "evidence_refs": [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {
            "model": "m",
            "vote": {
                "votes": 3, "cast": 3, "positives": 0,
                "winner": False, "tie": False,
            },
        },
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        return {
            "ok": True,
            "candidates": [{
                "candidate_id": "c1",
                "claim": "wrong value",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
                "quote": "4",
            }],
        }, {"stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": []}

    def fake_upgrade(*, packet, candidates, config, plan):
        return {
            "ok": True,
            "upgrades": [],
            "upgraded_candidate": {
                "candidate_id": "c1", "content_error": True, "causal": True,
                "corrected_step": 2, "reason": "r",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
                "claim": "wrong value", "quote": "4",
            },
        }, {"stage": "causal_upgrade", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": []}

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan, positive, intervention_prompt=None):
        assert positive.get("source") in {"judge", "recall_upgrade"}
        return {"ok": True, "parsed": {
            "step": 2, "quote": "4", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "traced",
        }}, {"stage": "step_tracer", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    def fake_defense(*, packet, positive, config, plan):
        return {"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 2,
            "evidence_refs": ["event_002"], "reason": "real and causal",
        }}, {"stage": "defense", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    interventions = {
        "schema_version": "detector.intervention_registry.v1",
        "interventions": [{
            "schema_version": "detector.intervention.v1",
            "intervention_id": "IV-RT-001",
            "type": "recall_trigger",
            "hook": "recall.trigger",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": ["judge_positive=false"],
            },
            "body": {
                "predicate": "evo_calculator_question",
                "runner": "typed_dsl",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
                "trigger_verdicts": ["violated"],
                "require_judge_negative": True,
                "predicate_definition": {
                    "schema_version": "detector.predicate_program.v1",
                    "name": "evo_calculator_question",
                    "description": "calculator question needs recall review",
                    "verdicts": ["satisfied", "violated", "not_applicable"],
                    "conviction_verdicts": ["violated"],
                    "default_verdict": "not_applicable",
                    "rules": [{
                        "field": "question_text",
                        "operator": "contains",
                        "value": "2 + 2",
                        "verdict": "violated",
                        "detail": "arithmetic question",
                    }],
                },
            },
            "claim": {
                "expected_behavior": "force recall for arithmetic questions",
                "target_polarity": "recall",
            },
            "provenance": {
                "cycle": 1,
                "failure_family": "FAM-fn-test",
                "failure_cases": ["A/000.json", "A/001.json"],
            },
        }],
    }

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector.hearing.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector.hearing.run_defense", fake_defense)
    detector = Detector(CONFIG, require_credentials=False)
    frozen = None
    if replay:
        from detector.identity import digest
        from detector.packet import build_packet
        frozen = {"judge_result": judge_result, "judge_status": judge_status,
                  "packet_hash": digest(build_packet(sample_record)), "hearings": [],
                  "replay_inputs": replay_inputs_for(sample_record, CONFIG)}
        def unexpected_judge(**kwargs):
            raise AssertionError("paired recall must not redraw judge")
        monkeypatch.setattr("detector.api.run_judge", unexpected_judge)
    result = detector.detect(
        sample_record, case_id="x", interventions=interventions,
        frozen_stages=frozen,
    )
    assert result["state"] == "verified_error"
    deployment = result["proof"]["deployment"]
    assert any(
        item.get("event") == "recall_triggered"
        for item in deployment.get("fired") or ()
    )


def test_detect_judge_conviction_is_authoritative(sample_record, monkeypatch):
    """A judge positive that defense substantiates convicts without degradation."""
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": True,
            "hallucination_step": 2,
            "hallucination_reason": "said 5",
            "delivered_result": "5",
            "correct_result": "4",
            "basis": "derivation",
            "confidence": "high",
            "evidence_refs": ["event_002"],
            "root_cause": {"step": 2, "quote": "5", "referent": "4"},
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        raise AssertionError("recall net must not run for a conviction")

    def fake_upgrade(*, packet, candidates, config, plan):
        raise AssertionError("recall net must not run for a conviction")

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan, positive, intervention_prompt=None):
        assert positive.get("source") in {"judge", "recall_upgrade"}
        return {"ok": True, "parsed": {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }}, {"stage": "step_tracer", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    def fake_defense(*, packet, positive, config, plan):
        return {"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 2,
            "evidence_refs": ["event_002"], "reason": "real and causal",
        }}, {"stage": "defense", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector.hearing.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector.hearing.run_defense", fake_defense)
    detector = Detector(CONFIG, require_credentials=False)
    result = detector.detect(sample_record, case_id="x")
    assert result["state"] == "verified_error"
    assert result["decision_authority"] == "authoritative"
    assert result["responsible_step"] == 2
    facts = result["proof"]["terminal_basis"]["decisive_facts"]
    assert facts["positive_source"] == "judge"
    assert facts["defense"] == "substantiated"
    hearing = result["proof"]["hearings"][0]
    assert hearing["source"] == "judge"
    assert hearing["defense_result"]["parsed"]["verdict"] == "substantiated"


def test_detect_judge_refuted_and_recall_empty_is_clean(sample_record, monkeypatch):
    """The refuted judge hearing plus an empty recall net acquits."""
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": True,
            "hallucination_step": 2,
            "hallucination_reason": "said 5",
            "delivered_result": "5",
            "correct_result": "4",
            "basis": "derivation",
            "confidence": "high",
            "evidence_refs": ["event_002"],
            "root_cause": {"step": 2, "quote": "5", "referent": "4"},
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }

    def fake_judge(*, packet, config, plan):
        return judge_result, judge_status

    def fake_recall(*, packet, config, plan):
        return {"ok": True, "candidates": []}, {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        }

    def fake_upgrade(*, packet, candidates, config, plan):
        return {
            "ok": True, "upgrades": [], "upgraded_candidate": None,
            "upgraded_candidates": [],
        }, {"stage": "causal_upgrade", "state": "not_needed", "attempts": 0,
            "view": "not_rendered", "warnings": []}

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan, positive, intervention_prompt=None):
        assert positive.get("source") in {"judge", "recall_upgrade"}
        return {"ok": True, "parsed": {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }}, {"stage": "step_tracer", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    def fake_defense(*, packet, positive, config, plan):
        return {"ok": True, "parsed": {
            "verdict": "refuted", "confirmed_step": None,
            "evidence_refs": ["event_002"], "reason": "not a real error",
        }}, {"stage": "defense", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector.hearing.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector.hearing.run_defense", fake_defense)
    detector = Detector(CONFIG, require_credentials=False)
    result = detector.detect(sample_record, case_id="x")
    assert result["state"] == "verified_clean"
    assert result["decision_authority"] == "authoritative"
    hearing = result["proof"]["hearings"][0]
    assert hearing["defense_result"]["parsed"]["verdict"] == "refuted"


def test_detect_rejects_extra_fields(sample_record):
    detector = Detector(CONFIG, require_credentials=False)
    bad = {**sample_record, "true_answer": "4"}
    try:
        detector.detect(bad)
    except Exception as exc:  # noqa: BLE001
        assert "allows only" in str(exc)
    else:
        raise AssertionError("extra field was not rejected")


def test_frozen_hearing_is_reused_instead_of_rerunning_tracer_and_defense(
    sample_record, monkeypatch, replay_inputs_for,
):
    sample_record["history"][-1]["content"] = "The answer is 5."
    from detector.hearing import judge_target
    judge_result = {
        "ok": True,
        "parsed": {
            "is_hallucination": True,
            "hallucination_step": 2,
            "hallucination_reason": "said 5",
            "delivered_result": "5",
            "correct_result": "4",
            "basis": "derivation",
            "confidence": "high",
            "evidence_refs": ["event_002"],
            "root_cause": {"step": 2, "quote": "5", "referent": "4"},
            "contracts": [{"kind": "exact_value", "requirement": "4"}],
        },
        "provider": {"model": "m"},
    }
    judge_status = {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }
    frozen = {
        "judge_result": judge_result,
        "judge_status": judge_status,
        "hearings": [{
            "source": "judge",
            "candidate_id": None,
            "positive": judge_target(judge_result["parsed"]),
            "tracer_result": {"ok": True, "parsed": {
                "step": 2, "quote": "5", "referent": "4",
                "evidence_refs": ["event_002"], "reason": "frozen tracer",
            }},
            "tracer_status": {"stage": "step_tracer", "state": "ok",
                              "attempts": 0, "view": "complete", "warnings": []},
            "defense_result": {"ok": True, "parsed": {
                "verdict": "substantiated", "confirmed_step": 2,
                "evidence_refs": ["event_002"], "reason": "frozen defense",
            }},
            "defense_status": {"stage": "defense", "state": "ok",
                               "attempts": 0, "view": "complete", "warnings": []},
        }],
    }

    def unexpected(**kwargs):
        raise AssertionError("a frozen hearing must not run live stages")

    monkeypatch.setattr("detector.api.run_judge", unexpected)
    monkeypatch.setattr("detector.api.run_recall_scan", unexpected)
    monkeypatch.setattr("detector.api.run_causal_upgrade", unexpected)
    monkeypatch.setattr("detector.hearing.run_step_tracer", unexpected)
    monkeypatch.setattr("detector.hearing.run_defense", unexpected)
    detector = Detector(CONFIG, require_credentials=False)
    frozen["replay_inputs"] = replay_inputs_for(sample_record, CONFIG)
    result = detector.detect(sample_record, case_id="x", frozen_stages=frozen)
    assert result["state"] == "verified_error"
    assert result["proof"]["defense"]["verdict"] == "substantiated"
    assert result["proof"]["step_tracer"]["reason"] == "frozen tracer"


@pytest.mark.parametrize("field,value", [
    ("confidence", "low"), ("contracts", [{"kind": "exact_value", "requirement": "6"}]),
])
def test_frozen_hearing_rechecks_fields_actually_consumed_by_defense(sample_record, monkeypatch, field, value):
    sample_record["history"][-1]["content"] = "The answer is 5."
    from copy import deepcopy
    from evolution.backends import LiveDetectorBackend
    parsed = {"is_hallucination": True, "hallucination_step": 2,
              "hallucination_reason": "said 5", "delivered_result": "5", "correct_result": "4",
              "basis": "derivation", "confidence": "high", "evidence_refs": ["event_002"],
              "root_cause": {"step": 2, "quote": "5", "referent": "4"},
              "contracts": [{"kind": "exact_value", "requirement": "4"}]}
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": deepcopy(parsed)}, {"stage": "judge", "state": "ok"}))
    calls = []
    monkeypatch.setattr("detector.hearing.run_step_tracer", lambda **kwargs: (
        {"ok": True, "parsed": {"step": 2, "quote": "5", "referent": "4", "reason": "trace",
                                 "evidence_refs": ["event_002"]}}, {"stage": "step_tracer", "state": "ok"}))
    def defense(**kwargs):
        calls.append(deepcopy(kwargs["positive"]))
        return {"ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 2,
                                        "reason": "review", "evidence_refs": ["event_002"]}}, {
            "stage": "defense", "state": "ok"}
    monkeypatch.setattr("detector.hearing.run_defense", defense)
    detector = Detector(CONFIG, require_credentials=False)
    baseline = detector.detect(sample_record)
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    frozen["judge_result"]["parsed"][field] = deepcopy(value)
    calls.clear()
    result = detector.detect(sample_record, frozen_stages=frozen)
    assert len(calls) == 1
    assert calls[0][field] == value
    assert result["proof"]["hearings"][0]["positive"][field] == value
