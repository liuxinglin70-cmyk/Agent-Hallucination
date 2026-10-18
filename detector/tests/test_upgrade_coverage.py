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


def test_upgrade_rejection_detail_when_content_error_not_established(sample_packet, monkeypatch):
    """测试内容错误未确立时的详细拒绝原因"""
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {
            "upgrades": [{
                "candidate_id": "c1", "content_error": False,
                "causal": True, "corrected_step": None, "reason": "observation matches claim",
            }]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "ok"
    assert result["upgraded_candidates"] == []
    assert result["upgrades"][0]["upgrade_rejection"]["primary_reason"] == "content_error_not_established"
    assert result["upgrades"][0]["upgrade_rejection"]["explanation"] == "no votes confirmed content error"


def test_upgrade_rejection_detail_when_causal_not_established(sample_packet, monkeypatch):
    """测试材料性未确立时的详细拒绝原因"""
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {
            "upgrades": [{
                "candidate_id": "c1", "content_error": True,
                "causal": False, "corrected_step": None, "reason": "error not in delivery path",
            }]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "ok"
    assert result["upgraded_candidates"] == []
    assert result["upgrades"][0]["upgrade_rejection"]["primary_reason"] == "causal_not_established"
    assert result["upgrades"][0]["upgrade_rejection"]["explanation"] == "no votes confirmed material impact to task"


def test_upgrade_rejection_detail_with_vote_reasons(sample_packet, monkeypatch):
    """测试拒绝诊断包含投票理由样本"""
    config_three_votes = {**CONFIG, "judge_route": {**CONFIG["judge_route"], "votes": 3}}
    call_count = [0]

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "upgrades": [{"candidate_id": "c1", "content_error": False, "causal": True, "corrected_step": None, "reason": "reason1"}]
            }, {"attempts": 1, "model": "m", "usage": {}}
        elif call_count[0] == 2:
            return {
                "upgrades": [{"candidate_id": "c1", "content_error": False, "causal": False, "corrected_step": None, "reason": "reason2"}]
            }, {"attempts": 1, "model": "m", "usage": {}}
        else:
            return {
                "upgrades": [{"candidate_id": "c1", "content_error": True, "causal": False, "corrected_step": None, "reason": "reason3"}]
            }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=config_three_votes,
    )
    assert status["state"] == "ok"
    assert result["upgraded_candidates"] == []
    rejection = result["upgrades"][0]["upgrade_rejection"]
    assert "vote_reasons" in rejection
    assert len(rejection["vote_reasons"]) == 3
    assert rejection["vote_reasons"][0]["content_error"] is False
    assert rejection["vote_reasons"][2]["reason"] == "reason3"


def test_upgraded_candidate_has_no_rejection_detail(sample_packet, monkeypatch):
    """测试成功升级的候选没有拒绝诊断"""
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {
            "upgrades": [{
                "candidate_id": "c1", "content_error": True,
                "causal": True, "corrected_step": 2, "reason": "wrong value",
            }]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "ok"
    assert len(result["upgraded_candidates"]) == 1
    assert "upgrade_rejection" not in result["upgrades"][0]
