from __future__ import annotations

from evolution_v3.backends import DetectionBackend, DetectorV2Backend
from evolution_v3.contracts import make_intervention, make_registry
from detector_v2.api import _validate_frozen_stages
from detector_v2.packet import build_packet


class _FakeDetector:
    def __init__(self):
        self.calls = []

    def detect(self, record, *, case_id, interventions=None, frozen_judge=None, frozen_stages=None):
        self.calls.append({
            "case": case_id,
            "interventions": interventions,
            "frozen_judge": frozen_judge,
            "frozen_stages": frozen_stages,
        })
        judge_parsed = {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "ok",
            "correct_result": "ok",
            "basis": None,
            "confidence": "high",
            "evidence_refs": [],
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "ok"}],
        }
        return {
            "case_id": case_id,
            "state": "verified_clean",
            "decision_authority": "authoritative",
            "proof": {"judge": judge_parsed, "deployment": {}},
            "stage_status": {
                "judge": {
                    "stage": "judge", "state": "ok", "attempts": 1,
                    "view": "complete", "warnings": [],
                },
            },
            "provenance": {},
        }


def _backend():
    backend = DetectorV2Backend.__new__(DetectorV2Backend)
    backend.detector = _FakeDetector()
    backend._base_results = {}
    return backend


def _scanner_candidate():
    return make_intervention(
        intervention_id="IV-S",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report every missing required action.",
            "not_when": "Never when all required actions are present.",
        },
        expected_behavior="recall candidates cover missing required actions",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn",
        failure_cases=["A/001.json", "A/002.json"],
    )


def test_detector_v2_backend_uses_frozen_judge_for_non_judge_hook():
    backend = _backend()
    record = {"question": "q", "history": [{"role": "assistant", "content": "a"}]}
    backend.run_case(record, case_id="case", registry=None)
    backend.run_case(
        record, case_id="case", registry=make_registry([_scanner_candidate()]),
    )
    assert len(backend.detector.calls) == 2
    assert backend.detector.calls[0]["frozen_judge"] is None
    assert backend.detector.calls[1]["frozen_stages"] is not None
    assert "judge_result" in backend.detector.calls[1]["frozen_stages"]
    assert backend.detector.calls[1]["interventions"] is not None


def test_detector_v2_backend_does_not_freeze_when_judge_hook_active():
    backend = _backend()
    record = {"question": "q", "history": [{"role": "assistant", "content": "a"}]}
    backend.run_case(record, case_id="case", registry=None)
    judge_candidate = make_intervention(
        intervention_id="IV-J",
        intervention_type="text_boundary",
        hook="judge.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Re-check the final answer.",
            "not_when": "Never when the delivered result is already correct.",
        },
        expected_behavior="judge re-checks delivered result",
        target_polarity="boundary",
        cycle=1,
        failure_family="FAM-fn",
        failure_cases=["A/001.json", "A/002.json"],
    )
    backend.run_case(
        record, case_id="case", registry=make_registry([judge_candidate]),
    )
    assert backend.detector.calls[-1]["frozen_judge"] is None


def test_backend_isolates_single_case_exception(tmp_path):
    case = tmp_path / "A" / "001.json"
    case.parent.mkdir(parents=True)
    case.write_text(
        '{"question":"q","history":[],"is_hallucination":true}',
        encoding="utf-8",
    )

    class _Broken(DetectionBackend):
        def run_case(self, record, *, case_id, registry=None, gold=None):
            raise RuntimeError("boom")

    rows = _Broken().evaluate(
        ["A/001.json"], dataset_root=tmp_path,
        gold_map={"A/001.json": True},
    )
    assert rows[0]["prediction"] is None
    assert rows[0]["result"]["authority_failure_reasons"][0]["code"] == (
        "case_evaluation_exception"
    )


def test_frozen_stages_freeze_failed_provider_stages():
    """A settled failure replays instead of re-rolling provider noise."""
    baseline = {
        "proof": {"judge": {"is_hallucination": False}},
        "stage_status": {
            "judge": {"state": "ok"},
            "recall_scanner": {"state": "provider_error"},
            "causal_upgrade": {"state": "contract_error"},
            "step_tracer": {"state": "not_run"},
            "defense": {"state": "not_run"},
        },
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    assert "judge_result" in frozen
    assert frozen["recall_result"]["ok"] is False
    assert frozen["recall_status"]["state"] == "provider_error"
    assert frozen["upgrade_result"]["ok"] is False
    assert frozen["upgrade_status"]["state"] == "contract_error"
    # No hearing rows in the baseline: nothing to freeze for tracer/defense.
    assert "hearings" not in frozen


def test_frozen_stages_freeze_failed_judge_as_error():
    baseline = {
        "proof": {},
        "stage_status": {"judge": {"state": "provider_error"}},
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    assert frozen["judge_result"]["ok"] is False
    assert frozen["judge_result"]["error_type"] == "provider_error"
    assert frozen["judge_status"]["state"] == "provider_error"


def test_frozen_stages_do_not_freeze_not_needed_stages():
    baseline = {
        "proof": {"judge": {"is_hallucination": False}},
        "stage_status": {
            "judge": {"state": "ok"},
            "recall_scanner": {"state": "not_needed"},
            "causal_upgrade": {"state": "not_needed"},
        },
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    assert "judge_result" in frozen
    assert "recall_result" not in frozen
    assert "upgrade_result" not in frozen


def test_frozen_stages_freeze_single_judge_hearing_tracer_defense():
    baseline = {
        "proof": {
            "judge": {"is_hallucination": True},
            "hearings": [_hearing_row("judge")],
        },
        "stage_status": {
            "judge": {"state": "ok"},
            "step_tracer": {"state": "ok"},
            "defense": {"state": "ok"},
        },
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    entry = frozen["hearings"][0]
    assert entry["source"] == "judge"
    assert entry["tracer_result"]["ok"] is True
    assert entry["defense_result"]["ok"] is True
    assert entry["tracer_status"]["state"] == "ok"
    assert entry["defense_status"]["state"] == "ok"


def test_frozen_stage_envelope_validates_with_canonical_status_names():
    sample_record = {
        "question": "What is 2 + 2?",
        "history": [
            {"step": 1, "role": "assistant", "content": "The answer is 4."},
        ],
    }
    packet = build_packet(sample_record)
    baseline = {
        "proof": {
            "judge": {
                "is_hallucination": True,
                "hallucination_step": 1,
                "hallucination_reason": "wrong answer",
                "delivered_result": "The answer is 4.",
                "correct_result": "The answer is 5.",
                "basis": "observation",
                "confidence": "high",
                "evidence_refs": ["event_001"],
                "root_cause": {"step": 1, "quote": "4", "referent": "5"},
                "contracts": [{"kind": "exact_value", "requirement": "5"}],
            },
            "hearings": [
                {
                    "source": "judge",
                    "candidate_id": None,
                    "positive": {
                        "source": "judge", "step": 1,
                        "reason": "wrong answer",
                        "evidence_refs": ["event_001"],
                        "quote": "4",
                        "delivered_result": "The answer is 4.",
                        "correct_result": "The answer is 5.",
                    },
                    "tracer_result": {"ok": True, "parsed": {
                        "step": 1, "quote": "4", "referent": "5",
                        "evidence_refs": ["event_001"], "reason": "wrong answer",
                    }},
                    "tracer_status": {"stage": "step_tracer", "state": "ok"},
                    "defense_result": {"ok": True, "parsed": {
                        "verdict": "substantiated", "confirmed_step": 1,
                        "evidence_refs": ["event_001"], "reason": "confirmed",
                    }},
                    "defense_status": {"stage": "defense", "state": "ok"},
                },
            ],
        },
        "stage_status": {
            "judge": {"state": "ok"},
            "step_tracer": {"state": "ok"},
            "defense": {"state": "ok"},
        },
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    normalized = _validate_frozen_stages(frozen, packet)
    entry = normalized["hearings"][0]
    assert entry["tracer_status"]["state"] == "ok"
    assert entry["defense_status"]["state"] == "ok"


def _hearing_row(source, tracer_ok=True, defense_ok=True, candidate_id=None):
    tracer = {"ok": tracer_ok, "parsed": {"step": 1} if tracer_ok else None}
    tracer_status = (
        {"stage": "step_tracer", "state": "ok"}
        if tracer_ok
        else {"stage": "step_tracer", "state": "provider_error"}
    )
    defense = {"ok": defense_ok, "parsed": {"verdict": "substantiated"}}
    defense_status = (
        {"stage": "defense", "state": "ok"}
        if defense_ok
        else {"stage": "defense", "state": "contract_error"}
    )
    return {
        "source": source,
        "candidate_id": candidate_id,
        "positive": {"source": source, "step": 1},
        "tracer_result": tracer,
        "tracer_status": tracer_status,
        "defense_result": defense,
        "defense_status": defense_status,
    }


def test_frozen_stages_freeze_every_hearing_row():
    baseline = {
        "proof": {
            "judge": {"is_hallucination": True},
            "hearings": [
                _hearing_row("judge", defense_ok=False),
                _hearing_row("recall_upgrade", candidate_id="c1"),
                _hearing_row("recall_upgrade", candidate_id="c2"),
            ],
        },
        "stage_status": {"judge": {"state": "ok"}},
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    entries = frozen["hearings"]
    assert [entry["source"] for entry in entries] == [
        "judge", "recall_upgrade", "recall_upgrade",
    ]
    assert entries[0]["defense_status"]["state"] == "contract_error"
    assert entries[1]["defense_result"]["ok"] is True
    assert "tracer_result" not in frozen
    assert "defense_result" not in frozen


def test_frozen_stages_skip_hearing_stage_hook_targeted():
    baseline = {
        "proof": {
            "judge": {"is_hallucination": True},
            "hearings": [_hearing_row("judge")],
        },
        "stage_status": {"judge": {"state": "ok"}},
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"defense.pre"})
    entry = frozen["hearings"][0]
    assert "defense_result" not in entry
    assert "tracer_result" in entry


def test_frozen_stages_reject_invalid_hearing_rows():
    baseline = {
        "proof": {
            "judge": {"is_hallucination": True},
            "hearings": [
                {
                    "source": "judge",
                    "positive": {"source": "judge", "step": 1},
                    # ok=True but status says provider_error: inconsistent.
                    "tracer_result": {"ok": True, "parsed": {"step": 1}},
                    "tracer_status": {"stage": "step_tracer", "state": "provider_error"},
                    "defense_result": {"ok": True, "parsed": {"verdict": "x"}},
                    "defense_status": {"stage": "defense", "state": "ok"},
                },
            ],
        },
        "stage_status": {"judge": {"state": "ok"}},
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    assert "hearings" not in frozen


def test_run_case_fail_closes_on_empty_envelope():
    backend = _backend()
    record = {"question": "q", "history": [{"role": "assistant", "content": "a"}]}
    backend._base_results["case"] = {
        "proof": {},
        "stage_status": {"judge": {"state": "not_run"}},
    }
    try:
        backend.run_case(
            record, case_id="case", registry=make_registry([_scanner_candidate()]),
        )
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty envelope did not fail closed")


def test_frozen_stages_preserve_judge_vote():
    baseline = {
        "proof": {"judge": {"is_hallucination": False}},
        "stage_status": {"judge": {"state": "ok"}},
        "providers": {
            "judge": {"vote": {"votes": 3, "cast": 3, "positives": 0, "tie": False}},
        },
    }
    frozen = DetectorV2Backend._frozen_stages(baseline, {"judge.post"})
    assert frozen["judge_result"]["provider"]["vote"]["cast"] == 3
    assert frozen["judge_result"]["provider"]["vote"]["positives"] == 0
