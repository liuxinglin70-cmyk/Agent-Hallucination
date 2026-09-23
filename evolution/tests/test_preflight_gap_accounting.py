"""The pre-flight stop keeps the round's H_t bookkeeping.

``_run_cycle`` returns from ``_stop_preflight`` before the tail that calls
``record_capability_gaps`` and ``record_route_failures``. A round that found a
capability-gap family at the same time as an actionable-but-unmeasurable one
reported the gap in its worksheet and then dropped it: the report had no
``capability_backlog``, ``gaps.jsonl`` was never written, and the next round had
to rediscover the same missing capability.

The audit's reproduction is a hand-shaped backend: FN failures in the patrol
pool (an actionable family with no gain opportunity) beside a mechanical-FP
family (a capability gap). The gain set is left untouched, so the round blocks
at the evaluation pre-flight.
"""

from __future__ import annotations

from evolution.backends import DetectionBackend
from evolution.config import load_config
from evolution.engine import EvolutionEngine
from evolution.gaps import open_gaps
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit


def _dataset(root, n=20):
    import json

    cases = []
    for index in range(n):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = root / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "task",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1,
            "true_answer": "gold",
        }), encoding="utf-8")
    return cases


class _Shaped(DetectionBackend):
    """FN failures on patrol; a mechanical-FP family the vocabulary cannot treat."""

    name = "shaped"

    def __init__(self, fn_cases, fp_cases):
        self.fn_cases = set(fn_cases)
        self.fp_cases = set(fp_cases)

    def run_case(self, record, *, case_id, registry=None, gold=None):
        prediction = gold
        source = "none"
        if case_id in self.fn_cases:
            prediction = False
        elif case_id in self.fp_cases:
            prediction = True
            source = "mechanical_violation"
        state = (
            "verified_error" if prediction is True
            else "verified_clean" if prediction is False
            else "unresolved"
        )
        return {
            "schema_version": "detector.detection_result.v1",
            "case_id": case_id,
            "state": state,
            "decision_authority": "authoritative",
            "runtime_valid": True,
            "responsible_step": 1,
            "occurrence_state": state,
            "stage_status": {k: {"state": "ok"} for k in (
                "judge", "recall_scanner", "causal_upgrade",
                "step_tracer", "defense",
            )},
            "proof": {
                "state": state,
                "judge": {"is_hallucination": gold is True,
                          "confidence": "high", "contracts": []},
                "defense": {"verdict": "none"},
                "recall_candidates": [{"candidate_id": "c", "step": 1}],
                "terminal_basis": {"decisive_facts": {
                    "positive_source": source if prediction is True else "none",
                }},
            },
        }


class _Silent:
    name = "silent"

    def propose(self, families, *, cycle, max_proposals=3):
        return ProposalBatch(candidates=[], joint_groups=[], notes=[])


def _blocked_round(tmp_path):
    cases = _dataset(tmp_path / "data")
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["harness_dir"] = str(tmp_path / "harness")
    config["memory_root"] = str(tmp_path / "memory")
    config["backend"]["cache"] = False
    config["audit"]["enabled"] = False
    config["budget"]["patrol_cases"] = 8
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    split.gold = {
        case: case.endswith(("0.json", "2.json", "4.json", "6.json", "8.json"))
        for case in cases
    }
    backend = _Shaped(
        fn_cases={"A/012.json", "A/016.json"},
        fp_cases={"A/013.json", "A/015.json"},
    )
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True, proposer=_Silent(),
    )
    return engine, engine.run_cycle(1)


def test_preflight_stop_still_records_the_capability_backlog(tmp_path):
    """A blocked round must not lose the gap its own patrol found."""
    engine, report = _blocked_round(tmp_path)
    assert report["state"] == "blocked_evaluation_preflight"
    # The patrol really found a capability gap, or the test proves nothing.
    gaps = report["patrol"]["capability_gaps"]
    assert gaps, report["patrol"]

    assert "capability_backlog" in report, sorted(report)
    backlog = report["capability_backlog"]["open"]
    families = [item["failure_family"] for item in backlog]
    assert str(gaps[0]["failure_family"]) in families
    # And it is on disk, not only in the report. The round's planner may add
    # backlog items of its own for the blocked family (its route readings), so
    # membership is what this asserts, not being the only entry.
    assert engine.gap_store.exists()
    stored = [item["failure_family"] for item in open_gaps(engine.gap_store)]
    assert str(gaps[0]["failure_family"]) in stored
    assert set(families) <= set(stored)


def test_preflight_stop_report_matches_its_saved_file(tmp_path):
    """The report the caller gets is the report on disk.

    ``_stop_preflight`` saves before the backlog exists; the accounting is
    written into the same dict afterwards, so the file has to be re-saved or a
    reader of ``cycles/cycle001.json`` sees a different record from the caller.
    """
    import json

    engine, report = _blocked_round(tmp_path)
    saved = json.loads(
        (engine.paths["cycles"] / "cycle001.json").read_text(encoding="utf-8")
    )
    assert "capability_backlog" in saved
    assert saved["capability_backlog"] == report["capability_backlog"]


def test_preflight_stop_records_the_route_failure_attempt(tmp_path):
    """The empty route-failure call is still a recorded attempt."""
    engine, report = _blocked_round(tmp_path)
    assert "route_failures" in report
    assert report["route_failures"]["observed"] == 0
    # The ledger says the round asked, so "nothing failed" is distinguishable
    # from "this path never ran".
    events = [record["event"] for record in engine.ledger.read()]
    assert events.count("capability_gaps") == 1
