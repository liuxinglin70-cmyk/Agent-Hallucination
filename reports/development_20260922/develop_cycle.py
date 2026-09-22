"""P0.3 dry-run: the deployment-side development loop, offline.

Shows the whole loop end to end with no provider call: patrol finds a mechanism,
the capability planner says which route can act on it, the hook dependency map
says what a targeted replay has to re-run, and the developer drafts, checks L0,
measures L1, and freezes.

Two runs are printed, and the difference between them is the point:

* with a mock that does not repair anything, L1 fails and nothing is frozen;
* with a mock that repairs the target, L1 passes and a version is frozen.

A candidate is only ever frozen through L1: that refusal is what keeps a
candidate that broke a neighbour out of a formal promotion protocol.

Run from the repository root:

    python reports/development_20260922/develop_cycle.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.backends import (  # noqa: E402
    MockDetectorBackend,
    partial_replay_plan,
)
from evolution.config import load_config  # noqa: E402
from evolution.contracts import make_intervention  # noqa: E402
from evolution.development import (  # noqa: E402
    CandidateDeveloper,
    DevelopmentBudget,
    development_report,
)
from evolution.engine import EvolutionEngine  # noqa: E402
from evolution.splits import EvolutionSplit  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "development_run.json"


def build_workspace(root: pathlib.Path, n: int = 20):
    data = root / "data"
    cases = []
    for index in range(n):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"task {index}",
            "history": [
                {"step": 1, "role": "assistant", "content": "answer"},
            ],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1 if index % 2 == 0 else None,
            "true_answer": "gold",
        }), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"b_stage": {"evolution_set": {
            "size": len(cases), "cases": cases,
        }}}),
        encoding="utf-8",
    )
    config = load_config()
    config.update({
        "dataset_root": str(data),
        "split_manifest": str(root / "manifest.json"),
        "mini_split_manifest": str(root / "manifest.json"),
        "work_root": str(root / "work"),
        "registry_dir": str(root / "registry"),
        "ledger_path": str(root / "ledger.jsonl"),
        "harness_dir": str(root / "harness"),
        "memory_root": str(root / "memory"),
    })
    config["backend"].update({"name": "mock", "cache": False})
    config["budget"].update({"patrol_cases": 8})
    config["audit"]["enabled"] = False
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    return config, split, cases


def build_candidate(family, mechanism, cases):
    return make_intervention(
        intervention_id="IV-1-001",
        intervention_type="recall_trigger",
        hook="recall.trigger",
        packet_features=["has_tool_calls"],
        body={
            "predicate": "required_action_present",
            "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
            "trigger_verdicts": ["violated"],
        },
        expected_behavior="recall net opens for unmet required actions",
        target_polarity="recall",
        cycle=1,
        failure_family=str(family["failure_family"]),
        failure_cases=list(cases),
    )


def run_case(*, repairing: bool, root: pathlib.Path) -> dict:
    config, split, cases = build_workspace(root)
    targets = [
        case for case in [*split.gain, *split.patrol_pool]
        if int(case.split("/")[1][:3]) % 2 == 0
    ]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in targets},
        overrides=(
            {"IV-1-001": {case: True for case in targets}} if repairing else {}
        ),
    )
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True)
    preview = engine.preview_cycle(1)
    family = (preview.get("families") or [])[0]
    mechanism = family["mechanism_detail"]
    requirement = next(
        item for item in (
            (preview.get("capability_plan") or {}).get("requirements") or ()
        )
        if item["mechanism_id"] == mechanism["mechanism_id"]
    )
    mechanism_cases = [str(case) for case in family["failure_cases"]]
    candidate = build_candidate(family, mechanism, mechanism_cases)
    replay = partial_replay_plan(
        [candidate], active_entries=engine.registry.entries,
    )
    developer = CandidateDeveloper(
        output_dir=root / "development",
        budget=DevelopmentBudget(max_versions_per_mechanism=3),
        mechanism=mechanism,
        capability_plan=requirement,
        target_cases=mechanism_cases,
        neighbor_cases=[],
        active_entries=engine.registry.entries,
        target_kind=str(family.get("target_kind") or "detection"),
        l1_runner=lambda cs, *, candidate, plan: engine.develop_rows(
            cs, candidate=candidate, split=split,
        ),
    )
    drafts = [candidate]
    result = developer.develop(
        lambda *a, **k: [drafts.pop(0)] if drafts else [], cycle=1,
    )
    frozen = (
        developer.freeze(result["selected_version_id"])
        if result["selected_version_id"] else None
    )
    return {
        "repairing_mock": repairing,
        "mechanism_id": mechanism["mechanism_id"],
        "failure_stage": mechanism["detector_failure"]["stage"],
        "repair_operation": mechanism["repair_hypothesis"]["operation"],
        "capability": {
            "classification": requirement["classification"],
            "sufficient_routes": requirement["sufficient_routes"],
        },
        "partial_replay": {
            "hooks": replay["hooks"],
            "rerun_stages": replay["rerun_stages"],
            "fresh_required": replay["fresh_required"],
        },
        "development": development_report(result),
        "frozen": (
            {
                "version_id": frozen["version_id"],
                "mechanism_id": frozen["mechanism_id"],
                "route": frozen["route"],
                "lineage": frozen["lineage"],
            }
            if frozen else None
        ),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p03_development_") as temporary:
        base = pathlib.Path(temporary)
        not_repairing = run_case(repairing=False, root=base / "a")
        repairing = run_case(repairing=True, root=base / "b")
    payload = {
        "schema_version": "evolution.development_dryrun.v1",
        "evidence_grade": "diagnostic-only",
        "note": (
            "Deployment-side development loop over a synthetic workspace with a "
            "mock backend. No provider call, no admission, no promotion. The "
            "mock oracle is what makes the target reachable, so this run "
            "demonstrates the loop's mechanics, not detector quality."
        ),
        "not_repairing": not_repairing,
        "repairing": repairing,
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "out": OUT.relative_to(REPO).as_posix(),
        "not_repairing": {
            "status": not_repairing["development"]["status"],
            "version_status": [
                item["status"] for item in not_repairing["development"]["versions"]
            ],
            "frozen": not_repairing["frozen"],
        },
        "repairing": {
            "status": repairing["development"]["status"],
            "version_status": [
                item["status"] for item in repairing["development"]["versions"]
            ],
            "frozen": repairing["frozen"],
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
