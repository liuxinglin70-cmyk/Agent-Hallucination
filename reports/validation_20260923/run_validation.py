"""Validation experiment for this round's work packages. Offline, zero provider.

    python reports/validation_20260923/run_validation.py

What this measures, and what it does not:

The three new capabilities this round are (P2.3) a metamorphic admission gate
for runners, (P2.2) a Level B sandbox runner, and (§28.6) run-path writes to the
candidate archive and component store. Each one can be unit-tested and still not
be *reachable* from a run -- that failure happened three times in the earlier
work packages -- so this experiment exercises each through the path a real run
takes and records the reading.

It uses the mock backend and the offline proposer throughout. Every number below
is therefore evidence about the framework's mechanics, not about detection
quality: a mock oracle is what makes a target reachable at all. The report marks
its own evidence grade accordingly.

Output: ``validation_report.json`` beside this script.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
from typing import Mapping

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from detector.interventions import (  # noqa: E402
    COMPONENT_RUNNER,
    RUNNER_REGISTRY,
)
from detector.packet import build_packet  # noqa: E402
from evolution.archive import (  # noqa: E402
    archive_report,
    load_candidates,
    load_components,
)
from evolution.backends import (  # noqa: E402
    MockDetectorBackend,
    _compile_with_component_executors,
    _registry_component_executors,
)
from evolution.capability_surface import capability_surface_report  # noqa: E402
from evolution.config import load_config  # noqa: E402
from evolution.contracts import make_intervention, make_registry  # noqa: E402
from evolution.development import (  # noqa: E402
    CandidateDeveloper,
    DevelopmentBudget,
    check_l0,
)
from evolution.engine import EvolutionEngine  # noqa: E402
from evolution.mechanisms import infer_mechanism  # noqa: E402
from evolution.metamorphic import (  # noqa: E402
    CompositeSubject,
    MechanismSubject,
    PacketRuleSubject,
    metamorphic_report,
    run_gate,
)
from evolution.sandbox import (  # noqa: E402
    admission_blockers,
    make_component_executor,
    reference_component,
    run_forbidden_sources,
    sandbox_capabilities,
    validate_component,
)
from evolution.splits import EvolutionSplit  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "validation_report.json"

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
    config["proposer"]["mode"] = "offline"
    config["budget"].update({"patrol_cases": 8, "stable_failure_rerun_cases": 4})
    config["audit"]["enabled"] = False
    config["archive"] = {"on": "development_end", "root": str(root / "candidates")}
    # The cycle/experiment commands build their own split from the mini
    # manifest, so the pool has to be large enough for ``mini_pool_size``.
    # Sizing it here keeps the CLI path on the same synthetic workspace the
    # other measurements use, instead of a real dataset.
    config["split"]["mini_pool_size"] = min(16, n)
    config["split"]["mini_gain_size"] = 8
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    return config, split, cases


# ----------------------------------------------------------------------
# 1. The offline cycle and multi-cycle experiment
# ----------------------------------------------------------------------

def run_cli(arguments: list[str]) -> int:
    """Invoke the evolution CLI in-process, with explicit argv.

    Parsed rather than reloaded: ``evolution.cli.main`` reads ``sys.argv``, and
    a subprocess would rebuild the workspace it is handed a config for.
    """
    from evolution.cli import build_parser

    args = build_parser().parse_args(arguments)
    return int(args.func(args))


def measure_cycle(root: pathlib.Path) -> dict:
    """One full offline cycle that actually admits: patrol -> gates -> registry.

    A cycle over an unplanted mock finds no failure family, admits nothing, and
    would say nothing about the admission path. The failures are therefore
    planted the way ``evolution smoke`` plants them: two gold-positive patrol
    cases the bare base gets wrong, and a scripted candidate that repairs two
    gold-positive gain cases. What that demonstrates is the gate chain and the
    registry write, not detection quality -- the oracle is the mock.
    """
    from evolution.backends import make_backend
    from evolution.cli import _smoke_proposer

    config, split, cases = workspace(root)
    config["budget"].update({
        "patrol_cases": len(split.patrol_pool),
        "max_proposals": 3,
        "max_admits": 2,
    })
    gold = {
        case: bool(int(case.split("/")[1][:3]) % 2 == 0) for case in cases
    }
    patrol_true = [c for c in split.patrol_pool if gold[c]][:2]
    gain_true = [c for c in split.gain if gold[c]][:2]
    base = {case: False for case in patrol_true + gain_true}
    identifier = "IV-VALID-001"
    overrides = {identifier: {case: True for case in gain_true}}
    backend = make_backend(
        config, mode="mock", mock_base=base, mock_overrides=overrides,
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=_smoke_proposer(identifier, patrol_true),
    )
    started = time.monotonic()
    report = engine.run_cycle(1)
    elapsed = time.monotonic() - started
    gates = {}
    for item in report.get("gates") or ():
        identifier = str(item.get("intervention_id") or "")
        steps = item.get("gates")
        if isinstance(steps, Mapping):
            gates[identifier] = {
                str(key): bool((step or {}).get("passed"))
                for key, step in steps.items()
            }
        else:
            gates[identifier] = {
                str(key): bool(value) for key, value in item.items()
                if key not in {"intervention_id", "candidate", "reason"}
            }
    effect = report.get("active_effect_on_g") or {}
    return {
        "seconds": round(elapsed, 2),
        "state": report.get("state"),
        "patrol_families": len((report.get("patrol") or {}).get("families") or ()),
        "proposed": len(report.get("proposals") or ()),
        "admitted": [
            str(item) for item in
            (report.get("marginal_selection") or {}).get("selected") or ()
        ],
        "gates": gates,
        "gate_reason": {
            str(item.get("intervention_id") or ""): str(item.get("reason") or "")
            for item in report.get("gates") or ()
        },
        "active_after_cycle": [
            str(item) for item in report.get("active_after_cycle") or ()
        ],
        "effect_on_gain_set": {
            key: effect.get(key)
            for key in (
                "paired_n", "base_all_case_correct", "active_all_case_correct",
                "marginal_cases", "new_fn", "new_fp", "new_errors",
                "unresolved_added", "health_regressed",
            )
        },
        "audit_passed": bool((report.get("audit") or {}).get("passed")),
        "ledger_valid": bool((report.get("ledger_integrity") or {}).get("valid")),
        "registry_entries_after": len(engine.registry.entries),
        "registry_ids_after": [
            str(entry.get("intervention_id") or "")
            for entry in engine.registry.entries
        ],
    }


def measure_experiment(root: pathlib.Path, cycles: int = 2) -> dict:
    """The multi-round offline experiment, with the gates actually exercised.

    The CLI's own experiment path builds a split from the mini manifest with no
    planted failures, so it finds no family and admits nothing -- a reading that
    looks like a passing run and says nothing. It is driven here through
    ``run_experiment`` with the same scripted proposer the cycle measurement
    uses, so each round can be asked what it admitted and what the next round
    started from.

    ``active_ids`` after each round is the reading that matters for §17's
    inheritance question, and it is reported per cycle rather than only at the
    end: "the registry is non-empty at the end" does not say when an entry
    arrived.
    """
    from evolution.backends import make_backend
    from evolution.cli import _smoke_proposer
    from evolution.ledger import load_ledger
    from evolution.registry import load_active_registry

    config, split, cases = workspace(root)
    config["budget"].update({
        "patrol_cases": len(split.patrol_pool),
        "max_proposals": 3,
        "max_admits": 2,
    })
    gold = {case: bool(int(case.split("/")[1][:3]) % 2 == 0) for case in cases}
    patrol_true = [c for c in split.patrol_pool if gold[c]][:2]
    gain_true = [c for c in split.gain if gold[c]][:2]
    base = {case: False for case in patrol_true + gain_true}
    identifier = "IV-VALID-001"
    backend = make_backend(
        config, mode="mock", mock_base=base,
        mock_overrides={identifier: {case: True for case in gain_true}},
    )
    engine = EvolutionEngine(
        config, backend=backend, split=split, mini=True,
        proposer=_smoke_proposer(identifier, patrol_true),
    )
    started = time.monotonic()
    per_cycle: list[dict] = []
    for cycle in range(1, int(cycles) + 1):
        report = engine.run_cycle(cycle)
        per_cycle.append({
            "cycle": cycle,
            "state": report.get("state"),
            "admitted": [
                str(item) for item in
                (report.get("marginal_selection") or {}).get("selected") or ()
            ],
            "registry_after": [
                str(entry.get("intervention_id") or "")
                for entry in engine.registry.entries
            ],
            "marginal_cases": (
                (report.get("active_effect_on_g") or {}).get("marginal_cases")
            ),
            "new_errors": (
                (report.get("active_effect_on_g") or {}).get("new_errors")
            ),
        })
    elapsed = time.monotonic() - started
    registry = load_active_registry(config)
    ledger = load_ledger(config)
    return {
        "seconds": round(elapsed, 2),
        "cycles": int(cycles),
        "per_cycle": per_cycle,
        "active_entries": len(registry.entries),
        "active_ids": [
            str(entry.get("intervention_id") or "")
            for entry in registry.entries
        ],
        "ledger_records": len(ledger.read()),
        "inheritance_reading": (
            "each cycle's admitted set and the registry it left behind; an "
            "entry present in a later cycle's starting registry is an entry "
            "the next round ran with"
        ),
    }


# ----------------------------------------------------------------------
# 2. Development loop with the archive wired
# ----------------------------------------------------------------------

def measure_development(root: pathlib.Path) -> dict:
    """Draft -> L0 -> L1 -> freeze -> archive, through the real objects."""
    config, split, cases = workspace(root)
    targets = [
        case for case in [*split.gain, *split.patrol_pool]
        if int(case.split("/")[1][:3]) % 2 == 0
    ]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in targets},
        overrides={"IV-1-001": {case: True for case in targets}},
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
    candidate = make_intervention(
        intervention_id="IV-1-001",
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
        failure_family=str(family["failure_family"]),
        failure_cases=mechanism_cases,
    )
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
    drafts = [candidate]
    result = developer.develop(
        lambda *a, **k: [drafts.pop(0)] if drafts else [], cycle=1,
    )
    frozen = None
    if result.get("selected_version_id"):
        frozen = developer.freeze(str(result["selected_version_id"]))
    archive = engine.archive_development_run(
        output,
        cycle=1,
        mechanism_id=str(mechanism["mechanism_id"]),
        family_id=str(family["failure_family"]),
        mechanism=mechanism,
    )
    components = load_components(engine.archive_root)
    candidates = load_candidates(engine.archive_root)
    return {
        "mechanism_id": mechanism["mechanism_id"],
        "repair_operation": mechanism["repair_hypothesis"]["operation"],
        "capability": EvolutionEngine.component_capability(mechanism),
        "versions": len(result.get("versions") or ()),
        "version_status": [
            str(item.get("status") or "") for item in result.get("versions") or ()
        ],
        "l0_passed": [
            bool(((item.get("verdict") or {}).get("l0") or {}).get("passed"))
            for item in result.get("versions") or ()
        ],
        "l1_delta": (
            ((result.get("versions") or [{}])[0].get("verdict") or {}).get("l1")
            or {}
        ).get("delta"),
        "frozen_version": (frozen or {}).get("version_id"),
        "archived_candidates": len(candidates),
        "archived_statuses": [str(row.get("status") or "") for row in candidates],
        "archived_components": len(components),
        "component_names": [str(row.get("name") or "") for row in components],
        "archive_report": archive_report(engine.archive_root),
    }


# ----------------------------------------------------------------------
# 3. The new capabilities, through the path a run uses
# ----------------------------------------------------------------------

def measure_runner_admission() -> dict:
    """P2.3: every registered runner's metamorphic admission verdict."""
    rows = []
    for name in sorted(RUNNER_REGISTRY):
        report = run_gate(name)
        rows.append({
            "runner": name,
            "admitted": bool(report["passed"]),
            "classes": len(report["required_classes"]),
            "failed": list(report["failed"]),
        })
    return {
        "runners": rows,
        "all_admitted": all(row["admitted"] for row in rows),
        "report": metamorphic_report(),
    }


def measure_sandbox() -> dict:
    """P2.2: the component runner's contract, limits and refusals."""
    routes = run_forbidden_sources()
    declared = sandbox_capabilities(verify=False)
    return {
        "admission_blockers": admission_blockers(),
        "capabilities_declared": declared["declared"],
        "mechanism": declared["mechanism"],
        "escape_routes": len(routes),
        "escape_routes_refused": sum(1 for row in routes if row["refused"]),
        "runner_contract": RUNNER_REGISTRY[COMPONENT_RUNNER].as_dict(),
    }


def measure_component_execution() -> dict:
    """P2.2: a component entry compiled, executed and refused end to end."""
    component = reference_component()
    normalized = validate_component(component)
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": normalized["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": {
                "expected": "last_prose_text",
                "actual": "question_text",
            },
        },
        "artifact": {"id": normalized["name"], "digest": normalized["digest"]},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    registry = make_registry([entry])
    executors = _registry_component_executors(registry["interventions"])
    plan = _compile_with_component_executors(registry)
    packet = build_packet({
        "question": "Please deliver the summary.",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "Delivered.",
                "tool_calls": [{"name": "writer", "arguments": {}}],
                "tool_responses": ["ok"],
            },
        ],
    })
    results = plan.evidence_for("judge.post", packet, {})
    tampered = json.loads(json.dumps(registry))
    tampered["interventions"][0]["artifact"]["digest"] = "0" * 64
    tampered.pop("content_hash", None)
    refused = ""
    try:
        _compile_with_component_executors(tampered).evidence_for(
            "judge.post", packet, {},
        )
    except ValueError as exc:
        refused = str(exc)
    # A runaway component must abstain, never convict.
    spin = reference_component(
        name="evo_runaway_probe",
        source=(
            "def judge(expected, actual):\n"
            "    total = 0\n"
            "    while True:\n"
            "        total += 1\n"
        ),
        limits={"wall_seconds": 2.0, "cpu_seconds": 1.0},
    )
    abstained = make_component_executor(spin).judge(
        "evo_runaway_probe", {}, {"arguments": {"expected": "a", "actual": "b"}},
    )
    return {
        "executors_built": sorted(executors),
        "evidence_rows": results,
        "evidence_verdict": results[0]["verdict"] if results else "",
        "tampered_digest_refused": bool(refused),
        "tampered_digest_reason": refused[:160],
        "runaway_verdict": abstained.get("verdict"),
    }


def measure_metamorphic_negative_controls() -> dict:
    """P2.3: subjects that must be refused, so the gate is not vacuous."""
    def keyword_rule(packet, context):
        text = " ".join(
            str(event.get("content") or "") for event in packet.get("events") or ()
        )
        return "violated" if "verbatim" in text.lower() else "not_applicable"

    from evolution.metamorphic import (
        normal_record, SemanticSubject,
    )

    keyword = CompositeSubject(
        name="keyword_matcher",
        mechanism=MechanismSubject(),
        packet_rule=PacketRuleSubject(
            name="keyword_matcher",
            run=keyword_rule,
            prose="Flag verbatim phrase omissions when delivered text omits the phrase.",
            packets=[build_packet(normal_record())],
        ),
    )
    keyword_report = run_gate("deterministic", keyword)
    strict = SemanticSubject(
        name="surface_sensitive",
        judge=lambda a, b: "supported" if a == b else "contradicted",
        pairs=[("Hello there.", "Hello there.")],
    )
    strict_report = run_gate("typed_semantic", strict)

    class CaseReadingSubject(MechanismSubject):
        def mechanism_group(self, rows):
            return [
                {
                    "group": ["case"],
                    "mechanism_id": "M-" + str(case).split("/")[-1],
                    "status": "confirmed",
                    "member_count": 1,
                }
                for row in rows
                for case in row.get("failure_cases") or ()
            ]

    case_report = run_gate("deterministic", CaseReadingSubject())
    return {
        "keyword_matcher_admitted": bool(keyword_report["passed"]),
        "keyword_matcher_failed_on": [
            item["class"] for item in keyword_report["checks"]
            if not item["passed"]
        ],
        "surface_sensitive_judge_admitted": bool(strict_report["passed"]),
        "surface_sensitive_failed_on": [
            item["class"] for item in strict_report["checks"]
            if not item["passed"]
        ],
        "case_id_reading_admitted": bool(case_report["passed"]),
        "case_id_reading_failed_on": [
            item["class"] for item in case_report["checks"]
            if not item["passed"]
        ],
    }


def measure_l0_admission_block() -> dict:
    """P2.3 in the development gate: an inadmissible runner is refused at L0."""
    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates",
        members=[],
        member_cases=["A/001.json", "B/002.json"],
    )
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "verbatim_fidelity",
            "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
        },
        "claim": {
            "expected_behavior": "verbatim phrases are delivered",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-judge_false_no_recall_candidates",
            "failure_cases": ["A/001.json", "B/002.json"],
        },
    }
    report = check_l0(entry, mechanism=mechanism)
    return {
        "checks": [
            {"check": item["check"], "passed": item["passed"]}
            for item in report["checks"]
        ],
        "passed": bool(report["passed"]),
    }


def measure_capability_surface() -> dict:
    surface = capability_surface_report()
    freedom = surface["generative_freedom"]
    return {
        "open_ended_self_evolution": surface["open_ended_self_evolution"],
        "runner_count": len(surface["executable_inventory"]["runners"]),
        "runner_admission_all": surface["executable_inventory"][
            "runner_admission"
        ]["all_admitted"],
        "sandboxed_code_generation": freedom["sandboxed_code_generation"],
        "sandbox_scope": freedom["sandboxed_code_generation_scope"],
        "unmet_for_open_ended": [
            item for item in surface["required_for_open_ended_evolution"]
        ],
    }


def measure_capability_smoke(root: pathlib.Path) -> dict:
    """The typed-DSL gain path: does a *new* predicate earn a mechanism gain?

    This is the reading closest to the plan's own question -- "can the framework
    expand its observation vocabulary and then get admitted on the strength of
    it" -- and it runs ``evolution capability-smoke``, which plants the case
    that only the new predicate can catch. Still a mock oracle: the planted
    case is a scripted fact, not a measurement of detection quality.
    """
    config, _split, _cases = workspace(root)
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    started = time.monotonic()
    code = run_cli([
        "capability-smoke", "--config", str(config_path),
    ])
    elapsed = time.monotonic() - started
    report_path = (
        pathlib.Path(config["work_root"]) / "capacity_smoke"
    )
    candidates = sorted(
        (pathlib.Path(config["work_root"])).glob("**/capability_smoke_report.json")
    )
    payload = {}
    if candidates:
        payload = json.loads(candidates[0].read_text(encoding="utf-8"))
    return {
        "exit_code": int(code),
        "seconds": round(elapsed, 2),
        "state": payload.get("state"),
        "admitted": list((payload.get("marginal_selection") or {}).get(
            "selected") or ()),
        "new_predicate": (
            (payload.get("capability_report") or {}).get("predicate")
            or (payload.get("capability") or {}).get("predicate")
        ),
        "gain_opportunity": payload.get("gain_opportunity"),
        "audit_passed": bool((payload.get("audit") or {}).get("passed")),
    }


def main() -> int:
    report: dict = {
        "schema_version": "evolution.validation.v1",
        "evidence_grade": (
            "engineering-closure only: mock backend and offline proposer "
            "throughout, so every reading is about framework mechanics and none "
            "is about detection quality. No provider was called."
        ),
        "packages_measured": ["P2.3", "P2.2", "§28.6"],
    }
    with tempfile.TemporaryDirectory(prefix="validation_") as temporary:
        base = pathlib.Path(temporary)
        print("1/9 off-line cycle ...", flush=True)
        report["offline_cycle"] = measure_cycle(base / "cycle")
        print("2/9 off-line multi-cycle experiment ...", flush=True)
        report["offline_experiment"] = measure_experiment(base / "experiment")
        print("3/9 development loop with the archive wired ...", flush=True)
        report["development"] = measure_development(base / "development")
        print("4/9 runner admission ...", flush=True)
        report["runner_admission"] = measure_runner_admission()
        print("5/9 sandbox contract and refusals ...", flush=True)
        report["sandbox"] = measure_sandbox()
        print("6/9 component execution end to end ...", flush=True)
        report["component_execution"] = measure_component_execution()
        print("8/9 negative controls and L0 admission ...", flush=True)
        report["metamorphic_negative_controls"] = (
            measure_metamorphic_negative_controls()
        )
        report["l0_admission"] = measure_l0_admission_block()
        report["capability_surface"] = measure_capability_surface()
        print("9/9 capability smoke: a new typed-DSL predicate must earn a "
              "mechanism gain ...", flush=True)
        report["capability_smoke"] = measure_capability_smoke(base / "cap_smoke")

    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "out": OUT.relative_to(REPO).as_posix(),
        "offline_cycle": {
            k: report["offline_cycle"][k]
            for k in (
                "state", "admitted", "registry_ids_after", "gate_reason",
                "effect_on_gain_set", "audit_passed", "ledger_valid",
                "seconds",
            )
        },
        "experiment": {
            k: report["offline_experiment"][k]
            for k in ("per_cycle", "active_ids", "ledger_records", "seconds")
        },
        "development": {
            k: report["development"][k]
            for k in (
                "version_status", "frozen_version", "archived_statuses",
                "archived_components", "component_names",
            )
        },
        "all_runners_admitted": report["runner_admission"]["all_admitted"],
        "escape_routes_refused": (
            f"{report['sandbox']['escape_routes_refused']}"
            f"/{report['sandbox']['escape_routes']}"
        ),
        "component_verdict": report["component_execution"]["evidence_verdict"],
        "tampered_digest_refused": (
            report["component_execution"]["tampered_digest_refused"]
        ),
        "runaway_verdict": report["component_execution"]["runaway_verdict"],
        "capability_smoke": report.get("capability_smoke"),
        "negative_controls_all_refused": all(
            not report["metamorphic_negative_controls"][key]
            for key in (
                "keyword_matcher_admitted",
                "surface_sensitive_judge_admitted",
                "case_id_reading_admitted",
            )
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
