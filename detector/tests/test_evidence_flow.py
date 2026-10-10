"""The snapshot travels beside the record and reaches every acting stage."""

from __future__ import annotations

import pytest

from detector.api import Detector
from detector.evidence import EVIDENCE_SCHEMA, excerpt_sha256, snapshot_digest

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


def _source(**overrides):
    row = {
        "evidence_id": "E1",
        "url": "https://example.org/ledger",
        "title": "Ledger entry",
        "publisher": "example.org",
        "retrieved_at": "2026-09-18T10:00:00+00:00",
        "excerpt": "The vessel measured 116 tons in the 1922 register.",
        "verifies": {"entity": "Lisette", "attribute": "tonnage",
                     "value": "116"},
        "applicable_context": "1873 hull, decoy craft service 1917",
    }
    row.update(overrides)
    row["content_sha256"] = excerpt_sha256(row["excerpt"])
    return row


def snapshot(sources=None, **extra):
    payload = {
        "schema_version": EVIDENCE_SCHEMA,
        "query": {"text": "Lisette tonnage 1873", "derivation": "task",
                  "task_excerpt": "How many tons was the Lisette?"},
        "sources": sources or [_source()],
    }
    payload.update(extra)
    return payload


LISETTE_RECORD = {
    "question": "How many tons was the topsail schooner \"Lisette\", built in 1873?",
    "history": [
        {"step": 1, "role": "assistant", "content": "planning"},
        {"step": 2, "role": "assistant", "content": "The answer is 76 tons."},
    ],
}


@pytest.mark.parametrize("stage", ["scanner", "upgrade", "defense"])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("with_evidence", [False, True])
def test_acting_stages_forward_the_complete_snapshot_identity(monkeypatch, stage, repair, with_evidence):
    from detector.defense import run_defense
    from detector.packet import build_packet
    from detector.recaller import run_causal_upgrade, run_recall_scan
    packet = build_packet(LISETTE_RECORD)
    evidence = snapshot() if with_evidence else None
    seen = []
    replies = {
        "scanner": {"candidates": []},
        "upgrade": {"upgrades": [{"candidate_id": "c1", "content_error": False, "causal": False,
                                    "corrected_step": None, "reason": "not established"}]},
        "defense": {"verdict": "inconclusive", "confirmed_step": None,
                     "evidence_refs": [], "reason": "not established"},
    }
    def provider(**kwargs):
        seen.append(kwargs)
        return ({}, {}) if repair and len(seen) == 1 else (replies[stage], {"attempts": 1})
    module = "defense" if stage == "defense" else "recaller"
    monkeypatch.setattr(f"detector.{module}.provider_call", provider)
    if stage == "scanner":
        result, status = run_recall_scan(packet=packet, config=CONFIG, snapshot=evidence)
    elif stage == "upgrade":
        result, status = run_causal_upgrade(packet=packet, config=CONFIG, snapshot=evidence,
                                           candidates=[CANDIDATE])
    else:
        result, status = run_defense(packet=packet, config=CONFIG, snapshot=evidence,
                                     positive={"source": "judge", "step": 2, "reason": "wrong value"})
    assert result["ok"] and status["state"] == "ok"
    assert len(seen) == (2 if repair else 1)
    expected = snapshot_digest(evidence) if evidence is not None else ""
    assert all(call.get("cache_tag", "") == expected for call in seen)


def _judge(positive=False):
    return {
        "ok": True,
        "parsed": {
            "is_hallucination": positive,
            "hallucination_step": 2 if positive else None,
            "hallucination_reason": "wrong value" if positive else None,
            "delivered_result": "76 tons" if positive else "4",
            "correct_result": "116 tons" if positive else "4",
            "basis": None,
            "confidence": "high",
            "evidence_refs": ["event_002"] if positive else [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "116 tons"}],
        },
        "provider": {"model": "m"},
    }


JUDGE_STATUS = {
    "stage": "judge", "state": "ok", "attempts": 1,
    "view": "complete", "warnings": [],
}


def _install(monkeypatch, judge_result, recall, upgrade, defense=None):
    seen = {}

    def fake_judge(*, packet, config, plan):
        return judge_result, JUDGE_STATUS

    def fake_recall(*, packet, config, plan, **kwargs):
        seen["recall_snapshot"] = kwargs.get("snapshot")
        return recall

    def fake_upgrade(*, packet, candidates, config, plan, **kwargs):
        seen["upgrade_snapshot"] = kwargs.get("snapshot")
        return upgrade

    monkeypatch.setattr("detector.api.run_judge", fake_judge)
    monkeypatch.setattr("detector.api.run_recall_scan", fake_recall)
    monkeypatch.setattr("detector.api.run_causal_upgrade", fake_upgrade)
    if defense is not None:
        def fake_defense(*, packet, positive, config, plan, **kwargs):
            seen["defense_snapshot"] = kwargs.get("snapshot")
            return defense
        monkeypatch.setattr("detector.api.run_step_tracer", lambda **k: (
            {"ok": True, "parsed": {
                "step": 2, "quote": "76 tons", "referent": "116 tons",
                "evidence_refs": ["event_002"], "reason": "r",
            }},
            {"stage": "step_tracer", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []},
        ))
        monkeypatch.setattr("detector.api.run_defense", fake_defense)
    return seen


CANDIDATE = {
    "candidate_id": "c1", "claim": "said 76 instead of 116",
    "locus_event_id": "event_002", "evidence_refs": ["event_002"],
    "evidence_citations": ["E1"], "quote": "76 tons",
}

RECALL_WITH_CANDIDATE = (
    {"ok": True, "candidates": [CANDIDATE]},
    {"stage": "recall_scanner", "state": "ok", "attempts": 0,
     "view": "complete", "warnings": []},
)

UPGRADE_NONE = (
    {"ok": True, "upgrades": [], "upgraded_candidate": None},
    {"stage": "causal_upgrade", "state": "ok", "attempts": 0,
     "view": "complete", "warnings": []},
)


def _frozen_recall(*, citations=(), positive=True):
    candidate = {**CANDIDATE, "evidence_citations": list(citations)}
    row = {
        "candidate_id": "c1", "content_error": positive, "causal": positive,
        "corrected_step": 2 if positive else None, "reason": "wrong value",
        "evidence_refs": list(citations),
    }
    return {
        "judge_result": _judge(), "judge_status": dict(JUDGE_STATUS),
        "recall_result": {"ok": True, "candidates": [candidate]},
        "recall_status": RECALL_WITH_CANDIDATE[1],
        "upgrade_result": {"ok": True, "upgrades": [row]},
        "upgrade_status": UPGRADE_NONE[1],
    }


def test_frozen_recall_preserves_snapshot_citations_through_hearing(monkeypatch):
    frozen = _frozen_recall(citations=["E1"])
    frozen["evidence_snapshot_digest"] = snapshot_digest(snapshot())

    def unexpected(**kwargs):
        raise AssertionError("frozen stage must be replayed")

    monkeypatch.setattr("detector.api.run_judge", unexpected)
    monkeypatch.setattr("detector.api.run_recall_scan", unexpected)
    monkeypatch.setattr("detector.api.run_causal_upgrade", unexpected)
    heard = []
    def tracer(**kwargs):
        heard.append(kwargs["positive"])
        return {"ok": True, "parsed": {
            "step": 2, "quote": "76 tons", "referent": "116 tons",
            "evidence_refs": ["event_002"], "reason": "wrong value",
        }}, {"stage": "step_tracer", "state": "ok", "view": "complete"}
    def defense(**kwargs):
        heard.append(kwargs["positive"])
        return {"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 2,
            "evidence_refs": ["event_002"], "reason": "wrong value",
        }}, {"stage": "defense", "state": "ok", "view": "complete"}
    monkeypatch.setattr("detector.api.run_step_tracer", tracer)
    monkeypatch.setattr("detector.api.run_defense", defense)
    result = Detector(CONFIG, require_credentials=False).detect(
        LISETTE_RECORD, evidence=snapshot(), frozen_stages=frozen,
    )
    assert result["state"] == "verified_error"
    assert len(heard) == 2
    assert all(item["evidence_citations"] == ["E1"] for item in heard)
    assert result["proof"]["causal_upgrades"][0]["evidence_refs"] == ["event_002"]


@pytest.mark.parametrize("positive", [False, True])
def test_frozen_upgrade_discards_supplied_derived_candidates(positive):
    from detector.api import _validate_frozen_stages
    from detector.packet import build_packet

    frozen = _frozen_recall(positive=positive)
    forged = {"candidate_id": "forged", "corrected_step": 1,
              "claim": "fabricated", "evidence_refs": ["event_001"]}
    frozen["upgrade_result"].update(
        upgraded_candidates=[forged], upgraded_candidate=(
            {"candidate_id": "c1", "corrected_step": 2} if positive else None
        ),
    )
    result = _validate_frozen_stages(frozen, build_packet(LISETTE_RECORD))
    upgraded = result["upgrade_result"]
    assert [row["candidate_id"] for row in upgraded["upgraded_candidates"]] == (
        ["c1"] if positive else []
    )
    assert upgraded["upgraded_candidate"] == (
        upgraded["upgraded_candidates"][0] if positive else None
    )
    assert frozen["upgrade_result"]["upgraded_candidates"] == [forged]


@pytest.mark.parametrize("snap,citations", [(None, ["E1"]), (snapshot(), ["E9"])])
def test_frozen_recall_rejects_unbound_snapshot_citations(snap, citations):
    from detector.api import _validate_frozen_stages
    from detector.packet import build_packet

    with pytest.raises(ValueError, match="contract error"):
        _validate_frozen_stages(
            _frozen_recall(citations=citations), build_packet(LISETTE_RECORD), snap,
        )


def test_live_and_frozen_upgrade_assembly_share_order_and_owned_references():
    from detector.api import _validate_frozen_stages
    from detector.packet import build_packet
    from detector.recaller import assemble_upgraded_candidates

    frozen = _frozen_recall()
    earlier = {**CANDIDATE, "candidate_id": "earlier", "locus_event_id": "event_001",
               "evidence_refs": ["event_001"], "evidence_citations": []}
    frozen["recall_result"]["candidates"].append(earlier)
    frozen["upgrade_result"]["upgrades"].append({
        "candidate_id": "earlier", "content_error": True, "causal": True,
        "corrected_step": 1, "reason": "first deviation",
    })
    normalized = _validate_frozen_stages(frozen, build_packet(LISETTE_RECORD))
    upgraded = normalized["upgrade_result"]
    candidates = normalized["recall_result"]["candidates"]
    assert upgraded["upgraded_candidates"] == assemble_upgraded_candidates(
        candidates, upgraded["upgrades"],
    )
    assert [row["candidate_id"] for row in upgraded["upgraded_candidates"]] == [
        "earlier", "c1",
    ]
    assert upgraded["upgraded_candidate"]["candidate_id"] == "earlier"
    upgraded["upgraded_candidates"][0]["evidence_refs"].clear()
    assert candidates[1]["evidence_refs"] == ["event_001"]
    assert earlier["evidence_refs"] == ["event_001"]


def test_failed_frozen_upgrade_cannot_supply_a_positive_hearing():
    from detector.api import _validate_frozen_stages
    from detector.packet import build_packet

    frozen = _frozen_recall()
    frozen["upgrade_result"].update(
        ok=False, upgraded_candidates=[{"candidate_id": "forged"}],
        upgraded_candidate={"candidate_id": "forged"},
    )
    frozen["upgrade_status"] = {"state": "provider_error"}
    result = _validate_frozen_stages(frozen, build_packet(LISETTE_RECORD))
    assert result["upgrade_result"]["upgraded_candidates"] == []
    assert result["upgrade_result"]["upgraded_candidate"] is None


def test_execution_identity_binds_the_replay_payload_actually_selected(monkeypatch):
    from detector.identity import digest
    primary = {"judge_result": _judge(), "judge_status": dict(JUDGE_STATUS)}
    ignored = {"judge_result": _judge(), "upgrade_result": {"ignored": True}}
    _install(monkeypatch, _judge(),
             recall=({"ok": True, "candidates": []}, RECALL_WITH_CANDIDATE[1]),
             upgrade=UPGRADE_NONE)
    detector = Detector(CONFIG, require_credentials=False)
    first = detector.detect(LISETTE_RECORD, frozen_judge=primary, frozen_stages=ignored)
    identity = first["provenance"]["execution_identity"]
    assert identity["replay"] == digest(primary)
    assert first["provenance"]["frozen_stages"] == ["judge_result"]
    ignored["upgrade_result"]["ignored"] = False
    second = detector.detect(LISETTE_RECORD, frozen_judge=primary, frozen_stages=ignored)
    assert second["provenance"]["execution_identity"] == identity
    primary["judge_result"]["parsed"]["delivered_result"] = "changed delivery"
    third = detector.detect(LISETTE_RECORD, frozen_judge=primary, frozen_stages=ignored)
    assert third["proof"]["judge"]["delivered_result"] == "changed delivery"
    assert third["provenance"]["execution_identity"]["digest"] != identity["digest"]


def test_snapshot_reopens_the_scan_for_a_unanimous_clean_judge(
    monkeypatch,
):
    """A high-confidence clean judge alone would skip the scan entirely."""
    judge_result = _judge(positive=False)
    judge_result["provider"] = {
        "model": "m",
        "vote": {"votes": 3, "cast": 3, "positives": 0,
                 "winner": False, "tie": False},
    }
    config = {
        **CONFIG,
        "judge_route": {**CONFIG["judge_route"], "votes": 3},
    }
    seen = _install(
        monkeypatch, judge_result,
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    # Without a snapshot the unanimous clean judge skips the scan.
    Detector(config, require_credentials=False).detect(LISETTE_RECORD, case_id="x")
    assert "recall_snapshot" not in seen

    # With one, the scan runs and reads the snapshot.
    seen.clear()
    Detector(config, require_credentials=False).detect(
        LISETTE_RECORD, case_id="x", evidence=snapshot(),
    )
    assert seen["recall_snapshot"]["schema_version"] == EVIDENCE_SCHEMA


def test_snapshot_reaches_recall_and_upgrade_stages(monkeypatch):
    seen = _install(
        monkeypatch, _judge(positive=False),
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    snap = snapshot()
    result = Detector(CONFIG, require_credentials=False).detect(LISETTE_RECORD, case_id="x", evidence=snap)
    assert seen["recall_snapshot"]["schema_version"] == EVIDENCE_SCHEMA
    assert seen["upgrade_snapshot"]["schema_version"] == EVIDENCE_SCHEMA
    assert result["proof"]["external_evidence"]["sources"][0]["evidence_id"] == "E1"
    assert result["provenance"]["evidence_snapshot_digest"] == snapshot_digest(snap)


def test_no_snapshot_keeps_stage_calls_unchanged(monkeypatch):
    seen = _install(
        monkeypatch, _judge(positive=False),
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    result = Detector(CONFIG, require_credentials=False).detect(LISETTE_RECORD, case_id="x")
    assert seen["recall_snapshot"] is None
    assert seen["upgrade_snapshot"] is None
    assert result["proof"]["external_evidence"] is None
    assert "evidence_snapshot_digest" not in result["provenance"]


def test_upgrade_hearing_carries_citations_to_defense(monkeypatch):
    recall = (
        {"ok": True, "candidates": [{
            "candidate_id": "c1", "claim": "said 76 instead of 116",
            "locus_event_id": "event_002", "evidence_refs": ["event_002"],
            "evidence_citations": ["E1"], "quote": "76 tons",
        }]},
        {"stage": "recall_scanner", "state": "ok", "attempts": 0,
         "view": "complete", "warnings": []},
    )
    upgrade = (
        {"ok": True, "upgrades": [], "upgraded_candidate": {
            "candidate_id": "c1", "content_error": True, "causal": True,
            "corrected_step": 2, "reason": "wrong value",
            "locus_event_id": "event_002", "evidence_refs": ["event_002"],
            "evidence_citations": ["E1"],
            "claim": "said 76 instead of 116", "quote": "76 tons",
        }},
        {"stage": "causal_upgrade", "state": "ok", "attempts": 0,
         "view": "complete", "warnings": []},
    )
    # Force the judge negative so the recall upgrade drives the hearing.
    seen = _install(
        monkeypatch, _judge(positive=False), recall, upgrade,
        defense=({"ok": True, "parsed": {
            "verdict": "substantiated", "confirmed_step": 2,
            "evidence_refs": ["event_002"], "evidence_citations": ["E1"],
            "reason": "grounded in the register",
        }}, {"stage": "defense", "state": "ok", "attempts": 0,
             "view": "complete", "warnings": []}),
    )
    result = Detector(CONFIG, require_credentials=False).detect(
        LISETTE_RECORD, case_id="x", evidence=snapshot(),
    )
    assert seen["defense_snapshot"]["schema_version"] == EVIDENCE_SCHEMA
    hearing = result["proof"]["hearings"][0]
    assert hearing["evidence_citations"] == ["E1"]
    assert result["state"] == "verified_error"


def test_invalid_snapshot_is_refused_before_any_call(sample_record):
    called = {"judge": 0}

    with pytest.raises(Exception):
        Detector(CONFIG, require_credentials=False).detect(
            sample_record, case_id="x",
            evidence={"schema_version": EVIDENCE_SCHEMA, "query": {}},
        )
    assert called["judge"] == 0


UNRELATED_RECORD = {
    "question": "Who was the first cardiologist in Kashmir?",
    "history": [
        {"step": 1, "role": "assistant", "content": "Searching for the name."},
        {"step": 2, "role": "assistant",
         "content": "The first cardiologist in Kashmir was Dr. Upendra Kaul."},
    ],
}


def test_unrelated_snapshot_is_recorded_but_not_applied(monkeypatch):
    """A snapshot naming nothing in the case must not reopen its review."""
    judge_result = _judge(positive=False)
    judge_result["provider"] = {
        "model": "m",
        "vote": {"votes": 3, "cast": 3, "positives": 0,
                 "winner": False, "tie": False},
    }
    config = {**CONFIG, "judge_route": {**CONFIG["judge_route"], "votes": 3}}
    seen = _install(
        monkeypatch, judge_result,
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    result = Detector(config, require_credentials=False).detect(
        UNRELATED_RECORD, case_id="x", evidence=snapshot(),
    )
    # The scan was not reopened, and no stage received the snapshot.
    assert "recall_snapshot" not in seen
    # The attachment and the refusal to apply it stay auditable.
    assert result["provenance"]["evidence_snapshot_digest"] == snapshot_digest(
        snapshot()
    )
    assert result["provenance"]["evidence_applied"] is False
    assert "does not bear on this case" in (
        result["provenance"]["evidence_application_reason"]
    )
    assert result["proof"]["external_evidence"] is None


def test_matching_snapshot_is_marked_applied(monkeypatch):
    seen = _install(
        monkeypatch, _judge(positive=False),
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    result = Detector(CONFIG, require_credentials=False).detect(
        LISETTE_RECORD, case_id="x", evidence=snapshot(),
    )
    assert result["provenance"]["evidence_applied"] is True
    assert "appears" in result["provenance"]["evidence_application_reason"]
    assert result["proof"]["external_evidence"] is not None


def test_frozen_recall_replay_is_refused_with_an_active_snapshot():
    """A frozen recall stage recorded without the snapshot would mask it."""
    judge_result = _judge(positive=False)
    frozen = {
        "packet_hash": None,
        "judge_result": {"ok": True, "parsed": judge_result["parsed"]},
        "judge_status": dict(JUDGE_STATUS),
        "recall_result": {"ok": True, "candidates": []},
        "recall_status": {
            "stage": "recall_scanner", "state": "ok", "attempts": 0,
            "view": "complete", "warnings": [],
        },
    }
    with pytest.raises(ValueError, match="frozen stage replay is refused"):
        Detector(CONFIG, require_credentials=False).detect(
            LISETTE_RECORD, case_id="x", frozen_stages=frozen,
            evidence=snapshot(),
        )


def test_judge_only_freeze_is_compatible_with_a_snapshot(monkeypatch):
    judge_result = _judge(positive=False)
    frozen = {
        "judge_result": {"ok": True, "parsed": judge_result["parsed"]},
        "judge_status": dict(JUDGE_STATUS),
    }
    seen = _install(
        monkeypatch, judge_result,
        recall=RECALL_WITH_CANDIDATE, upgrade=UPGRADE_NONE,
    )
    result = Detector(CONFIG, require_credentials=False).detect(
        LISETTE_RECORD, case_id="x", frozen_stages=frozen,
        evidence=snapshot(),
    )
    assert result["provenance"]["frozen_judge_replay"] is True
    assert seen["recall_snapshot"]["schema_version"] == EVIDENCE_SCHEMA
