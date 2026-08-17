from __future__ import annotations

from detector_v2.api import Detector

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


def test_detect_rejects_extra_fields(sample_record):
    detector = Detector(CONFIG, require_credentials=False)
    bad = {**sample_record, "true_answer": "4"}
    try:
        detector.detect(bad)
    except Exception as exc:  # noqa: BLE001
        assert "allows only" in str(exc)
    else:
        raise AssertionError("extra field was not rejected")
