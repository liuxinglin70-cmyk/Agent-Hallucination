from __future__ import annotations

from detector.recaller import run_causal_upgrade

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


def _candidates():
    return [{
        "candidate_id": "c1",
        "claim": "wrote 5 instead of 4",
        "locus_event_id": "event_002",
        "evidence_refs": ["event_002"],
        "quote": "5",
    }]


def test_missing_row_is_repaired_not_silently_negative(sample_packet, monkeypatch):
    purposes = []

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        purposes.append(purpose)
        if "coverage" in purpose:
            return {
                "upgrades": [{
                    "candidate_id": "c1", "content_error": True,
                    "causal": True, "corrected_step": 2, "reason": "wrong value",
                }]
            }, {"attempts": 2, "model": "m", "usage": {}}
        return {"upgrades": []}, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert "causal_upgrade_coverage_repair" in purposes
    assert status["state"] == "ok"
    assert result["coverage"] == {
        "expected": 1, "answered": 1, "missing": [],
    }
    assert result["upgraded_candidate"]["candidate_id"] == "c1"


def test_still_missing_row_is_explicit_degraded(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {"upgrades": []}, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "degraded_missing_rows"
    assert result["ok"] is False
    assert result["error_type"] == "degraded_missing_rows"
    assert result["upgrades"][0]["coverage"] == "missing"
    assert result["upgraded_candidate"] is None
