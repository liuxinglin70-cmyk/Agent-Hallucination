import json

import pytest

from detector.config import load_config, validate_causal_config
from detector.packet import build_packet
from detector.semantics import LOCALIZATION_LAW
from detector.step_uncertainty import build_step_risk_profile, select_causal_candidates


def policy():
    return validate_causal_config({"mode": "tracer_context"}, base_dir=".")


@pytest.mark.parametrize("long", [False, True])
def test_roleless_tool_event_remains_a_risk_candidate(long):
    packet = build_packet({"question": "Report the requested result", "history": [
        {"step": 1, "content": "Plan the task"},
        {"step": 2, "tool_calls": [{"name": "python_execute", "arguments": {
            "code": "# unverified task assumptions\n" + "x = 1\n" * (1500 if long else 1)}}],
         "tool_responses": ["27"]},
        {"step": 3, "content": "The answer is 27"},
    ]})
    profile = build_step_risk_profile(packet, policy=policy())
    assert profile["by_event"][1]["risk_signals"]
    selected = select_causal_candidates(profile, mandatory_event_ids=[], policy=policy())
    assert "event_002" in selected["selected_event_ids"]
    assert all(signal["verification_status"] != "verified"
               for row in profile["by_event"] for signal in row["risk_signals"])
    assert packet["events"][1]["role"] is None


def test_localization_targets_the_first_error_event_including_observations():
    assert "including tool observations" in LOCALIZATION_LAW
    assert "a tool observation itself is not an assistant error" not in LOCALIZATION_LAW.lower()


def test_deployment_config_enables_context_for_both_entry_points():
    config = load_config(require_credentials=False)
    assert config["causal_audit"]["mode"] == "tracer_context"
    assert config["causal_audit"]["score_source"] == "semantic_proxy"
    from detector.config import validate_config
    assert validate_config({})["causal_audit"]["mode"] == "tracer_context"


@pytest.mark.parametrize("ids,annotation,position,state", [
    ([10, 20, 30], "20", 2, "source_step"),
    ([1, 2, 2], 2, None, "ambiguous"),
    ([10, 20], 1, None, "unmapped"),
    ([None, None], 2, 2, "position"),
    ([None, None], 3, None, "unmapped"),
    ([1, 2], True, None, "unannotated"),
])
def test_annotations_map_to_event_coordinates_without_guessing(ids, annotation, position, state):
    from detector.localization import gold_step_mapping
    record = {"history": [{"step": i} for i in ids]}
    mapping = gold_step_mapping(record, annotation)
    assert (mapping["position"], mapping["mapping_state"]) == (position, state)


def data():
    return {"question": "Report the product with independently checked reasoning", "history": [
        {"step": 10, "content": "Plan the task"},
        {"step": 20, "tool_calls": [{"name": "generate_solution", "arguments": {"prompt": "Solve"}}],
         "tool_responses": ["The wrong product is WrongProduct."]},
        {"step": 30, "content": "The wrong product is WrongProduct."},
    ]}


def install_provider(monkeypatch):
    calls = []
    parsed = {"is_hallucination": True, "hallucination_step": 3, "confidence": "high",
              "hallucination_reason": "wrong delivered product", "basis": "derivation",
              "contracts": [{"kind": "exact_value", "requirement": "CorrectProduct"}],
              "root_cause": {"step": 3, "quote": "WrongProduct", "referent": "CorrectProduct"},
              "delivered_result": "WrongProduct", "correct_result": "CorrectProduct",
              "evidence_refs": ["event_002", "event_003"]}
    monkeypatch.setattr("detector.api.run_judge", lambda **kwargs: (
        {"ok": True, "parsed": parsed}, {"stage": "judge", "state": "ok"}))
    monkeypatch.setattr("detector.hearing.run_defense", lambda **kwargs: (
        {"ok": True, "parsed": {"verdict": "substantiated", "confirmed_step": 3,
                                 "evidence_refs": ["event_003"], "reason": "wrong product remains delivered"}},
        {"stage": "defense", "state": "ok"}))
    def provider(**kwargs):
        calls.append(kwargs)
        assert "including tool observations" in kwargs["system"]
        assert "RISK CONTEXT:" in kwargs["user"]
        assert "The wrong product is WrongProduct." in kwargs["user"]
        return {"step": 2, "quote": "WrongProduct", "referent": "CorrectProduct",
                "reason": "same error first introduced in the tool response",
                "evidence_refs": ["event_002_tool_01"]}, {"attempts": 1}
    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    return calls


@pytest.mark.parametrize("backend", [False, True])
def test_standalone_and_evolution_deliver_and_record_diagnostic_context(monkeypatch, backend):
    from detector.api import Detector
    from evolution.backends import LiveDetectorBackend
    from evolution.causal_failures import build_causal_failure_record, causal_failure_prompt_view
    monkeypatch.setenv("AUTODL_API_KEY", "offline-test-key")
    calls = install_provider(monkeypatch)
    if backend:
        detector = LiveDetectorBackend({"detector_config": "detector/configs/autodl.json"})
        result = detector.run_case(data(), case_id="synthetic.json")
        cached = detector.run_case(data(), case_id="synthetic.json")
        assert cached == result
        assert len(calls) == 1
        cfg = detector.detector.config
    else:
        cfg = load_config(require_credentials=False)
        result = Detector(cfg, require_credentials=False).detect(data())
    assert result["responsible_step"] == 2
    assert result["localization"]["source_step_id"] == 20
    assert result["localization"]["source_channels"] == [
        {"kind": "observation", "reference": "event_002_tool_01"}]
    assert result["proof"]["causal_diagnostics"]["mode"] == "tracer_context"
    context = result["proof"]["hearings"][0]["tracer_result"]["causal_context"]
    assert context["scope"] == "diagnostic_event_priority"
    assert context["decision_authority"] == context["localization_authority"] == "none"
    failure = build_causal_failure_record({"gold": True, "prediction": True, "authority": "authoritative",
                                          "responsible_step": 2, "result": result}, data(), policy=cfg["causal_audit"])
    assert failure["evidence_state"] == "hypothesis"
    assert causal_failure_prompt_view(failure)["risk_candidates"]


@pytest.mark.parametrize("tamper", ["priority", "excerpt", "authority", "target"])
def test_resealed_risk_context_is_recomputed_before_provider(monkeypatch, tamper):
    from detector.risk_context import build_risk_context
    from detector.causal_contracts import seal
    from detector.step_tracer import run_step_tracer
    from detector.hearing import judge_target
    cfg = load_config(require_credentials=False)
    packet = build_packet(data())
    target = judge_target({"hallucination_step": 3, "root_cause": {"quote": "WrongProduct"},
                           "delivered_result": "WrongProduct", "correct_result": "CorrectProduct",
                           "evidence_refs": ["event_002", "event_003"]})
    context = build_risk_context(packet, positive=target, policy=cfg["causal_audit"])
    if tamper == "priority":
        context["candidates"][0]["priority"] = -100
    elif tamper == "excerpt":
        context["candidates"][0]["channels"][0]["excerpt"] = "invented evidence"
    elif tamper == "authority":
        context["localization_authority"] = "authoritative"
    else:
        context["target_digest"] = "0" * 64
    context = seal({k: v for k, v in context.items() if k != "digest"})
    monkeypatch.setattr("detector.step_tracer.provider_call", lambda **kwargs: pytest.fail("forged context called provider"))
    with pytest.raises(ValueError, match="risk context"):
        run_step_tracer(packet=packet, positive=target, delivered_result="WrongProduct",
                        correct_result="CorrectProduct", config=cfg, causal_context=context)


def test_risk_context_is_bounded_and_keeps_its_hearing_target():
    from detector.risk_context import build_risk_context
    from detector.hearing import judge_target
    packet = build_packet(data())
    cfg = load_config(require_credentials=False)
    target = judge_target({"hallucination_step": 3, "root_cause": {"quote": "WrongProduct"},
                           "evidence_refs": ["event_003"]})
    context = build_risk_context(packet, positive=target, policy=cfg["causal_audit"])
    assert len(json.dumps(context, ensure_ascii=False).encode("utf-8")) <= 6000
    assert context["candidates"][0]["event_id"] == "event_003"


def test_risk_context_does_not_dereference_an_unscored_mandatory_event():
    from detector.risk_context import build_risk_context
    from detector.hearing import judge_target
    cfg = load_config(require_credentials=False)
    limit = cfg["causal_audit"]["max_projection_events"] * cfg["causal_audit"]["max_dependency_depth"]
    packet = build_packet({"question": "Report the result", "history": [
        {"step": i, "content": "WrongProduct"} for i in range(1, limit + 2)]})
    target = judge_target({"hallucination_step": limit + 1,
                           "root_cause": {"quote": "WrongProduct"},
                           "evidence_refs": [packet["events"][-1]["event_id"]]})
    assert build_risk_context(packet, positive=target, policy=cfg["causal_audit"]) is None


def test_risk_context_reports_events_omitted_by_profile_capacity():
    from detector.risk_context import build_risk_context
    from detector.hearing import judge_target
    cfg = load_config(require_credentials=False)
    limit = cfg["causal_audit"]["max_projection_events"] * cfg["causal_audit"]["max_dependency_depth"]
    packet = build_packet({"question": "Report the result", "history": [
        {"step": i, "content": "WrongProduct"} for i in range(1, limit + 2)]})
    target = judge_target({"hallucination_step": 1, "root_cause": {"quote": "WrongProduct"},
                           "evidence_refs": ["event_001"]})
    context = build_risk_context(packet, positive=target, policy=cfg["causal_audit"])
    assert packet["events"][-1]["event_id"] in context["omitted_event_ids"]


def test_generic_context_budget_fallback_is_visible(monkeypatch):
    from detector.risk_context import build_risk_context
    from detector.hearing import judge_target
    from detector.runtime import added_budget_scope
    from detector.step_tracer import run_step_tracer
    cfg = load_config(require_credentials=False)
    packet = build_packet(data())
    target = judge_target({"hallucination_step": 3, "root_cause": {"quote": "WrongProduct"},
                           "delivered_result": "WrongProduct", "correct_result": "CorrectProduct",
                           "evidence_refs": ["event_003"]})
    context = build_risk_context(packet, positive=target, policy=cfg["causal_audit"])
    seen = []
    def provider(**kwargs):
        seen.append(kwargs["user"])
        return {"step": 2, "quote": "WrongProduct", "referent": "CorrectProduct",
                "reason": "same observed error", "evidence_refs": ["event_002_tool_01"]}, {"attempts": 1}
    monkeypatch.setattr("detector.step_tracer.provider_call", provider)
    with added_budget_scope(cfg["causal_audit"]) as budget:
        budget.reserve_context("x" * cfg["causal_audit"]["max_added_prompt_tokens_per_case"])
        result, status = run_step_tracer(packet=packet, positive=target, delivered_result="WrongProduct",
                                        correct_result="CorrectProduct", config=cfg, causal_context=context)
    assert result["ok"] and result["causal_context_digest"] is None
    assert "RISK CONTEXT:" not in seen[0]
    assert status["warnings"] == [{"attempt": "initial", "error": "causal_context_budget:input"}]


def test_frozen_tracer_does_not_reuse_a_different_risk_policy(monkeypatch):
    from detector.api import Detector
    from evolution.backends import LiveDetectorBackend
    calls = install_provider(monkeypatch)
    cfg = load_config(require_credentials=False)
    baseline = Detector(cfg, require_credentials=False).detect(data())
    frozen = LiveDetectorBackend._frozen_stages(baseline, set())
    replayed = Detector(cfg, require_credentials=False).detect(data(), frozen_stages=frozen)
    assert len(calls) == 1 and replayed["responsible_step"] == 2
    cfg["causal_audit"]["policy_version"] += "-changed"
    rerun = Detector(cfg, require_credentials=False).detect(data(), frozen_stages=frozen)
    assert len(calls) == 2
    assert rerun["proof"]["hearings"][0]["tracer_result"]["causal_context_digest"] != baseline[
        "proof"]["hearings"][0]["tracer_result"]["causal_context_digest"]


def test_scoring_rejects_a_different_localization_target():
    from evolution.metrics import row_responsible_step
    assert row_responsible_step({"responsible_step": 2, "localization_target": "assistant_adoption"}) is None


def test_detector_and_evolution_gold_mapping_are_identical(tmp_path):
    from detector.evaluation import evaluate_run
    from detector.runner import record_case
    from evolution.splits import load_gold_step
    record = data()
    case = "synthetic.json"
    (tmp_path / case).write_text(json.dumps({**record, "is_hallucination": True,
                                            "hallucination_step": "20"}), encoding="utf-8")
    result = {"schema_version": "detector.detection_result.v1", "case_id": case,
              "state": "verified_error", "occurrence_state": "verified_error",
              "runtime_valid": True, "decision_authority": "authoritative",
              "localization_authority": "authoritative", "responsible_step": 2,
              "localization": {"target": "dataset_first_error_event.v1"}, "proof": {}}
    record_case(runs_root=tmp_path / "runs", run_id="mapping", case_id=case,
                input_record=record, result=result, elapsed_seconds=0)
    report = evaluate_run(runs_root=tmp_path / "runs", run_id="mapping", dataset_root=tmp_path, cases=[case])
    assert report["rows"][0]["gold_step"] == load_gold_step(tmp_path, case) == 2
    assert report["rows"][0]["gold_step_mapping"]["annotation"] == 20
    assert report["metrics"]["step_localization"]["hits"] == 1


def test_public_result_reader_preserves_unknown_location_in_a_summary_packet():
    from detector.result import build_detection_output
    result = build_detection_output(case_id="summary", packet={"events": [{}]},
                                    proof={"state": "verified_clean", "runtime_valid": True},
                                    stage_status={}, providers={}, provenance={})
    assert result["localization"]["position"] is None
    assert result["localization"]["source_step_id"] is None
    assert result["localization"]["source_channels"] == []
