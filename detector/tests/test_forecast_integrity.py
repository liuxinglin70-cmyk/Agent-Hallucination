"""Integration counterexamples for the forecast evidence and resource boundary."""

from copy import deepcopy
import json
from pathlib import Path
import pytest

from detector.api import Detector
from detector.action_forecast import action_comparisons, build_action_forecast, prefix_view
from detector.packet import build_packet
from detector.config import diagnostic_budget_reserve
from detector.runtime import AddedBudgetExceeded, OptionalBudget, added_budget_scope, added_request_scope, request_deadline
from detector.runtime import deadline_scope
from detector.judge import run_judge
from detector.causal_contracts import seal
from detector.action_evidence import action_evidence_block, validate_action_evidence
from detector.replay import build_replay_inputs
from detector.stage_retry import StageRetryCache, stage_retry_scope
from detector.tests.test_action_forecast import config, constraints, install_transport, record


def test_judge_can_quote_a_raw_action_channel_without_rendering_labels(monkeypatch):
    from detector import provider
    from detector.action_forecast import COMPARE_INSTRUCTION
    calls = install_transport(monkeypatch)
    ordinary = provider._provider_call
    raw = record()
    raw["history"][1] = {"role": "assistant", "tool_calls": [
        {"name": "notify", "arguments": {"message": "5"}}], "tool_responses": ["OBSERVATION_ONLY"]}
    raw["history"].append({"role": "assistant", "content": "FUTURE_ONLY"})

    def transport(**kwargs):
        parsed, meta = ordinary(**kwargs)
        if kwargs["purpose"] == "judge":
            checks = json.loads(kwargs["user"].rsplit(COMPARE_INSTRUCTION, 1)[1])
            action = checks[0]["actual_action"]
            assert "OBSERVATION_ONLY" not in json.dumps(action)
            assert "FUTURE_ONLY" not in json.dumps(action)
            channel = next(c for c in action["channels"] if c["channel"] == "tool_name")
            parsed["next_action_comparisons"] = [{
                **{k: checks[0][k] for k in ("constraint_id", "source_event_id", "next_event_id")},
                "state": "matched", "reason": "The next event contains the stated tool call.",
                "evidence_refs": [channel["reference"]], "quote": channel["excerpts"][0]}]
        return parsed, meta

    monkeypatch.setattr(provider, "_provider_call", transport)
    result, status = run_judge(packet=build_packet(raw), config=config())
    assert status["state"] == "ok" and result["parsed"]["is_hallucination"] is False
    assert all(b["next_action_comparison_health"]["state"] == "ok" for b in result["raw"])
    assert result["parsed"]["next_action_comparisons"][0]["quote"] == "notify"
    assert not any(c["purpose"] == "action_comparison_repair" for c in calls)
    assert all("FUTURE_ONLY" not in c["user"] for c in calls if c["purpose"] == "action_forecast")


def test_composite_rendering_quotes_remain_invalid(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    supplied = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "matched",
                "reason": "The next event is present.", "evidence_refs": ["event_002"],
                "quote": "CONTENT event_002: The answer is 5."}
    comparison = action_comparisons({"next_action_comparisons": [supplied]}, forecast, packet)[0]
    assert comparison["validation_state"] == "quote_not_in_action"
    assert comparison["state"] == "unknown"


def test_long_action_excerpts_preserve_exact_quotes_and_packet_identity(monkeypatch):
    from detector.action_forecast import comparison_prompt, COMPARE_INSTRUCTION
    install_transport(monkeypatch)
    raw = record()
    raw["history"][1]["content"] = "A" * 600 + "M" * 3000 + "Z" * 600
    packet = build_packet(raw)
    forecast = build_action_forecast(packet, config=config())
    checks = json.loads(comparison_prompt(forecast, packet).split(COMPARE_INSTRUCTION, 1)[1])
    channel = checks[0]["actual_action"]["channels"][0]
    assert channel["truncated"] and sum(map(len, channel["excerpts"])) == 1200
    supplied = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "matched",
                "reason": "The action quote is visible.", "evidence_refs": ["event_002"]}
    for quote in channel["excerpts"]:
        assert action_comparisons({"next_action_comparisons": [{**supplied, "quote": quote}]},
                                  forecast, packet)[0]["validation_state"] == "valid"
    assert action_comparisons({"next_action_comparisons": [{**supplied, "quote": "".join(channel["excerpts"])}]},
                              forecast, packet)[0]["validation_state"] == "quote_not_in_action"
    changed = deepcopy(packet)
    changed["events"][1]["content"] = "different"
    with pytest.raises(ValueError, match="packet identity"):
        comparison_prompt(forecast, changed)


def test_optional_forecast_timeout_preserves_existing_diagnostics(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("detector.runtime.time.monotonic", lambda: clock[0])
    install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call

    def transport(**kwargs):
        if kwargs["purpose"] == "action_forecast":
            clock[0] = request_deadline() + 0.001
            raise AddedBudgetExceeded("time")
        return ordinary(**kwargs)

    monkeypatch.setattr(provider, "_provider_call", transport)
    result = Detector(config(), require_credentials=False).detect(deepcopy(record()))
    assert result["proof"]["action_forecast"]["health"] == "budget_exhausted"
    assert result["proof"]["causal_diagnostics"]["health"]["state"] == "ok"


def test_optional_context_cannot_consume_diagnostic_input(monkeypatch):
    policy = config()["causal_audit"]
    with added_budget_scope(policy) as parent:
        optional = OptionalBudget(parent, diagnostic_budget_reserve(policy))
        optional.reserve_context("x" * 54000)
        with pytest.raises(AddedBudgetExceeded, match="input"):
            optional.reserve_context("x")
        parent.reserve_context("x" * 6000)
        assert parent.input_bytes == policy["max_added_prompt_tokens_per_case"]


def test_optional_request_preserves_existing_model_audit_allowance():
    policy = config(counterfactual_mode="model_hypothesis")["causal_audit"]
    with added_budget_scope(policy) as parent:
        optional = OptionalBudget(parent, diagnostic_budget_reserve(policy))
        with pytest.raises(AddedBudgetExceeded, match="requests"):
            optional.reserve(system="", user="", output_tokens=1)
        assert parent.requests == 0
        assert parent.reserve(system="", user="", output_tokens=1) == 1


def test_optional_budget_rejects_a_foreign_case():
    policy = config()["causal_audit"]
    with added_budget_scope(policy) as first:
        optional = OptionalBudget(first, diagnostic_budget_reserve(policy))
    with added_budget_scope(policy), pytest.raises(ValueError, match="current case"):
        with added_request_scope(optional):
            pass


def test_optional_forecast_preserves_the_remaining_case_deadline(monkeypatch):
    monkeypatch.setattr("detector.runtime.time.monotonic", lambda: 0.0)
    policy = config()["causal_audit"]
    with deadline_scope(seconds=20), added_budget_scope(policy) as parent:
        optional = OptionalBudget(parent, diagnostic_budget_reserve(policy))
        with pytest.raises(AddedBudgetExceeded, match="time"):
            optional.reserve(system="", user="", output_tokens=1)
        parent.check()
        assert parent.requests == 0


def test_unaligned_multiturn_prefix_never_includes_future_questions():
    raw = record()
    raw["question"] = [["Compute 2+2."], ["FUTURE_TIRE_PRESSURE_REQUEST"]]
    view = prefix_view(build_packet(raw), 1)
    assert "FUTURE_TIRE_PRESSURE_REQUEST" not in view["question"]
    assert view["task_binding"]["state"] == "unknown"


def test_unaligned_multiturn_forecast_does_not_guess_active_task(monkeypatch):
    calls = install_transport(monkeypatch)
    raw = record()
    raw["question"] = [["Compute 2+2."], ["Check tire pressure."]]
    forecast = build_action_forecast(build_packet(raw), config=config())
    assert forecast["health"] == "task_alignment_unknown"
    assert not calls


def test_explicit_turn_binding_is_invariant_to_future_question():
    raw = record()
    raw["question"] = [["Compute 2+2."], ["FUTURE_SECRET"]]
    raw["history"][0]["task_turn"] = 1
    original = prefix_view(build_packet(raw), 1)
    raw["question"][1] = ["DIFFERENT_FUTURE_SECRET"]
    changed = prefix_view(build_packet(raw), 1)
    assert original == changed
    assert original["task_binding"]["active_turn"] == 1


def test_each_constraint_has_an_independent_comparison(monkeypatch):
    predicted = constraints()
    predicted["constraints"].append({**deepcopy(predicted["constraints"][0]),
                                     "requirement": "Preserve the task's stated expression."})
    install_transport(monkeypatch, bad_prediction=predicted)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    rows = action_comparisons({}, forecast, packet)
    assert len(rows) == 2
    ids = [c["constraint_id"] for c in forecast["predictions"][0]["constraints"]]
    supplied = [{"source_event_id": "event_001", "next_event_id": "event_002", "constraint_id": cid,
                 "state": state, "reason": "Only this constraint was checked.",
                 "evidence_refs": ["event_002"], "quote": "5"}
                for cid, state in zip(ids, ["divergent", "matched"])]
    rows = action_comparisons({"next_action_comparisons": supplied}, forecast, packet)
    assert [row["state"] for row in rows] == ["divergent", "matched"]


def test_comparison_missing_and_semantic_unknown_have_different_health(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    missing = action_comparisons({}, forecast, packet)[0]
    assert missing["validation_state"] == "missing"
    unknown = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "unknown",
               "reason": "Action order is not established.", "evidence_refs": [], "quote": ""}
    row = action_comparisons({"next_action_comparisons": [unknown]}, forecast, packet)[0]
    assert row["validation_state"] == "valid" and row["state"] == "unknown"


def test_explicit_future_information_cannot_validate_a_local_comparison(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    actual = {"source_event_id": "event_001", "next_event_id": "event_002", "state": "divergent",
              "reason": "This causes the wrong answer in event_003.",
              "evidence_refs": ["event_002"], "quote": "5"}
    row = action_comparisons({"next_action_comparisons": [actual]}, forecast, packet)[0]
    assert row["state"] == "unknown" and row["validation_state"] == "future_reason_reference"
    assert "event_003" in row["rejected_comparison"]["text"]


def install_full_chain(monkeypatch, *, upgrade=True, defend="substantiated"):
    install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        purpose = kwargs["purpose"]
        if purpose == "recall_scanner":
            assert "correct_result" not in kwargs["user"] and '"is_hallucination"' not in kwargs["user"]
            parsed = {"candidates": []}
        elif purpose == "causal_upgrade":
            ids = [c["candidate_id"] for c in json.JSONDecoder().raw_decode(
                kwargs["user"].split("CANDIDATES:\n", 1)[1])[0]]
            parsed = {"upgrades": [{"candidate_id": cid, "content_error": upgrade, "causal": upgrade,
                                    "corrected_step": 1 if upgrade else None,
                                    "reason": "Recompute the literal result and inspect its adoption.", "evidence_refs": []}
                                   for cid in ids]}
        elif purpose == "step_tracer":
            parsed = {"step": 1, "quote": "5", "referent": "4", "reason": "The source result is wrong.",
                      "evidence_refs": ["event_001_tool_01"]}
        elif purpose == "defense":
            parsed = {"verdict": defend, "confirmed_step": 1 if defend == "substantiated" else None,
                      "evidence_refs": ["event_001_tool_01"], "reason": "Independent content and relevance review."}
        else:
            return ordinary(**kwargs)
        return parsed, {"attempts": 1, "usage": {"total_tokens": 10}}

    monkeypatch.setattr(provider, "_provider_call", transport)
    return calls


@pytest.mark.parametrize("upgrade,defend,expected", [(True, "substantiated", "verified_error"),
                                                    (False, "substantiated", "verified_clean"),
                                                    (True, "refuted", "verified_clean")])
def test_forecast_candidate_must_pass_existing_upgrade_and_defense(monkeypatch, upgrade, defend, expected):
    calls = install_full_chain(monkeypatch, upgrade=upgrade, defend=defend)
    result = Detector(config(), require_credentials=False).detect(record())
    assert result["state"] == expected
    purposes = {call["purpose"] for call in calls}
    assert {"recall_scanner", "causal_upgrade"} <= purposes
    assert result["proof"]["recall_candidates"]
    if upgrade:
        assert {"step_tracer", "defense"} <= purposes
        target = result["proof"]["hearings"][0]["positive"]
        assert target["action_evidence"]
    if expected == "verified_error":
        assert result["decision_authority"] == result["localization_authority"] == "authoritative"
        assert result["responsible_step"] == 1


def test_comparison_repair_prompt_is_admissible_under_json_mode():
    from detector.action_forecast import COMPARISON_REPAIR_SYSTEM

    assert "json" in COMPARISON_REPAIR_SYSTEM.lower()


def test_comparison_repair_cannot_change_the_base_judge(monkeypatch):
    install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call
    calls = []

    def transport(**kwargs):
        calls.append(kwargs["purpose"])
        if kwargs["purpose"] == "judge":
            parsed, meta = ordinary(**kwargs)
            parsed.pop("next_action_comparisons")
            return parsed, meta
        if kwargs["purpose"] == "action_comparison_repair":
            views = json.loads(kwargs["user"])
            check = views[0]["check"]
            return {"is_hallucination": True, "hallucination_step": 1, "correct_result": "FORGED",
                    "next_action_comparisons": [{"constraint_id": check["constraint_id"],
                    "source_event_id": "event_001", "next_event_id": "event_002", "state": "unknown",
                    "reason": "The materiality is unknown.", "quote": "", "evidence_refs": []}]}, {"attempts": 1}
        return ordinary(**kwargs)

    monkeypatch.setattr(provider, "_provider_call", transport)
    result, status = run_judge(packet=build_packet(record()),
                               config=config(max_added_provider_requests_per_case=2, max_added_output_tokens_per_case=8192))
    assert status["state"] == "ok" and result["parsed"]["is_hallucination"] is False
    assert result["parsed"]["correct_result"] != "FORGED"
    assert calls.count("action_forecast") == calls.count("action_comparison_repair") == 1
    assert any(row["next_action_comparison_health"]["repair"]["state"] == "ok" for row in result["raw"])


def test_direct_judge_does_not_reopen_the_forecast_request_budget(monkeypatch):
    calls = install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call

    def transport(**kwargs):
        parsed, meta = ordinary(**kwargs)
        if kwargs["purpose"] == "judge":
            parsed.pop("next_action_comparisons")
        return parsed, meta

    monkeypatch.setattr(provider, "_provider_call", transport)
    result, _ = run_judge(packet=build_packet(record()), config=config())
    assert len([c for c in calls if c["purpose"] == "action_forecast"]) == 1
    assert not any(c["purpose"] == "action_comparison_repair" for c in calls)
    assert all(row["next_action_comparison_health"]["repair"]["state"] == "not_admitted" for row in result["raw"])


def test_action_evidence_cannot_acquire_authority_by_resealing(monkeypatch):
    install_full_chain(monkeypatch)
    result = Detector(config(), require_credentials=False).detect(record())
    evidence = result["proof"]["action_candidates"][0]["action_evidence"][0]
    forged = seal({**{k: v for k, v in evidence.items() if k != "digest"}, "decision_authority": "authoritative"})
    with pytest.raises(ValueError, match="authority"):
        validate_action_evidence(build_packet(record()), forged)


def test_verified_arithmetic_copy_is_recomputed_without_model_comparison(monkeypatch):
    calls = install_full_chain(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call

    def transport(**kwargs):
        parsed, meta = ordinary(**kwargs)
        if kwargs["purpose"] == "judge":
            parsed.pop("next_action_comparisons")
        return parsed, meta

    monkeypatch.setattr(provider, "_provider_call", transport)
    raw = record()
    raw["history"][1]["content"] = {"entity": "result", "attribute": "value", "value": 5,
                                      "source_ref": "event_001_tool_01"}
    result = Detector(config(), require_credentials=False).detect(raw)
    assert result["state"] == "verified_error"
    witnesses = [w for c in result["proof"]["action_candidates"] for w in c["action_evidence"]]
    assert {w["producer"] for w in witnesses} == {"literal_arithmetic"}
    witness = witnesses[0]
    assert witness["witness"]["origin_event_id"] == "event_001"
    assert witness["witness"]["adoption_event_id"] == "event_002"
    forged = {k: deepcopy(v) for k, v in witness.items() if k != "digest"}
    forged["witness"]["certificate"]["expected"] = "99"
    with pytest.raises(ValueError, match="witness"):
        validate_action_evidence(build_packet(raw), seal(forged))


def test_successful_action_pipeline_retries_do_not_recharge_context(monkeypatch):
    calls = install_full_chain(monkeypatch)
    cfg = config()
    cache = StageRetryCache()
    with added_budget_scope(cfg["causal_audit"]) as budget, stage_retry_scope(cache):
        detector = Detector(cfg, require_credentials=False)
        cache.begin_attempt(1)
        first = detector.detect(record())
        count, size = len(calls), budget.input_bytes
        cache.begin_attempt(2)
        second = detector.detect(record())
        assert len(calls) == count
        assert budget.input_bytes == size
    assert first["state"] == second["state"] == "verified_error"


def test_forecast_policy_invalidates_all_evidence_consuming_replay_stages():
    packet = build_packet(record())
    off = build_replay_inputs(packet=packet, config=config("off"), source="same")
    on = build_replay_inputs(packet=packet, config=config(), source="same")
    for stage in ("judge", "recall_scanner", "causal_upgrade", "step_tracer", "defense"):
        assert off["stage_digests"][stage] != on["stage_digests"][stage]


@pytest.mark.parametrize("change", [{"top_k": 1}, {"policy_version": "changed"},
                                  {"max_added_seconds_per_case": 240}])
def test_enabled_forecast_policy_changes_invalidate_judge_replay(change):
    packet = build_packet(record())
    before = build_replay_inputs(packet=packet, config=config(), source="same")
    after = build_replay_inputs(packet=packet, config=config(**change), source="same")
    assert before["stage_digests"]["judge"] != after["stage_digests"]["judge"]


@pytest.mark.local_data
def test_real_bfcl071_cannot_predict_from_future_tire_pressure_request(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    raw = json.loads((root / "datasets/raw/agenthallu/AgentHallu/BFCL/071.json").read_text(encoding="utf-8-sig"))
    calls = install_transport(monkeypatch)
    packet = build_packet({"question": raw["question"], "history": raw["history"]})
    view = prefix_view(packet, 1)
    assert "tire pressure" not in view["question"]
    forecast = build_action_forecast(packet, config=config())
    assert forecast["health"] == "task_alignment_unknown" and not calls


def test_recorded_user_message_binds_only_the_matching_task_turn():
    raw = {"question": [["Compute 2+2."], ["Check tire pressure."]], "history": [
        {"role": "user", "content": "Compute 2+2."},
        {"role": "assistant", "content": "Calculating."},
        {"role": "user", "content": "An unaligned new request."}]}
    packet = build_packet(raw)
    assert prefix_view(packet, 2)["task_binding"]["active_turn"] == 1
    assert "tire pressure" not in prefix_view(packet, 2)["question"]
    assert prefix_view(packet, 3)["task_binding"]["state"] == "unknown"


def test_resealed_action_evidence_cannot_use_an_observation_as_an_action(monkeypatch):
    install_full_chain(monkeypatch)
    raw = record()
    raw["history"][1].update(tool_calls=[{"name": "lookup", "arguments": {"query": "status"}}],
                              tool_responses=["OBSERVATION_ONLY_TOKEN"])
    result = Detector(config(), require_credentials=False).detect(raw)
    evidence = result["proof"]["action_candidates"][0]["action_evidence"][0]
    forged = {k: deepcopy(v) for k, v in evidence.items() if k != "digest"}
    forged.update(quote="OBSERVATION_ONLY_TOKEN", evidence_refs=["event_002_tool_01", "event_001_tool_01"])
    with pytest.raises(ValueError, match="action quote"):
        validate_action_evidence(build_packet(raw), seal(forged))


def test_semantic_unknown_is_never_retried_to_obtain_a_positive(monkeypatch):
    calls = install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call

    def transport(**kwargs):
        parsed, meta = ordinary(**kwargs)
        if kwargs["purpose"] == "judge":
            parsed["next_action_comparisons"][0].update(state="unknown", reason="The obligation is unknown.",
                                                        evidence_refs=[], quote="")
        return parsed, meta

    monkeypatch.setattr(provider, "_provider_call", transport)
    result = Detector(config(max_added_provider_requests_per_case=3, max_added_output_tokens_per_case=16384),
                      require_credentials=False).detect(record())
    assert result["state"] == "verified_clean"
    assert not any(c["purpose"] in {"action_comparison_repair", "recall_scanner"} for c in calls)


def test_an_isolated_divergent_ballot_does_not_force_a_new_hearing(monkeypatch):
    from threading import Lock
    calls = install_transport(monkeypatch)
    from detector import provider
    ordinary = provider._provider_call
    lock, count = Lock(), [0]

    def transport(**kwargs):
        parsed, meta = ordinary(**kwargs)
        if kwargs["purpose"] == "judge":
            with lock:
                count[0] += 1
                divergent = count[0] == 1
            if not divergent:
                parsed["next_action_comparisons"][0].update(state="unknown", reason="The scope is unknown.",
                                                            evidence_refs=[], quote="")
        return parsed, meta

    monkeypatch.setattr(provider, "_provider_call", transport)
    result = Detector(config(), require_credentials=False).detect(record())
    assert result["state"] == "verified_clean"
    assert not any(c["purpose"] == "recall_scanner" for c in calls)


def test_route_only_judge_preserves_an_existing_case_budget(monkeypatch):
    calls = install_transport(monkeypatch)
    cfg = config()
    with added_budget_scope(cfg["causal_audit"]) as budget:
        result, status = run_judge(packet=build_packet(record()), config={"judge_route": cfg["judge_route"]})
        assert status["state"] == "ok" and result["parsed"]["is_hallucination"] is False
        assert budget.requests == 0
    assert {call["purpose"] for call in calls} == {"judge"}


def test_action_context_without_stage_policy_cannot_bypass_admission(monkeypatch):
    install_full_chain(monkeypatch)
    cfg = config()
    result = Detector(cfg, require_credentials=False).detect(record())
    evidence = result["proof"]["action_candidates"][0]["action_evidence"]
    with added_budget_scope(cfg["causal_audit"]) as budget:
        text, admission = action_evidence_block(build_packet(record()), evidence, None)
        assert text == "" and admission == {"state": "not_admitted", "resource": "policy_unavailable"}
        assert budget.requests == budget.input_bytes == 0


def test_forecast_targets_the_risky_next_event_not_an_arbitrary_early_step(monkeypatch):
    """The comparison must confront the step whose risk signal fired.

    The old rule picked the first Top-K event whose *next* event was agent
    output, which frequently produced a forecast about an unrelated early
    step.  The comparison then had no bearing on the recorded error.
    """
    from detector.action_forecast import forecast_targets
    from detector.config import validate_config
    from detector.step_uncertainty import build_step_risk_profile
    raw = {"question": "Compute 2+2, then report it.", "history": [
        {"role": "assistant", "content": "I will compute the sum now."},
        {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}],
         "tool_responses": ["5"]},
        {"role": "assistant", "content": "The answer is 5."},
        {"role": "assistant", "content": "Done."},
    ]}
    cfg = validate_config({"causal_audit": {"next_action_mode": "judge_context",
                                           "max_added_prompt_tokens_per_case": 60000,
                                           "max_added_output_tokens_per_case": 4096,
                                           "max_added_seconds_per_case": 180}}, require_credentials=False)
    packet = build_packet(raw)
    profile = build_step_risk_profile(packet, policy=cfg["causal_audit"])
    targets = forecast_targets(packet, profile, set(), set())
    assert targets, "a verified arithmetic deviation must be targeted"
    assert targets[0][2] == "event_002"
    assert targets[0][1] == "event_001"


def test_multiple_constraints_for_one_pair_accept_complete_positional_answers(monkeypatch):
    install_transport(monkeypatch)
    packet = build_packet(record())
    forecast = build_action_forecast(packet, config=config())
    predicted = constraints()
    predicted["constraints"].append({**deepcopy(predicted["constraints"][0]),
                                     "requirement": "Preserve the task's stated expression."})
    install_transport(monkeypatch, bad_prediction=predicted)
    forecast = build_action_forecast(packet, config=config())
    ids = [c["constraint_id"] for c in forecast["predictions"][0]["constraints"]]
    supplied = [{"source_event_id": "event_001", "next_event_id": "event_002",
                 "state": state, "reason": "Only this constraint was checked.",
                 "evidence_refs": ["event_002"], "quote": "5"}
                for state in ("divergent", "matched")]
    rows = action_comparisons({"next_action_comparisons": supplied}, forecast, packet)
    assert [row["state"] for row in rows] == ["divergent", "matched"]
    assert all(row["validation_state"] == "valid" for row in rows)
    # A partial answer must not be attributed to an arbitrary constraint.
    partial = action_comparisons({"next_action_comparisons": supplied[:1]}, forecast, packet)
    assert all(row["validation_state"] == "missing" for row in partial)
    assert ids


def test_missing_channel_is_rendered_empty_not_as_none_text(monkeypatch):
    install_transport(monkeypatch)
    raw = record()
    raw["history"][0]["tool_responses"] = []
    view = prefix_view(build_packet(raw), 1)
    assert view["events"][0]["tool_interactions"][0]["observation"] == ""
    assert "None" not in view["events"][0]["tool_interactions"][0]["observation"]
