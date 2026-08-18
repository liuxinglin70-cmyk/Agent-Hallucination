from __future__ import annotations

import pytest

from detector_v2.defense import run_defense
from detector_v2.judge import run_judge
from detector_v2.provider import ProviderError

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

JUDGE_TRUE = {
    "is_hallucination": True,
    "hallucination_step": 2,
    "hallucination_reason": "wrong value",
    "delivered_result": "5",
    "correct_result": "4",
    "basis": "derivation",
    "confidence": "high",
    "evidence_refs": ["event_002"],
    "root_cause": {"step": 2, "quote": "5", "referent": "4"},
    "contracts": [{"kind": "exact_value", "requirement": "the result 4"}],
}


def test_run_judge_ok(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    result, status = run_judge(packet=sample_packet, config=CONFIG)
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is True
    assert status == {
        "stage": "judge", "state": "ok", "attempts": 1,
        "view": "complete", "warnings": [],
    }


def test_run_judge_contract_repair(sample_packet, monkeypatch):
    outputs = [
        {"is_hallucination": "yes"},
        JUDGE_TRUE,
    ]

    def fake_call(*, route, system, user, purpose):
        return outputs.pop(0), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    result, status = run_judge(packet=sample_packet, config=CONFIG)
    assert result["ok"] is True
    assert status["warnings"][0]["error"].startswith("contract:")


def test_run_judge_provider_error(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        raise ProviderError("judge", "boom", attempts=1)

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    result, status = run_judge(packet=sample_packet, config=CONFIG)
    assert result["ok"] is False
    assert status["state"] == "provider_error"


POSITIVE = {
    "source": "judge",
    "step": 2,
    "reason": "wrong value",
    "delivered_result": "5",
    "correct_result": "4",
    "evidence_refs": ["event_002"],
}


def test_run_defense_not_needed(sample_packet):
    result, status = run_defense(
        packet=sample_packet, positive={}, config=CONFIG,
    )
    assert result["ok"] is True
    assert status["state"] == "not_needed"


def test_run_defense_refuted(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        return {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": ["event_002"],
            "reason": "the value is correct",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    result, status = run_defense(
        packet=sample_packet, positive=POSITIVE, config=CONFIG,
    )
    assert result["parsed"]["verdict"] == "refuted"
    assert status["state"] == "ok"


def test_run_defense_rejects_refuted_without_resolved_evidence(
    sample_packet, monkeypatch,
):
    def fake_call(*, route, system, user, purpose):
        return {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": [],
            "reason": "the value is correct",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    result, status = run_defense(
        packet=sample_packet, positive=POSITIVE, config=CONFIG,
    )
    assert result["ok"] is False
    assert status["state"] == "contract_error"


def test_run_defense_rejects_unknown_evidence_reference(
    sample_packet, monkeypatch,
):
    def fake_call(*, route, system, user, purpose):
        return {
            "verdict": "refuted",
            "confirmed_step": None,
            "evidence_refs": ["event_999"],
            "reason": "the value is correct",
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    result, status = run_defense(
        packet=sample_packet, positive=POSITIVE, config=CONFIG,
    )
    assert result["ok"] is False
    assert status["state"] == "contract_error"


def test_run_defense_inconclusive_keeps_judge_on_failure(
    sample_packet, monkeypatch,
):
    def fake_call(*, route, system, user, purpose):
        raise ProviderError("defense", "boom", attempts=1)

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    result, status = run_defense(
        packet=sample_packet, positive=POSITIVE, config=CONFIG,
    )
    assert result["ok"] is False
    assert status["state"] == "provider_error"
