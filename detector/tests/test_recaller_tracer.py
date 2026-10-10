from __future__ import annotations

from detector.recaller import run_causal_upgrade, run_recall_scan
from detector.step_tracer import run_step_tracer

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
    def fake_call(*, route, system, user, purpose, cache_tag=""):
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

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
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


def test_recall_candidate_rejects_refs_for_another_locus(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {"candidates": [{
            "candidate_id": "c1",
            "claim": "wrong value",
            "locus_event_id": "event_002",
            "evidence_refs": ["event_001"],
            "quote": "5",
        }]}, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_recall_scan(packet=sample_packet, config=CONFIG)
    assert result["ok"] is False
    assert status["state"] == "contract_error"


def test_upgrade_rejects_corrected_step_without_supporting_ref(sample_packet):
    from detector.recaller import _parse_upgrades

    candidates = [{
        "candidate_id": "c1",
        "claim": "wrong value",
        "locus_event_id": "event_001",
        "evidence_refs": ["event_001"],
        "quote": "wrong",
    }]
    rows, missing, error = _parse_upgrades(
        {"upgrades": [{
            "candidate_id": "c1", "content_error": True, "causal": True,
            "corrected_step": 2, "reason": "wrong step",
        }]},
        candidates,
        sample_packet,
    )
    assert rows is None
    assert missing == []
    assert "do not support corrected_step" in error


def test_step_tracer(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        return {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.step_tracer.provider_call", fake_call)
    result, status = run_step_tracer(
        packet=sample_packet, delivered_result="5", correct_result="4",
        config=CONFIG,
    )
    assert result["parsed"]["step"] == 2
    assert status["state"] == "ok"


def test_step_tracer_does_not_inherit_judge_vote_count(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        return {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_002"], "reason": "earliest wrong step",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.step_tracer.provider_call", fake_call)
    config = {**CONFIG, "judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_step_tracer(
        packet=sample_packet, delivered_result="5", correct_result="4",
        config=config,
    )
    assert result["parsed"]["step"] == 2
    assert calls == ["step_tracer"]
    assert "vote" not in result["provider"]


def test_step_tracer_rejects_refs_for_another_step(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        return {
            "step": 2, "quote": "5", "referent": "4",
            "evidence_refs": ["event_001"], "reason": "wrong step",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.step_tracer.provider_call", fake_call)
    result, status = run_step_tracer(
        packet=sample_packet, delivered_result="5", correct_result="4",
        config=CONFIG,
    )
    assert result["ok"] is False
    assert status["state"] == "contract_error"
