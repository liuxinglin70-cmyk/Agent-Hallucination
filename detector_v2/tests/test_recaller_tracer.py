from __future__ import annotations

from detector_v2.recaller import run_causal_upgrade, run_recall_scan
from detector_v2.step_tracer import run_step_tracer

CONFIG = {
    "judge_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "defense_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
    "recall_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
}


def test_recall_scan_and_upgrade(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        if "upgrade" in purpose:
            return {
                "upgrades": [{
                    "candidate_id": "c1", "content_error": True,
                    "causal": True, "corrected_step": 2, "reason": "wrong value",
                }]
            }, {"attempts": 1, "model": "m", "usage": {}}
        return {
            "candidates": [{
                "candidate_id": "c1",
                "claim": "the agent wrote 5 instead of 4",
                "locus_event_id": "event_002",
                "evidence_refs": ["event_002"],
                "quote": "5",
            }]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.recaller.provider_call", fake_call)
    recall_result, recall_status = run_recall_scan(
        packet=sample_packet, config=CONFIG,
    )
    assert recall_status["state"] == "ok"
    upgrade_result, upgrade_status = run_causal_upgrade(
        packet=sample_packet, candidates=recall_result["candidates"],
        config=CONFIG,
    )
    assert upgrade_status["state"] == "ok"
    assert upgrade_result["upgraded_candidate"]["candidate_id"] == "c1"
    assert upgrade_result["upgraded_candidate"]["corrected_step"] == 2


def test_step_tracer(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        return {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.step_tracer.provider_call", fake_call)
    result, status = run_step_tracer(
        packet=sample_packet, delivered_result="5", correct_result="4",
        config=CONFIG,
    )
    assert result["parsed"]["step"] == 2
    assert status["state"] == "ok"
