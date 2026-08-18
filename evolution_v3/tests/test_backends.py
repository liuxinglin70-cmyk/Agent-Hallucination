from __future__ import annotations

from evolution_v3.backends import DetectionBackend, DetectorV2Backend
from evolution_v3.contracts import make_intervention, make_registry


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
