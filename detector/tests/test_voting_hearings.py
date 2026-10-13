"""Voting and hearing-queue contracts for detector.2."""

from __future__ import annotations

from detector.adjudication import adjudicate
from detector.defense import run_defense
from detector.judge import run_judge
from detector.provider import majority_vote
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


def test_judge_three_votes_majority_positive(wrong_answer_packet, monkeypatch):
    outputs = [dict(JUDGE_FALSE), dict(JUDGE_TRUE), dict(JUDGE_TRUE)]

    def fake_call(*, route, system, user, purpose):
        return outputs.pop(0), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {
        "judge_route": {**CONFIG["judge_route"], "votes": 3},
    }
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is True
    vote = result["provider"]["vote"]
    assert vote == {"votes": 3, "cast": 3, "positives": 2, "winner": True,
                    "tie": False}


def test_judge_three_votes_tie_keeps_negative(wrong_answer_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        # Vote 1 positive, vote 2 negative, vote 3 abstains (broken twice).
        if len(calls) == 1:
            return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}
        if len(calls) == 2:
            return dict(JUDGE_FALSE), {"attempts": 1, "model": "m", "usage": {}}
        return {"is_hallucination": "yes"}, {"attempts": 1}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert result["ok"] is True
    assert result["parsed"]["is_hallucination"] is False
    vote = result["provider"]["vote"]
    assert vote["tie"] is True
    assert vote["cast"] == 2
    assert vote["positives"] == 1


def test_judge_vote_abstains_on_contract_error(wrong_answer_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        if len(calls) <= 2:
            # First vote: broken contract, repair also broken -> abstain.
            return {"is_hallucination": "yes"}, {"attempts": 1}
        return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=wrong_answer_packet, config=config)
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


def test_defense_tie_upgrades_substantiated(wrong_answer_packet, monkeypatch):
    outputs = [
        _defense_output("substantiated"),
        _defense_output("refuted"),
    ]

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        if outputs:
            return outputs.pop(0)
        # Third vote abstains: contract violation, repair also broken.
        return {"verdict": "nonsense"}, {"attempts": 1}

    monkeypatch.setattr("detector.defense.provider_call", fake_call)
    config = {"defense_route": {**CONFIG["defense_route"], "votes": 3}}
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=wrong_answer_packet, positive=positive, config=config,
    )
    assert result["ok"] is True
    assert result["parsed"]["verdict"] == "substantiated_upgraded"


def test_defense_majority_substantiated_stays_plain(wrong_answer_packet, monkeypatch):
    outputs = [
        _defense_output("substantiated"),
        _defense_output("substantiated"),
        _defense_output("refuted"),
    ]

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return outputs.pop(0)

    monkeypatch.setattr("detector.defense.provider_call", fake_call)
    config = {"defense_route": {**CONFIG["defense_route"], "votes": 3}}
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=wrong_answer_packet, positive=positive, config=config,
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


def test_upgrade_returns_all_positive_candidates(wrong_answer_packet, monkeypatch):
    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return {
            "upgrades": [
                {"candidate_id": "c1", "content_error": True, "causal": True,
                 "corrected_step": 2, "reason": "wrong value"},
                {"candidate_id": "c2", "content_error": True, "causal": True,
                 "corrected_step": 1, "reason": "wrong tool"},
            ]
        }, {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    result, status = run_causal_upgrade(
        packet=wrong_answer_packet, candidates=_candidates(), config=CONFIG,
    )
    assert status["state"] == "ok"
    upgraded = result["upgraded_candidates"]
    assert [c["candidate_id"] for c in upgraded] == ["c2", "c1"]
    assert result["upgraded_candidate"]["candidate_id"] == "c2"


def test_upgrade_majority_per_candidate(wrong_answer_packet, monkeypatch):
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

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        return outputs.pop(0), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.recaller.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_causal_upgrade(
        packet=wrong_answer_packet, candidates=_candidates(), config=config,
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
        # The real emitter forces the field to null for an upgraded verdict:
        # a half-conviction has no confirmed step by contract (defense.py).
        # A fixture that hands it a step is a shape no producer can emit, and
        # it hid the adjudication defect this fixture now exercises.
        "confirmed_step": (
            None if verdict == "substantiated_upgraded"
            else step if verdict.startswith("substantiated") else None
        ),
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


def test_second_hearing_convicts_when_first_refuted(wrong_answer_packet):
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
        packet=wrong_answer_packet,
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


def test_all_hearings_refuted_is_clean(wrong_answer_packet):
    upgrade_result = {
        "ok": True, "upgrades": [],
        "upgraded_candidate": None, "upgraded_candidates": [],
    }
    hearings = [
        _hearing("judge", 2, "refuted"),
        _hearing("recall_upgrade", 1, "refuted", candidate_id="c1"),
    ]
    proof = adjudicate(
        packet=wrong_answer_packet,
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


def test_upgraded_verdict_degrades_authority(wrong_answer_packet):
    hearings = [_hearing("judge", 2, "substantiated_upgraded")]
    proof = adjudicate(
        packet=wrong_answer_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        defense_result=hearings[0]["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )
    assert proof["state"] == "verified_error"
    assert proof["decision_authority"] == "degraded_judge_only"


def test_upgraded_verdict_convicts_with_the_tracer_step(wrong_answer_packet):
    """The half-conviction convicts; only its authority degrades.

    ``substantiated_upgraded`` + null ``confirmed_step`` is what the vote
    emitter produces for a split ballot. Treating the null as a contract
    contradiction reclassified the verdict as invalid, dropped the case into
    the "defense unsettled" branch, and took the responsible step from the
    raw allegation instead of the tracer -- so a degraded but real
    conviction was scored as an unlocalized one. The conviction stands
    (degraded authority, by the vote contract); the step is the tracer's.
    """
    hearings = [_hearing("judge", 2, "substantiated_upgraded")]
    # The producer clears the field; the tracer still names its locus (1).
    hearings[0]["tracer_result"] = {"ok": True, "parsed": {
        "step": 1, "quote": "4", "referent": "4",
        "evidence_refs": ["event_001"], "reason": "earliest",
    }}
    proof = adjudicate(
        packet=wrong_answer_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        defense_result=hearings[0]["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )
    assert proof["state"] == "verified_error"
    assert proof["decision_authority"] == "degraded_judge_only"
    facts = proof["terminal_basis"]["decisive_facts"]
    assert facts["defense"] == "substantiated_upgraded"
    assert facts["confirmed_step"] is None
    # The tracer's locus is adopted; the raw allegation (2) is not.
    assert proof["responsible_step"] == 1
    assert facts["traced_step"] == 1
    assert facts["hearings"] == [{
        "source": "judge", "candidate_id": None,
        "verdict": "substantiated_upgraded",
    }]


def test_upgraded_verdict_with_a_disagreeing_step_still_invalidates(
    wrong_answer_packet,
):
    """A *present* confirmed step that contradicts the allegation still fails.

    The null is read as "no confirmation"; a real step that disagrees is a
    contradiction and must keep the verdict invalid.
    """
    hearing = _hearing("judge", 2, "substantiated_upgraded")
    hearing["defense_result"]["parsed"]["confirmed_step"] = 1
    proof = adjudicate(
        packet=wrong_answer_packet,
        judge_result=JUDGE_TRUE_RESULT, judge_status=JUDGE_OK,
        recall_result=None, recall_status=None,
        upgrade_result=None, upgrade_status=None,
        defense_result=hearing["defense_result"],
        defense_status={"stage": "defense", "state": "ok"},
        hearings=[hearing],
    )
    # Invalid hearing -> unsettled branch: the conviction survives on the
    # preliminary positive with degraded authority, and the record says the
    # defense did not settle.
    assert proof["state"] == "verified_error"
    assert proof["decision_authority"] == "degraded_judge_only"
    assert proof["terminal_basis"]["decisive_facts"]["defense"] == "unsettled"


def test_judge_topup_refills_abstained_ballots(wrong_answer_packet, monkeypatch):
    """vote_topup re-casts abstained ballots so quorum does not shrink."""
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(purpose)
        # First two ballots abstain, the topup succeeds twice.
        if len(calls) <= 2:
            return {"is_hallucination": "yes"}, {"attempts": 1}
        return dict(JUDGE_FALSE), {"attempts": 1, "model": "m", "usage": {}}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {
        "judge_route": {**CONFIG["judge_route"], "votes": 2, "vote_topup": 2},
    }
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert result["ok"] is True
    vote = result["provider"]["vote"]
    assert vote["cast"] == 2
    assert vote["topups"] == 1


def test_judge_topup_absent_keeps_abstention_contract(wrong_answer_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(1)
        return {"is_hallucination": "yes"}, {"attempts": 1}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert result["ok"] is False
    assert result["error"] == "every judge vote failed"
    # 3 ballots x (initial + contract repair) provider calls, no topup.
    assert len(calls) == 6


def test_defense_topup_refills_abstained_ballots(wrong_answer_packet, monkeypatch):
    calls = []

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        calls.append(1)
        # Ballot 1 fails both its initial call and its contract repair.
        if len(calls) <= 2:
            return {"verdict": "nonsense"}, {"attempts": 1}
        return _defense_output("substantiated")

    monkeypatch.setattr("detector.defense.provider_call", fake_call)
    config = {
        "defense_route": {**CONFIG["defense_route"], "votes": 2, "vote_topup": 1},
    }
    positive = {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }
    result, status = run_defense(
        packet=wrong_answer_packet, positive=positive, config=config,
    )
    assert result["ok"] is True
    assert result["parsed"]["verdict"] == "substantiated"
    vote = result["provider"]["vote"]
    assert vote["cast"] == 2
    assert vote["topups"] == 1


def _defense_positive():
    return {
        "source": "judge", "step": 2, "reason": "wrong value",
        "delivered_result": "5", "correct_result": "4",
        "evidence_refs": ["event_002"],
    }


def test_defense_no_majority_never_overturns_and_is_order_independent(wrong_answer_packet, monkeypatch):
    """A tied defense must degrade, not overturn, whatever the order.

    The previous collapse fell back to ``ballots[0]`` when no verdict had a
    strict majority, so ``[refuted, inconclusive]`` cleared the case and
    ``[inconclusive, refuted]`` kept it convicted for the same vote multiset.
    """
    for outputs in (
        ["refuted", "inconclusive"],
        ["inconclusive", "refuted"],
    ):
        queue = [_defense_output(v) for v in outputs]

        def fake_call(*, route, system, user, purpose, cache_tag="", queue=queue):
            return queue.pop(0)

        monkeypatch.setattr("detector.defense.provider_call", fake_call)
        config = {"defense_route": {**CONFIG["defense_route"], "votes": 2}}
        result, status = run_defense(
            packet=wrong_answer_packet, positive=_defense_positive(), config=config,
        )
        assert result["ok"] is True
        assert result["parsed"]["verdict"] == "inconclusive"
        assert result["provider"]["vote"]["tie"] is True


def test_judge_quorum_rejects_a_single_surviving_ballot(wrong_answer_packet, monkeypatch):
    """One valid ballot must not decide a 3-vote judge."""
    calls = []

    def fake_call(*, route, system, user, purpose):
        calls.append(1)
        if len(calls) == 1:
            return dict(JUDGE_TRUE), {"attempts": 1, "model": "m", "usage": {}}
        return {"is_hallucination": "yes"}, {"attempts": 1}

    monkeypatch.setattr("detector.judge.provider_call", fake_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_judge(packet=wrong_answer_packet, config=config)
    assert result["ok"] is False
    assert "quorum not reached" in result["error"]
    assert status["state"] == "no_quorum"


def test_defense_quorum_keeps_conviction_and_degrades(wrong_answer_packet, monkeypatch):
    """An under-quorum defense keeps the conviction instead of clearing it."""
    calls = []

    def fake_call(*, route, system, user, purpose, cache_tag=""):
        calls.append(1)
        if len(calls) == 1:
            return _defense_output("refuted")
        return {"verdict": "nonsense"}, {"attempts": 1}

    monkeypatch.setattr("detector.defense.provider_call", fake_call)
    config = {"defense_route": {**CONFIG["defense_route"], "votes": 3}}
    result, status = run_defense(
        packet=wrong_answer_packet, positive=_defense_positive(), config=config,
    )
    assert result["ok"] is True
    assert result["parsed"]["verdict"] == "inconclusive"
    assert result["provider"]["vote"]["quorum_met"] is False


def test_upgrade_quorum_rejects_a_single_surviving_sample(wrong_answer_packet, monkeypatch):
    """The causal upgrade had no top-up and no quorum floor at all."""
    from detector.provider import ProviderError

    calls = []

    def failing_call(*, route, system, user, purpose, cache_tag=""):
        calls.append(1)
        if len(calls) == 1:
            return {"upgrades": [
                {"candidate_id": "c1", "content_error": True, "causal": True,
                 "corrected_step": 2, "reason": "r"},
                {"candidate_id": "c2", "content_error": False, "causal": False,
                 "corrected_step": None, "reason": "r"},
            ]}, {"attempts": 1, "model": "m", "usage": {}}
        raise ProviderError("causal_upgrade", "boom", attempts=1)

    monkeypatch.setattr("detector.recaller.provider_call", failing_call)
    config = {"judge_route": {**CONFIG["judge_route"], "votes": 3}}
    result, status = run_causal_upgrade(
        packet=wrong_answer_packet, candidates=_candidates(), config=config,
    )
    assert result["ok"] is False
    assert status["state"] == "no_quorum"
    assert result["upgraded_candidates"] == []
