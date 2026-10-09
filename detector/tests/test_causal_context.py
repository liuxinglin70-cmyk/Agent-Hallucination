from copy import deepcopy

import pytest

from detector.api import Detector
from detector.causal_audit import build_causal_diagnostics
from detector.config import validate_causal_config
from detector.identity import digest
from detector.packet import build_packet
from detector.runtime import AddedBudget, added_budget_scope
from detector.step_tracer import run_step_tracer
from detector.tests.test_api import CONFIG
from detector.tests.test_causal_audit import record


def positive(**changes):
    return {"source": "judge", "step": 1, "reason": "calculator returned five for 2+2",
            "quote": "5", "evidence_refs": ["event_001"],
            "delivered_result": "5", "correct_result": "4", **changes}


def config(**changes):
    return {**deepcopy(CONFIG), "causal_audit": validate_causal_config(
        {"mode": "tracer_context", "max_added_prompt_tokens_per_case": 20000, **changes}, base_dir=".")}


def diagnostics(packet, cfg):
    return build_causal_diagnostics(packet=packet, config=cfg, budget=AddedBudget(cfg["causal_audit"]),
                                    mandatory_event_ids=["event_001"], context=None, snapshot=None)


def test_context_is_bound_to_hearing_and_excludes_unrelated_candidates():
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    data = diagnostics(packet, config())
    context = context_for_hearing(data["audit"], positive=positive(), packet=packet)
    assert context["target_digest"] == digest(positive())
    assert [row["event_id"] for row in context["candidates"]] == ["event_001"]
    assert context["decision_authority"] == "none"
    context["candidates"].clear()
    assert data["audit"]["candidates"]
    assert context_for_hearing(data["audit"], positive=positive(step=2, evidence_refs=["event_002"]), packet=packet) is None


def test_context_rejects_changed_packet_and_tampered_audit():
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    audit = diagnostics(packet, config())["audit"]
    with pytest.raises(ValueError, match="packet identity"):
        context_for_hearing(audit, positive=positive(), packet=build_packet(record("6")))
    audit["candidates"][0]["claim_status"] = "refuted"
    with pytest.raises(ValueError, match="identity"):
        context_for_hearing(audit, positive=positive(), packet=packet)


def tracer_reply():
    return {"step": 1, "quote": "5", "referent": "4", "reason": "same calculator deviation",
            "evidence_refs": ["event_001"]}, {"attempts": 1}


def test_tracer_rejects_context_for_another_claim_before_provider(monkeypatch):
    from detector.causal_audit import context_for_hearing
    cfg = config()
    packet = build_packet(record())
    context = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=positive(), packet=packet)
    def unexpected(**kwargs):
        raise AssertionError("wrong hearing must be rejected before provider")
    monkeypatch.setattr("detector.step_tracer.provider_call", unexpected)
    with pytest.raises(ValueError, match="hearing identity"):
        run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                        positive=positive(reason="a different error"), causal_context=context)


def test_official_hearing_passes_context_to_real_tracer(monkeypatch):
    cfg = config()
    seen = []
    allegation = positive()
    parsed = {"is_hallucination": True, "hallucination_step": 1,
              "hallucination_reason": allegation["reason"], "root_cause": {"quote": "5"},
              "delivered_result": "5", "correct_result": "4", "evidence_refs": ["event_001"]}
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": parsed}, {"stage": "judge", "state": "ok"}))
    monkeypatch.setattr("detector.api.run_defense", lambda **kwargs: (
        {"ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 1,
                                 "evidence_refs": ["event_001"], "reason": "verified"}},
        {"stage": "defense", "state": "ok"}))
    def tracer(**kwargs):
        seen.append(kwargs["user"])
        return tracer_reply()
    monkeypatch.setattr("detector.step_tracer.provider_call", tracer)
    result = Detector(cfg, require_credentials=False).detect(record())
    assert "CAUSAL CONTEXT:" in seen[0]
    assert "literal_arithmetic.v1" in seen[0]
    assert result["state"] == "verified_error"
    assert result["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"]
    assert result["proof"]["causal_diagnostics"]["mode"] == "tracer_context"


@pytest.mark.parametrize("changed", [False, True])
def test_frozen_tracer_reuse_checks_actual_context_identity(monkeypatch, changed):
    from evolution.backends import LiveDetectorBackend
    cfg = config()
    parsed = {"is_hallucination": True, "hallucination_step": 1,
              "hallucination_reason": "wrong arithmetic", "confidence": "high",
              "contracts": [{"kind": "exact_value", "requirement": "4"}],
              "delivered_result": "5", "correct_result": "4", "basis": None,
              "root_cause": {"step": 1, "quote": "5", "referent": "4"},
              "evidence_refs": ["event_001"]}
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": parsed}, {"stage": "judge", "state": "ok"}))
    monkeypatch.setattr("detector.api.run_defense", lambda **kwargs: (
        {"ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 1,
                                 "evidence_refs": ["event_001"], "reason": "verified"}},
        {"stage": "defense", "state": "ok"}))
    calls = []
    def tracer(**kwargs):
        calls.append(kwargs)
        return tracer_reply()
    monkeypatch.setattr("detector.step_tracer.provider_call", tracer)
    baseline = Detector(cfg, require_credentials=False).detect(record())
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    if changed:
        cfg = config(policy_version="causal-v2")
    result = Detector(cfg, require_credentials=False).detect(record(), frozen_stages=frozen)
    assert result["state"] == "verified_error"
    assert len(calls) == (2 if changed else 1)


def test_repair_admits_context_again_without_resetting_budget(monkeypatch):
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    cfg = config()
    context = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=positive(), packet=packet)
    seen = []
    def provider(**kwargs):
        seen.append(kwargs["user"])
        return ({}, {}) if len(seen) == 1 else tracer_reply()
    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    with added_budget_scope(cfg["causal_audit"]) as budget:
        result, status = run_step_tracer(packet=packet, delivered_result="5", correct_result="4",
                                        config=cfg, positive=positive(), causal_context=context)
        assert budget.snapshot()["reserved_input_bytes"] > 2 * len(context["digest"])
    assert all("CAUSAL CONTEXT:" in user for user in seen)
    assert result["ok"]
    assert status["warnings"][0]["attempt"] == "initial"


def test_context_budget_failure_keeps_base_tracer_and_records_fallback(monkeypatch):
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    cfg = config()
    context = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=positive(), packet=packet)
    seen = []
    def tracer(**kwargs):
        seen.append(kwargs["user"])
        return tracer_reply()
    monkeypatch.setattr("detector.step_tracer.provider_call", tracer)
    with added_budget_scope(cfg["causal_audit"]) as budget:
        budget.reserve_context("x" * cfg["causal_audit"]["max_added_prompt_tokens_per_case"])
        result, status = run_step_tracer(packet=packet, delivered_result="5", correct_result="4",
                                        config=cfg, positive=positive(), causal_context=context)
    assert result["ok"]
    assert "CAUSAL CONTEXT:" not in seen[0]
    assert status["warnings"] == [{"attempt": "initial", "error": "causal_context_budget:input"}]
    assert result["causal_context_digest"] is None
