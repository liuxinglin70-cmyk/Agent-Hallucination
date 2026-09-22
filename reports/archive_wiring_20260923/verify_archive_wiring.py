#!/usr/bin/env python
"""Re-run the §28.6 archive-wiring readings. Read-only except a temp store.

    python reports/archive_wiring_20260923/verify_archive_wiring.py

Prints:

1. the declared policy and its default;
2. the refusal a run gets when the policy is ``off``;
3. an archived development run: candidates (pass and fail) and the components
   the passing version contributed;
4. the retrieval round trip -- a part a run produced, found by the next round;
5. that neither store can reach the Active Registry.

The workspace is a fresh temporary directory and the proposer is forced
offline, so this script makes no provider call and touches no real registry.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.archive import (  # noqa: E402
    archive_report,
    candidates_for_mechanism,
    load_candidates,
    load_components,
    retrieve_components,
)
from evolution.backends import MockDetectorBackend  # noqa: E402
from evolution.config import load_config  # noqa: E402
from evolution.contracts import make_intervention  # noqa: E402
from evolution.development import (  # noqa: E402
    CandidateDeveloper,
    DevelopmentBudget,
)
from evolution.engine import EvolutionEngine  # noqa: E402
from evolution.splits import EvolutionSplit  # noqa: E402

PROGRAM = {
    "schema_version": "detector.predicate_program.v1",
    "name": "evo_delivery_probe",
    "description": "Violated when the trajectory carries prose but no tool call.",
    "verdicts": ["satisfied", "violated", "not_applicable"],
    "conviction_verdicts": ["violated"],
    "rules": [
        {
            "conditions": [
                {"field": "tool_call_count", "operator": "equals", "value": 0},
            ],
            "verdict": "violated",
            "detail": "no tool call in the trajectory",
        }
    ],
    "default_verdict": "not_applicable",
    "default_detail": "no delivery probe matched",
}


def workspace(root: pathlib.Path, n: int = 16):
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
    # Forced offline: patrol drafts a proposal, and the default mode is ``llm``.
    config["proposer"]["mode"] = "offline"
    config["budget"].update({"patrol_cases": 8, "stable_failure_rerun_cases": 4})
    config["audit"]["enabled"] = False
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    return config, split, cases


def candidate(family_id: str, cases: list[str], identifier: str) -> dict:
    return make_intervention(
        intervention_id=identifier,
        intervention_type="recall_trigger",
        hook="recall.trigger",
        packet_features=["has_tool_calls"],
        body={
            "predicate": PROGRAM["name"],
            "runner": "typed_dsl",
            "output_verdicts": list(PROGRAM["verdicts"]),
            "predicate_definition": PROGRAM,
            "trigger_verdicts": ["violated"],
        },
        expected_behavior="recall opens when the trajectory carries no tool call",
        target_polarity="recall",
        cycle=1,
        failure_family=family_id,
        failure_cases=cases,
    )


def develop(base: pathlib.Path, *, repairing: bool):
    label = "pass" if repairing else "fail"
    root = base / label
    root.mkdir(parents=True, exist_ok=True)
    config, split, cases = workspace(root)
    # The policy this script is demonstrating. Each run gets its own store root
    # so "what this run archived" cannot be satisfied by a sibling's writes.
    config["archive"] = {
        "on": "development_end",
        "root": str(root / "candidates"),
    }
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
    output = root / "development"
    developer = CandidateDeveloper(
        output_dir=output,
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
    drafts = [candidate(str(family["failure_family"]), mechanism_cases, "IV-1-001")]
    result = developer.develop(
        lambda *a, **k: [drafts.pop(0)] if drafts else [], cycle=1,
    )
    if result.get("selected_version_id"):
        developer.freeze(str(result["selected_version_id"]))
    outcome = engine.archive_development_run(
        output,
        cycle=1,
        mechanism_id=str(mechanism["mechanism_id"]),
        family_id=str(family["failure_family"]),
        mechanism=mechanism,
    )
    return {
        "label": label,
        "engine": engine,
        "mechanism": mechanism,
        "result": result,
        "outcome": outcome,
        "root": engine.archive_root,
    }


def main() -> int:
    ok = True

    print("=== 1. the declared policy ===")
    default = load_config()
    print(f"  default archive.on: {default['archive']['on']!r}")
    print(f"  default archive.root: {default['archive']['root']!r}")
    print(f"  choices: off | development_end | cycle_end")

    with tempfile.TemporaryDirectory(prefix="archive_wiring_") as temporary:
        base = pathlib.Path(temporary)

        print("\n=== 2. with the policy off, a run writes nothing ===")
        off = develop(base / "off_root", repairing=True)
        refused = off["engine"].archive_development_run(
            base / "off_root" / "pass" / "development", cycle=1,
            force=False,
        ) if off["engine"].archive_on == "off" else None
        if refused is None:
            off["engine"].archive_on = "off"
            refused = off["engine"].archive_development_run(
                base / "off_root" / "pass" / "development", cycle=1,
            )
        print(f"  archived={refused['archived']} policy={refused['policy']!r}")
        print(f"  reason: {refused['reason']}")
        ok = ok and refused["archived"] is False

        print("\n=== 3. a passing run: candidates and components ===")
        passing = develop(base / "on_root", repairing=True)
        print(f"  L1 selected: {passing['result'].get('selected_version_id')!r}")
        print(f"  archived: {passing['outcome']['archived']}")
        candidates = load_candidates(passing["root"])
        print(f"  candidates: {len(candidates)}")
        for row in candidates:
            print(
                f"    {row['version_id']} status={row['status']!r} "
                f"mechanism={row['mechanism_id']} "
                f"reason={str(row['failure_reason'])[:60]!r}"
            )
        components = load_components(passing["root"])
        print(f"  components: {len(components)}")
        for row in components:
            print(
                f"    {row['name']} kind={row['kind']} "
                f"capability={row['capability']!r} digest={str(row['digest'])[:16]}"
            )
        ok = ok and candidates and components

        print("\n=== 4. retrieval round trip ===")
        mechanism_id = str(passing["mechanism"]["mechanism_id"])
        recalled = candidates_for_mechanism(passing["root"], mechanism_id)
        print(f"  candidates_for_mechanism -> {len(recalled)}")
        capability = str(components[0]["capability"]) if components else ""
        found = retrieve_components(
            passing["root"], mechanism_id=mechanism_id,
            capability=capability, limit=5,
        )
        print(f"  retrieve_components(capability={capability!r}) -> "
              f"{[r['name'] for r in found['components']]}")
        ok = ok and recalled and found["components"]

        print("\n=== 5. a failing run: archived, with no component ===")
        failing = develop(base / "fail_root", repairing=False)
        failing_candidates = load_candidates(failing["root"])
        print(f"  L1 selected: {failing['result'].get('selected_version_id')!r}")
        print(f"  candidates: {len(failing_candidates)} "
              f"statuses={[r['status'] for r in failing_candidates]}")
        print(f"  components: {len(load_components(failing['root']))} "
              "(a version that failed L1 is not shown useful)")
        ok = ok and failing_candidates and not load_components(failing["root"])

        print("\n=== 6. no path from the stores to the registry ===")
        report = archive_report(passing["root"])
        print(f"  metric_role: {report['metric_role']!r}")
        print(f"  candidates.total={report['candidates']['total']} "
              f"components.total={report['components']['total']}")
        from evolution import archive

        source = pathlib.Path(archive.__file__).read_text(encoding="utf-8")
        reaches = "ActiveRegistry" in source or "from evolution.registry" in source
        print(f"  archive module reaches the registry: {reaches}")
        ok = ok and not reaches

    print(f"\nverdict: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
