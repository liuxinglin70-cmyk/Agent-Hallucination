"""Instrumentation experiment for the cycle-side development stage.

    python reports/cycle_development_wiring_20260923/run_experiment.py

What this measures, and what it does not:

The work package wires ``development.enabled`` into the cycle, so a round can
develop its candidates through L0/L1 before they reach the four gates. The
claim to test is *instrumental*: the stage must change which candidates reach
the gates, and must not change anything else. So this experiment runs the same
planted-failure scenario twice -- loop off and loop on -- and reads what each
round did.

The candidate in the "bad" scenario is deliberately one that fires but repairs
nothing: exactly the shape the first real cycle carried into 62 minutes of
paired promotion and had rejected. That is the cost the stage exists to avoid,
and the reading that matters is whether the gates ever see it.

Every number here comes from the mock backend and a scripted proposer, so this
is evidence about the framework's mechanics and nothing about detection
quality: the mock oracle is what makes any target reachable at all. The report
marks its own evidence grade accordingly.

Output: ``experiment_report.json`` beside this script. Zero provider calls.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
from typing import Any, Mapping

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.backends import MockDetectorBackend  # noqa: E402
from evolution.config import load_config  # noqa: E402
from evolution.contracts import make_intervention  # noqa: E402
from evolution.engine import EvolutionEngine  # noqa: E402
from evolution.proposer import ProposalBatch  # noqa: E402
from evolution.splits import EvolutionSplit  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "experiment_report.json"


# ----------------------------------------------------------------------
# The synthetic workspace, planted the way ``evolution smoke`` plants it
# ----------------------------------------------------------------------

def workspace(root: pathlib.Path, n: int = 24) -> tuple[dict, EvolutionSplit, list[str]]:
    data = root / "data"
    cases = []
    for index in range(n):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"task {index}",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
            "true_answer": "gold",
        }), encoding="utf-8")
    config = load_config()
    config.update({
        "dataset_root": str(data),
        "split_manifest": str(root / "manifest.json"),
        "mini_split_manifest": str(root / "mini_manifest.json"),
        "work_root": str(root / "work"),
        "registry_dir": str(root / "registry"),
        "ledger_path": str(root / "ledger.jsonl"),
        "harness_dir": str(root / "harness"),
        "memory_root": str(root / "memory"),
    })
    config["backend"].update({"name": "mock", "cache": False})
    config["proposer"]["mode"] = "offline"
    config["budget"].update({"patrol_cases": 8, "stable_failure_rerun_cases": 4})
    config["audit"]["enabled"] = False
    config["archive"] = {"on": "cycle_end", "root": str(root / "candidates")}
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:6], gain=cases[6:14],
        patrol_pool=cases[14:], mini=True,
    )
    return config, split, cases


class ScriptedProposer:
    """A proposer that returns one fixed candidate and counts its calls.

    The call count is the reading that separates the paths: a single-shot round
    asks once and hands the draft to the gates; a development round asks once
    per version and hands over only what survived its replay.
    """

    name = "scripted"

    def __init__(self, candidate: Mapping[str, Any]) -> None:
        self.candidate = dict(candidate)
        self.calls: list[dict[str, Any]] = []
        self.rejection_feedback: list[dict[str, Any]] = []

    def set_rejection_feedback(self, feedback) -> None:
        self.rejection_feedback = [dict(item) for item in feedback or ()]

    def propose(self, families, *, cycle, max_proposals=3) -> ProposalBatch:
        self.calls.append({
            "cycle": int(cycle),
            "families": [str(f.get("failure_family") or "") for f in families],
            "feedback_entries": len(self.rejection_feedback),
        })
        if not families:
            return ProposalBatch()
        bound = dict(self.candidate)
        bound["provenance"] = {
            "cycle": int(cycle),
            "failure_family": str(families[0]["failure_family"]),
            "failure_cases": list(families[0]["failure_cases"]),
        }
        bound.pop("content_hash", None)
        return ProposalBatch(
            candidates=[bound],
            joint_groups=[],
            notes=[{
                "intervention_id": str(bound.get("intervention_id") or ""),
                "generator": "scripted",
                "provider_called": False,
            }],
        )


def recall_candidate(cycle: int = 1) -> dict:
    return make_intervention(
        intervention_id=f"IV-{cycle}-001",
        intervention_type="text_boundary",
        hook="scanner.system",
        packet_features=["has_tool_calls"],
        body={
            "directive": "Report missing required actions.",
            "not_when": "Never when all required actions are present.",
        },
        expected_behavior="recall gain on the planted misses",
        target_polarity="recall",
        cycle=cycle,
        failure_family="FAM-fn",
        failure_cases=["A/000.json", "A/001.json"],
    )


# ----------------------------------------------------------------------
# One round, read the way a researcher reads it
# ----------------------------------------------------------------------

def measure(
    root: pathlib.Path,
    *,
    enabled: bool,
    repairs_target: bool,
) -> dict[str, Any]:
    """Run one planted-failure cycle and report what it did.

    ``repairs_target`` decides whether the scripted candidate actually repairs
    the patrol misses it was developed against. When it does not, L1 can only
    find "no target case improved" -- which is the falsification this
    experiment is about.
    """
    config, split, cases = workspace(root)
    config["development"]["enabled"] = enabled
    config["development"]["max_versions_per_mechanism"] = 2
    # One replay costs two arms over target plus neighbour cases; the default
    # cap of 24 would stop the loop before it measured a single version.
    config["development"]["max_l1_cases"] = 128
    config["budget"]["max_proposals"] = 3

    gold = {case: bool(int(case.split("/")[1][:3]) % 2 == 0) for case in cases}
    # Two gold-positive patrol cases the bare base misses: the mechanism.
    patrol_true = [case for case in split.patrol_pool if gold[case]][:2]
    # Two gold-positive gain cases a repair would recover.
    gain_true = [case for case in split.gain if gold[case]][:2]
    base = {case: False for case in patrol_true + gain_true}

    identifier = "IV-1-D001" if enabled else "IV-1-001"
    overrides: dict[str, dict[str, bool]] = {}
    if repairs_target:
        overrides[identifier] = {case: True for case in [*patrol_true, *gain_true]}

    proposer = ScriptedProposer(recall_candidate())
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(base_predictions=base, overrides=overrides),
        split=split,
        mini=True,
        proposer=proposer,
    )
    started = time.monotonic()
    report = engine.run_cycle(1)
    elapsed = time.monotonic() - started

    development = report.get("development") or {}
    run = (development.get("runs") or [{}])[0]
    effect = report.get("active_effect_on_g") or {}
    return {
        "seconds": round(elapsed, 2),
        "state": report.get("state"),
        "round_path": report.get("round_path"),
        "proposer_calls": len(proposer.calls),
        "proposer_call_feedback": [
            int(call["feedback_entries"]) for call in proposer.calls
        ],
        "development_targets": development.get("targets"),
        "development_skipped": development.get("skipped"),
        "development_candidate_count": development.get("candidate_count"),
        "development_status": run.get("status"),
        "development_selected": run.get("selected_version_id"),
        "development_versions": [
            {
                "version_id": item.get("version_id"),
                "parent_version_id": item.get("parent_version_id"),
                "status": item.get("status"),
                "l0_passed": item.get("l0_passed"),
                "l1_passed": item.get("l1_passed"),
                "l1_reasons": list(item.get("l1_reasons") or ()),
            }
            for item in run.get("versions") or ()
        ],
        "development_budget": (run.get("budget") or {}).get("used"),
        "development_l1_cases_available": (run.get("budget") or {}).get(
            "caps", {}
        ).get("max_l1_cases"),
        # The readings that say whether the stage stayed inside its own fence.
        "proposals_reaching_the_gates": len(report.get("proposals") or ()),
        "gate_entries": len(report.get("gates") or ()),
        "admitted": [
            str(item) for item in
            (report.get("marginal_selection") or {}).get("selected") or ()
        ],
        "active_after_cycle": [
            str(item) for item in report.get("active_after_cycle") or ()
        ],
        "effect_on_gain_set": {
            key: effect.get(key)
            for key in (
                "paired_n", "base_all_case_correct", "active_all_case_correct",
                "marginal_cases", "new_errors", "unresolved_added",
            )
        },
        "development_ledger_events": [
            record["event"] for record in engine.ledger.read()
        ],
    }


def main() -> int:
    report: dict[str, Any] = {
        "schema_version": "evolution.cycle_development_experiment.v1",
        "evidence_grade": (
            "instrumentation only: mock backend and scripted proposer "
            "throughout, zero provider calls. A mock oracle is what makes a "
            "target reachable, so nothing here is a statement about detection "
            "or localization quality."
        ),
        "scenarios": {},
    }
    with tempfile.TemporaryDirectory(prefix="cycle_development_") as temporary:
        root = pathlib.Path(temporary)
        # A: the loop is off. The draft goes straight to the gates.
        report["scenarios"]["loop_off"] = measure(
            root / "off", enabled=False, repairs_target=False,
        )
        # B: the loop is on, and the draft repairs nothing. L1 must falsify it
        #    before any gate measures it.
        report["scenarios"]["loop_on_unrepaired"] = measure(
            root / "on_bad", enabled=True, repairs_target=False,
        )
        # C: the loop is on, and the draft repairs the target. It must reach
        #    the gates and be admitted, exactly as it was with the loop off.
        report["scenarios"]["loop_on_repaired"] = measure(
            root / "on_good", enabled=True, repairs_target=True,
        )
        # D: the loop is off, and the draft repairs the target. This is the
        #    control for C: the same candidate, the same gates, the old path.
        report["scenarios"]["loop_off_repaired"] = measure(
            root / "off_good", enabled=False, repairs_target=True,
        )

    scenarios = report["scenarios"]
    off, bad, good, off_good = (
        scenarios["loop_off"], scenarios["loop_on_unrepaired"],
        scenarios["loop_on_repaired"], scenarios["loop_off_repaired"],
    )
    # The three claims, stated as readings rather than adjectives.
    claims = {
        "loop_off_is_still_single_shot": (
            off["round_path"] == "single_shot"
            and off["gate_entries"] >= 1
            and off["proposer_calls"] == 1
        ),
        "loop_off_writes_no_development_record": (
            off["development_targets"] is None
            and "development" not in off["development_ledger_events"]
        ),
        "loop_on_falsifies_before_the_gates": (
            bad["round_path"] == "development"
            and bad["development_candidate_count"] == 0
            and bad["proposals_reaching_the_gates"] == 0
            and bad["gate_entries"] == 0
            and bad["development_versions"]
            and bad["development_versions"][-1]["l1_passed"] is False
        ),
        "loop_on_does_not_redraft_behind_the_loop": (
            bad["proposer_calls"] == len(bad["development_versions"])
        ),
        "loop_on_carries_a_passing_version_to_the_gates": (
            good["round_path"] == "development"
            and good["development_candidate_count"] == 1
            and good["proposals_reaching_the_gates"] == 1
            and good["proposer_calls"] == 1
        ),
        # The candidate's *effect* must be identical; its identifier is
        # expected to differ, because the development drafter assigns it
        # (``IV-1-D001``) so two mechanisms developed in one cycle cannot both
        # arrive as ``IV-<cycle>-001``. Comparing ids would have made this
        # claim fail for a deliberate difference and told us nothing about
        # whether the stage changed the outcome.
        "the_passing_version_is_admitted_exactly_as_before": (
            len(good["admitted"]) == len(off_good["admitted"]) == 1
            and good["effect_on_gain_set"] == off_good["effect_on_gain_set"]
            and good["effect_on_gain_set"]["marginal_cases"] == 2
            and good["effect_on_gain_set"]["new_errors"] == 0
        ),
        "development_measures_against_a_baseline_arm": (
            bad["development_l1_cases_available"] >= 1
            and (bad["development_budget"] or {}).get("l1_cases", 0) > 0
        ),
    }
    report["claims"] = claims
    report["passed"] = all(claims.values())
    OUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    print(json.dumps({"passed": report["passed"], **claims}, indent=2))
    print(f"\nreport -> {OUT}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
