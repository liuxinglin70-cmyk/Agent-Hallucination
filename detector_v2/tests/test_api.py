from __future__ import annotations

from detector_v2.api import Detector, _judge_negative_needs_recall

CONFIG = {
    "judge_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "defense_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
}


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

    monkeypatch.setattr("detector_v2.api.run_judge", fake_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    detector = Detector(CONFIG)
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

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan):
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

    monkeypatch.setattr("detector_v2.api.run_judge", fake_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector_v2.api.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector_v2.api.run_defense", fake_defense)
    detector = Detector(CONFIG)
    result = detector.detect(sample_record, case_id="x")
    assert result["state"] == "verified_error"
    assert result["responsible_step"] == 2
    assert result["proof"]["terminal_basis"]["decisive_facts"]["positive_source"] == "recall_upgrade"


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

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan):
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

    monkeypatch.setattr("detector_v2.api.run_judge", fake_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector_v2.api.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector_v2.api.run_defense", fake_defense)
    detector = Detector(CONFIG)
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

    def fake_tracer(*, packet, delivered_result, correct_result, config, plan):
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

    monkeypatch.setattr("detector_v2.api.run_judge", fake_judge)
    monkeypatch.setattr("detector_v2.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector_v2.api.run_causal_upgrade", fake_upgrade)
    monkeypatch.setattr("detector_v2.api.run_step_tracer", fake_tracer)
    monkeypatch.setattr("detector_v2.api.run_defense", fake_defense)
    detector = Detector(CONFIG)
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
