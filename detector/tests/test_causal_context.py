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


def test_local_claim_certificate_binds_the_actual_interaction():
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    target = positive(evidence_refs=["event_001_tool_01"])
    context = context_for_hearing(diagnostics(packet, config())["audit"], positive=target, packet=packet)
    binding = context["claim_binding"]
    assert binding["status"] == "verified_local_result"
    assert binding["target_digest"] == digest(target)
    assert binding["packet_digest"] == digest(packet)
    assert binding["interaction_id"] == "event_001_tool_01"
    assert binding["expression"] == "2+2"
    assert binding["observed"] == "5" and binding["expected"] == "4"
    assert binding["localization_authority"] == "none"
    assert context["claim_equivalence"] == "same_local_result"
    assert context["candidates"][0]["role"] == "unknown"


@pytest.mark.parametrize("changes", [
    {"delivered_result": "7"}, {"correct_result": "6"}, {"quote": "7"},
    {"correct_result": ""}, {"delivered_result": "the answer is 5"},
    {"correct_result": True}, {"correct_result": "1e999999999"},
    {"evidence_refs": []},
    {"source": "recall_upgrade", "delivered_result": "", "correct_result": ""},
])
def test_local_context_falls_back_when_the_hearing_claim_is_not_bound(changes):
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    assert context_for_hearing(diagnostics(packet, config())["audit"],
                               positive=positive(**changes), packet=packet) is None


def test_event_overlap_does_not_select_between_two_identical_numeric_errors():
    from detector.causal_audit import context_for_hearing
    data = record()
    data["history"][0]["tool_calls"].append(
        {"name": "calculator", "arguments": {"expr": "8/2"}})
    data["history"][0]["tool_responses"].append("5")
    packet = build_packet(data)
    audit = diagnostics(packet, config())["audit"]
    assert context_for_hearing(audit, positive=positive(), packet=packet) is None
    target = positive(evidence_refs=["event_001_tool_02"])
    context = context_for_hearing(audit, positive=target, packet=packet)
    assert context["claim_binding"]["expression"] == "8/2"
    assert context["candidates"][0]["evidence_refs"] == ["event_001_tool_02"]
    assert len(context["candidates"][0]["certificates"]) == 1


def test_corrected_first_error_cannot_replace_a_second_hearing_claim():
    from detector.causal_audit import build_causal_diagnostics, context_for_hearing
    data = {"question": "Calculate both expressions", "history": [
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": ["5"]},
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": ["4"]},
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "3+3"}}],
         "tool_responses": ["7"]},
    ]}
    packet = build_packet(data)
    cfg = config()
    audit = build_causal_diagnostics(packet=packet, config=cfg, budget=AddedBudget(cfg["causal_audit"]),
                                    mandatory_event_ids=["event_001", "event_002", "event_003"],
                                    context=None, snapshot=None)["audit"]
    target = positive(step=3, quote="7", delivered_result="7", correct_result="6",
                      evidence_refs=["event_003_tool_01"])
    context = context_for_hearing(audit, positive=target, packet=packet)
    assert [row["event_id"] for row in context["candidates"]] == ["event_003"]
    assert context["claim_binding"]["interaction_id"] == "event_003_tool_01"
    assert context_for_hearing(audit, positive=positive(step=2, quote="4", delivered_result="4",
                                                     evidence_refs=["event_002_tool_01"]), packet=packet) is None
    assert context_for_hearing(audit, positive={**target, "evidence_refs": ["event_001_tool_01"]},
                               packet=packet) is None


@pytest.mark.parametrize("part", ["binding", "certificate", "role", "authority", "equivalence"])
def test_tracer_recomputes_resealed_local_context_before_provider(monkeypatch, part):
    from detector.causal_audit import context_for_hearing
    from detector.causal_contracts import seal
    packet = build_packet(record())
    cfg = config()
    context = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=positive(), packet=packet)
    if part == "binding":
        context["claim_binding"]["expected"] = "6"
        context["claim_binding"] = seal({key: value for key, value in context["claim_binding"].items()
                                         if key != "digest"})
    elif part == "certificate":
        context["candidates"][0]["certificates"][0]["expression"] = "8/2"
    elif part == "role":
        context["candidates"][0]["role"] = "introduction"
    elif part == "authority":
        context["decision_authority"] = "verified"
    else:
        context["claim_equivalence"] = "same_semantic_claim"
    context = seal({key: value for key, value in context.items() if key != "digest"})
    monkeypatch.setattr("detector.step_tracer.provider_call",
                        lambda **kwargs: pytest.fail("invalid context reached provider"))
    with pytest.raises(ValueError, match="local claim certificate"):
        run_step_tracer(packet=packet, delivered_result="5", correct_result="4", config=cfg,
                        positive=positive(), causal_context=context)


def test_valid_local_context_falls_back_for_a_prose_claim_without_any_extra_provider(monkeypatch):
    seen = []
    parsed = {"is_hallucination": True, "hallucination_step": 1,
              "hallucination_reason": "unverified broader claim", "root_cause": {"quote": "5"},
              "delivered_result": "the answer is five", "correct_result": "four",
              "evidence_refs": ["event_001"]}
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": parsed}, {"stage": "judge", "state": "ok"}))
    monkeypatch.setattr("detector.api.run_defense", lambda **kwargs: (
        {"ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 1,
                                 "evidence_refs": ["event_001"], "reason": "independent hearing"}},
        {"stage": "defense", "state": "ok"}))
    def tracer(**kwargs):
        seen.append(kwargs["user"])
        return tracer_reply()
    monkeypatch.setattr("detector.step_tracer.provider_call", tracer)
    result = Detector(config(), require_credentials=False).detect(record())
    assert result["state"] == "verified_error"
    assert len(seen) == 1 and "CAUSAL CONTEXT:" not in seen[0]
    assert result["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"] is None


@pytest.mark.parametrize("values", [("7", "4"), ("5", "6")])
def test_tracer_refuses_a_local_certificate_for_different_actual_result_inputs(monkeypatch, values):
    from detector.causal_audit import context_for_hearing
    packet = build_packet(record())
    cfg = config()
    context = context_for_hearing(diagnostics(packet, cfg)["audit"], positive=positive(), packet=packet)
    monkeypatch.setattr("detector.step_tracer.provider_call",
                        lambda **kwargs: pytest.fail("different result inputs reached provider"))
    with added_budget_scope(cfg["causal_audit"]), pytest.raises(ValueError, match="actual result inputs"):
        run_step_tracer(packet=packet, delivered_result=values[0], correct_result=values[1], config=cfg,
                        positive=positive(), causal_context=context)


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
