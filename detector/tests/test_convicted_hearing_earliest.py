"""Select the earliest valid error origin across convicted hearings."""
from copy import deepcopy
import json

import pytest

from detector import adjudication
from detector.api import Detector
from detector.packet import build_packet
from detector.tests.test_action_forecast import config
from evolution.backends import LiveDetectorBackend


PACKET = build_packet({"question": "Report the results.", "history": [
    {"role": "assistant", "content": f"result {step}"} for step in range(1, 6)
]})


def _hearing(cid, step, origin=None):
    ref = f"event_{step:03d}"
    hearing = {"source": "recall_upgrade", "candidate_id": cid, "step": step,
               "defense_result": {"ok": True, "parsed": {
                   "verdict": "substantiated", "confirmed_step": step, "evidence_refs": [ref]}}}
    if origin is not None:
        hearing["tracer_result"] = {"ok": True, "parsed": {
            "step": origin, "quote": f"result {origin}", "referent": "correct result",
            "evidence_refs": [f"event_{origin:03d}"], "reason": "earliest origin"}}
    return hearing


def test_convicted_hearing_selects_earliest_confirmed_step():
    queue = [_hearing("late", 2), _hearing("early", 1), _hearing("tie", 1)]
    assert adjudication._convicted_hearing(queue, PACKET)["candidate_id"] == "early"


def test_convicted_hearing_skips_refuted():
    refuted = _hearing("r", 1)
    refuted["defense_result"]["parsed"]["verdict"] = "refuted"
    assert adjudication._convicted_hearing([refuted, _hearing("ok", 3)], PACKET)["candidate_id"] == "ok"
    assert adjudication._convicted_hearing([refuted], PACKET) is None


def test_convicted_hearing_compares_origins_instead_of_allegation_steps():
    queue = [_hearing("B", 3, 2), _hearing("A", 5, 1)]
    assert adjudication._convicted_hearing(queue, PACKET)["candidate_id"] == "A"


def test_equal_origins_keep_hearing_order():
    queue = [_hearing("first", 5, 1), _hearing("second", 3, 1)]
    assert adjudication._convicted_hearing(queue, PACKET)["candidate_id"] == "first"


@pytest.mark.parametrize("fault", ["failed_result", "failed_status", "no_quorum", "unsupported_refs",
                                  "out_of_range", "boolean_step"])
def test_invalid_tracer_cannot_win_by_claiming_an_earlier_origin(fault):
    early = _hearing("invalid_trace", 5, 1)
    tracer = early["tracer_result"]
    if fault == "failed_result":
        tracer["ok"] = False
    elif fault == "failed_status":
        early["tracer_status"] = {"state": "contract_error"}
    elif fault == "no_quorum":
        tracer["provider"] = {"vote": {"quorum_met": False}}
    elif fault == "unsupported_refs":
        tracer["parsed"]["evidence_refs"] = ["event_004"]
    else:
        tracer["parsed"]["step"] = 0 if fault == "out_of_range" else True
    queue = [early, _hearing("valid_trace", 3, 2)]
    assert adjudication._convicted_hearing(queue, PACKET)["candidate_id"] == "valid_trace"


@pytest.mark.parametrize("fault", ["failed_result", "failed_status", "no_quorum", "missing_refs",
                                  "mismatched_confirmation"])
def test_invalid_defense_cannot_supply_a_convicted_hearing(fault):
    early = _hearing("invalid_defense", 1, 1)
    defense = early["defense_result"]
    if fault == "failed_result":
        defense["ok"] = False
    elif fault == "failed_status":
        early["defense_status"] = {"state": "no_quorum"}
    elif fault == "no_quorum":
        defense["provider"] = {"vote": {"quorum_met": False}}
    elif fault == "missing_refs":
        defense["parsed"]["evidence_refs"] = []
    else:
        defense["parsed"]["confirmed_step"] = 2
    queue = [early, _hearing("valid_defense", 3, 2)]
    assert adjudication._convicted_hearing(queue, PACKET)["candidate_id"] == "valid_defense"


def test_failed_tracer_uses_same_fallback_for_selection_and_adoption():
    later = _hearing("A", 5, 1)
    later["tracer_result"] = {"ok": False, "parsed": None}
    earlier = _hearing("B", 3)
    judge = {"is_hallucination": True, "hallucination_step": 5, "hallucination_reason": "wrong result"}
    result = adjudication.adjudicate(
        packet=PACKET, judge_result={"ok": True, "parsed": judge}, judge_status={"state": "ok"},
        hearings=[later, earlier])
    assert result["responsible_step"] == 3
    assert result["terminal_basis"]["decisive_facts"]["candidate_id"] == "B"


@pytest.mark.parametrize("candidate_step", [True, 0, 6, "1"])
def test_invalid_upgrade_fallback_cannot_win_source_selection(candidate_step):
    early = _hearing("A", 5)
    early["defense_result"]["parsed"].update(verdict="substantiated_upgraded", confirmed_step=None)
    upgrade = {"upgraded_candidates": [{"candidate_id": "A", "corrected_step": candidate_step}]}
    selected = adjudication._convicted_hearing([early, _hearing("B", 3, 2)], PACKET, upgrade)
    assert selected["candidate_id"] == "B"


RECORD = {"question": "Report both independent results: 2+2 and 3+3.", "history": [
    {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "2+2"}}], "tool_responses": ["5"]},
    {"role": "assistant", "tool_calls": [{"name": "calculator", "arguments": {"expr": "3+3"}}], "tool_responses": ["7"]},
    {"role": "assistant", "content": "3+3=7"},
    {"role": "assistant", "content": "I will report both results."},
    {"role": "assistant", "content": "2+2=5; 3+3=7"},
]}
CANDIDATES = [
    {"candidate_id": "A", "claim": "The first required result is 5 instead of 4.", "locus_event_id": "event_005",
     "evidence_refs": ["event_001_tool_01", "event_005"], "quote": "2+2=5", "evidence_citations": []},
    {"candidate_id": "B", "claim": "The second required result is 7 instead of 6.", "locus_event_id": "event_003",
     "evidence_refs": ["event_002_tool_01", "event_003", "event_005"], "quote": "3+3=7", "evidence_citations": []},
]


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("reverse_candidates", [False, True])
def test_full_chain_adopts_earliest_origin_with_its_own_candidate(monkeypatch, replay, reverse_candidates):
    calls = []
    candidates = list(reversed(CANDIDATES)) if reverse_candidates else CANDIDATES

    def transport(**kwargs):
        purpose = kwargs["purpose"]
        calls.append(purpose)
        if purpose == "judge":
            parsed = {"is_hallucination": False, "hallucination_step": None, "hallucination_reason": None,
                      "delivered_result": "2+2=5; 3+3=7", "correct_result": "2+2=4; 3+3=6",
                      "basis": "derivation", "confidence": "medium", "evidence_refs": [], "root_cause": None,
                      "contracts": [{"kind": "exact_value", "requirement": "Report both independent arithmetic results."}]}
        elif purpose == "recall_scanner":
            parsed = {"candidates": deepcopy(candidates)}
        elif purpose == "causal_upgrade":
            parsed = {"upgrades": [{"candidate_id": c["candidate_id"], "content_error": True, "causal": True,
                                    "corrected_step": 5 if c["candidate_id"] == "A" else 3, "evidence_refs": [],
                                    "reason": "Correcting this independent result repairs its required deliverable."}
                                   for c in candidates]}
        elif purpose == "step_tracer":
            target = json.loads(kwargs["user"].split("HEARING TARGET: ", 1)[1].split("\n\n", 1)[0])
            step = 1 if target["candidate_id"] == "A" else 2
            parsed = {"step": step, "quote": "5" if step == 1 else "7", "referent": "4" if step == 1 else "6",
                      "evidence_refs": [f"event_{step:03d}_tool_01"], "reason": "The tool first introduced this same error."}
        elif purpose == "defense":
            step = json.JSONDecoder().raw_decode(kwargs["user"].split("ALLEGATION (unverified model output):\n", 1)[1])[0]["alleged_step"]
            parsed = {"verdict": "substantiated", "confirmed_step": step, "evidence_refs": [f"event_{step:03d}"],
                      "reason": "The explicit arithmetic independently proves this required result wrong."}
        else:
            raise AssertionError(purpose)
        return parsed, {"attempts": 1, "usage": {"total_tokens": 1}}

    monkeypatch.setattr("detector.provider._provider_call", transport)
    cfg = config("off")
    cfg["causal_audit"]["mode"] = "off"
    cfg["defense_route"]["votes"] = 3
    detector = Detector(cfg, require_credentials=False)
    result = detector.detect(RECORD)
    if replay:
        frozen = LiveDetectorBackend._frozen_stages(result, set())
        previous = list(calls)
        result = detector.detect(RECORD, frozen_stages=frozen)
        assert calls == previous
    assert result["state"] == "verified_error"
    assert result["decision_authority"] == "authoritative"
    assert result["responsible_step"] == 1
    facts = result["proof"]["terminal_basis"]["decisive_facts"]
    assert (facts["candidate_id"], facts["traced_step"], facts["confirmed_step"]) == ("A", 1, 5)
    hearings = result["proof"]["hearings"]
    assert [h["candidate_id"] for h in hearings] == ["B", "A"]
    assert all(h["defense_result"]["provider"]["vote"]["quorum_met"] for h in hearings)
    # The legacy first-hearing mirror remains diagnostic, with its conflict explicit.
    assert result["proof"]["step_tracer"]["step"] == 2
    assert result["localization_authority"] == "degraded_localization_conflict"
    assert result["proof"]["localization_sources"]["tracer_step"] == 1
    assert result["proof"]["localization_conflict"]["adopted_step"] == 1
