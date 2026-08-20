"""Voting and hearing-queue contracts for detector_v2.2."""

from __future__ import annotations

from detector_v2.adjudication import adjudicate
from detector_v2.defense import run_defense
from detector_v2.judge import run_judge
from detector_v2.provider import majority_vote
from detector_v2.recaller import run_causal_upgrade

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
JUDGE_FALSE = {
    "is_hallucination": False,
    "hallucination_step": None,
    "hallucination_reason": None,
    "delivered_result": "4",
    "correct_result": "4",
    "basis": None,
    "confidence": "high",
    "evidence_refs": [],
    "root_cause": None,
    "contracts": [{"kind": "exact_value", "requirement": "the result 4"}],
}


def test_majority_vote_strict_and_order_independent():
    assert majority_vote([True, True, False], on_tie=False)[0] is True
    assert majority_vote([False, True, False], on_tie=False)[0] is False
    assert majority_vote([True, False, None], on_tie=False)[0] is False
    assert majority_vote([True, False], on_tie=False)[0] is False
    assert majority_vote([True, True], on_tie="x")[0] is True
    assert majority_vote([None, None], on_tie="x")[0] == "x"
    assert majority_vote([None, True], on_tie=False)[0] is True
    assert majority_vote([True, True, False], on_tie=False) == (True, 3)


def test_judge_three_votes_majority_positive(sample_packet, monkeypatch):
    outputs = [dict(JUDGE_FALSE), dict(JUDGE_TRUE), dict(JUDGE_TRUE)]

    def fake_call(*, route, system, user, purpose):
        return outputs.pop(0), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    config = {
        "judge_route": {**CONFIG["judge_route"], "votes": 3},
    }
    result, status = run_judge(packet=sample_packet, config=config)
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is True
    vote = result["provider"]["vote"]
    assert vote == {"votes": 3, "cast": 3, "positives": 2, "winner": True,
                    "tie": False}


def test_judge_three_votes_tie_keeps_negative(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        # Vote 1 positive, vote 2 negative, vote 3 abstains (broken twice).
        if len(calls) == 1:
            return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}
        if len(calls) == 2:
            return dict(JUDGE_FALSE), {"attempts": 1, "model": "m", "usage": {}}
        return {"is_hallucination": "yes"}, {"attempts": 1}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=sample_packet, config=config)
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is False
    vote = result["provider"]["vote"]
    assert vote["tie"] is True
    assert vote["cast"] == 2
    assert vote["positives"] == 1


def test_judge_vote_abstains_on_contract_error(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        if len(calls) <= 2:
            # First vote: broken contract, repair also broken -> abstain.
            return {"is_hallucination": "yes"}, {"attempts": 1}
        return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=sample_packet, config=config)
    # First vote abstained (contract error twice), second and third positive.
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is True
    assert result["provider"]["vote"]["cast"] == 2
    assert status["warnings"][0]["error"].startswith("contract:")


def _defense_output(verdict, step=2):
    return {
        "verdict": verdict,
        "confirmed_step": step if verdict == "substantiated" else None,
        "evidence_refs": ["event_002"],
        "reason": "r",
    }, {"attempts": 1, "model": "m", "usage": {}}


def test_defense_tie_upgrades_substantiated(sample_packet, monkeypatch):
    outputs = [
        _defense_output("substantiated"),
        _defense_output("refuted"),
    ]

    def fake_call(*, route, system, user, purpose):
        if outputs:
            return outputs.pop(0)
        # Third vote abstains: contract violation, repair also broken.
        return {"verdict": "nonsense"}, {"attempts": 1}

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    config = {"defense_route": {**CONFIG["defense_route"], "votes": 3}}
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=sample_packet, positive=positive, config=config,
    )
    assert result["ok"] is True
    assert result["parsed"]["verdict"] == "substantiated_upgraded"


def test_defense_majority_substantiated_stays_plain(sample_packet, monkeypatch):
    outputs = [
        _defense_output("substantiated"),
        _defense_output("substantiated"),
        _defense_output("refuted"),
    ]

    def fake_call(*, route, system, user, purpose):
        return outputs.pop(0)

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    config = {"defense_route": {**CONFIG["defense_route"], "votes": 3}}
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=sample_packet, positive=positive, config=config,
    )
    assert result["parsed"]["verdict"] == "substantiated"


def _candidates():
    return [
        {
            "candidate_id": "c1",
            "claim": "wrote 5 instead of 4",
            "locus_event_id": "event_002",
            "evidence_refs": ["event_002"],
            "quote": "5",
        },
        {
            "candidate_id": "c2",
            "claim": "called the wrong tool",
            "locus_event_id": "event_001",
            "evidence_refs": ["event_001"],
            "quote": "calculator",
        },
    ]


def test_upgrade_returns_all_positive_candidates(sample_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose):
        return {
            "upgrades": [
                {"candidate_id": "c1", "content_error": True, "causal": True,
                 "corrected_step": 2, "reason": "wrong value"},
                {"candidate_id": "c2", "content_error": True, "causal": True,
                 "corrected_step": 1, "reason": "wrong tool"},
            ]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "ok"
    upgraded = result["upgraded_candidates"]
    assert [c["candidate_id"] for c in upgraded] == ["c2", "c1"]
    assert result["upgraded_candidate"]["candidate_id"] == "c2"


def test_upgrade_majority_per_candidate(sample_packet, monkeypatch):
    outputs = [
        {"upgrades": [
            {"candidate_id": "c1", "content_error": True, "causal": True,
             "corrected_step": 2, "reason": "r"},
            {"candidate_id": "c2", "content_error": False, "causal": False,
             "corrected_step": None, "reason": "r"},
        ]},
        {"upgrades": [
            {"candidate_id": "c1", "content_error": False, "causal": False,
             "corrected_step": None, "reason": "r"},
            {"candidate_id": "c2", "content_error": True, "causal": True,
             "corrected_step": 1, "reason": "r"},
        ]},
        {"upgrades": [
            {"candidate_id": "c1", "content_error": True, "causal": True,
             "corrected_step": 2, "reason": "r"},
            {"candidate_id": "c2", "content_error": False, "causal": False,
             "corrected_step": None, "reason": "r"},
        ]},
    ]

    def fake_call(*, route, system, user, purpose):
        return outputs.pop(0), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.recaller.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_causal_upgrade(
        packet=sample_packet, candidates=_candidates(), config=config,
    )
    assert status["state"] == "ok"
    assert [c["candidate_id"] for c in result["upgraded_candidates"]] == ["c1"]
    facts = result["provider"]["vote"]["per_candidate"]
    assert facts["c1"] == {"cast": 3, "positives": 2, "winner": True,
                           "tie": False}
    assert facts["c2"]["winner"] is False


JUDGE_OK = {"stage": "judge", "state": "ok", "attempts": 1, "view": "complete", "warnings": []}
JUDGE_TRUE_RESULT = {"ok": True, "parsed": dict(JUDGE_TRUE)}
JUDGE_FALSE_RESULT = {"ok": True, "parsed": dict(JUDGE_FALSE)}
RECALL_EMPTY = ({"ok": True, "candidates": []},
                {"stage": "recall_scanner", "state": "ok"})
UPGRADE_EMPTY = ({"ok": True, "upgrades": [], "upgraded_candidate": None,
                  "upgraded_candidates": []},
                 {"stage": "causal_upgrade", "state": "not_needed"})


def _hearing(source, step, verdict, candidate_id=None):
    event_id = f"event_{step:03d}"
    parsed = {
        "verdict": verdict,
        "confirmed_step": step if verdict.startswith("substantiated") else None,
        "evidence_refs": [event_id],
        "reason": "r",
    }
    return {
        "source": source,
        "candidate_id": candidate_id,
        "step": step,
        "reason": "allegation",
        "delivered_result": "5",
        "correct_result": "4",
        "evidence_refs": [event_id],
        "quote": "5",
        "tracer_result": {"ok": True, "parsed": {
            "step": step, "quote": "5", "referent": "4",
            "evidence_refs": [event_id], "reason": "earliest",
        }},
        "defense_result": {"ok": True, "parsed": parsed},
    }


def test_second_hearing_convicts_when_first_refuted(sample_packet):
    """The judge hearing is refuted but a recall candidate substantiates."""
    upgrade_result = {
        "ok": True,
        "upgrades": [],
        "upgraded_candidate": {
            "candidate_id": "c1", "content_error": True, "causal": True,
            "corrected_step": 1, "reason": "r",
            "locus_event_id": "event_001", "evidence_refs": ["event_001"],
            "claim": "wrong tool", "quote": "calculator",
        },
        "upgraded_candidates": [{
            "candidate_id": "c1", "content_error": True, "causal": True,
            "corrected_step": 1, "reason": "r",
            "locus_event_id": "event_001", "evidence_refs": ["event_001"],
            "claim": "wrong tool", "quote": "calculator",
        }],
    }
    hearings = [
        _hearing("judge", 2, "refuted"),
        _hearing("recall_upgrade", 1, "substantiated", candidate_id="c1"),
    ]
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=RECALL_EMPTY[0], recall_status=RECALL_EMPTY[1],
        upgrade_result=upgrade_result,
        upgrade_status={"stage": "causal_upgrade", "state": "ok"},
        defense_result=hearings[0]["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )
    assert proof["state"] == "verified_error"
    facts = proof["terminal_basis"]["decisive_facts"]
    assert facts["positive_source"] == "recall_upgrade"
    assert facts["defense"] == "substantiated"
    assert proof["responsible_step"] == 1


def test_all_hearings_refuted_is_clean(sample_packet):
    upgrade_result = {
        "ok": True, "upgrades": [],
        "upgraded_candidate": None, "upgraded_candidates": [],
    }
    hearings = [
        _hearing("judge", 2, "refuted"),
        _hearing("recall_upgrade", 1, "refuted", candidate_id="c1"),
    ]
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=RECALL_EMPTY[0], recall_status=RECALL_EMPTY[1],
        upgrade_result=upgrade_result,
        upgrade_status={"stage": "causal_upgrade", "state": "ok"},
        defense_result=hearings[0]["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )
    assert proof["state"] == "verified_clean"
    assert proof["terminal_basis"]["decisive_facts"]["defense"] == "refuted"


def test_upgraded_verdict_degrades_authority(sample_packet):
    hearings = [_hearing("judge", 2, "substantiated_upgraded")]
    proof = adjudicate(
        packet=sample_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        defense_result=hearings[0]["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )
    assert proof["state"] == "verified_error"
    assert proof["decision_authority"] == "degraded_judge_only"


def test_judge_topup_refills_abstained_ballots(sample_packet, monkeypatch):
    """vote_topup re-casts abstained ballots so quorum does not shrink."""
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        # First two ballots abstain, the topup succeeds twice.
        if len(calls) <= 2:
            return {"is_hallucination": "yes"}, {"attempts": 1}
        return dict(JUDGE_FALSE), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    config = {
        "judge_route": {**CONFIG["judge_route"], "votes": 2, "vote_topup": 2},
    }
    result, status = run_judge(packet=sample_packet, config=config)
    assert result["ok"] is True
    vote = result["provider"]["vote"]
    assert vote["cast"] == 2
    assert vote["topups"] == 1


def test_judge_topup_absent_keeps_abstention_contract(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(1)
        return {"is_hallucination": "yes"}, {"attempts": 1}

    monkeypatch.setattr("detector_v2.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=sample_packet, config=config)
    assert result["ok"] is False
    assert result["error"] == "every judge vote failed"
    # 3 ballots x (initial + contract repair) provider calls, no topup.
    assert len(calls) == 6


def test_defense_topup_refills_abstained_ballots(sample_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(1)
        # Ballot 1 fails both its initial call and its contract repair.
        if len(calls) <= 2:
            return {"verdict": "nonsense"}, {"attempts": 1}
        return _defense_output("substantiated")

    monkeypatch.setattr("detector_v2.defense.provider_call", fake_call)
    config = {
        "defense_route": {**CONFIG["defense_route"], "votes": 2, "vote_topup": 1},
    }
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=sample_packet, positive=positive, config=config,
    )
    assert result["ok"] is True
    assert result["parsed"]["verdict"] == "substantiated"
    vote = result["provider"]["vote"]
    assert vote["cast"] == 2
    assert vote["topups"] == 1
