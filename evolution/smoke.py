"""Offline capability-evolution smoke experiment.

This experiment proves the optimized framework can execute real self-evolution
rather than only recombining six hard-coded predicates:

1. a scripted proposer (same contract as the LLM proposer) defines a NEW
   typed-DSL predicate ``evo_missing_search_tool`` inline;
2. the STRUCTURAL gate executes that new predicate on every patrol member of the
   failure family and requires a unanimous conviction-authorized verdict;
3. the mock backend runs the real ``detector.interventions.InterventionPlan``
   (including the typed-DSL interpreter and ``convict_mechanical_violation``),
   so the gain is caused by the evolved detector artifact, not by an override;
4. HARMLESSNESS/BEHAVIOR/M admit the evidence+guard unit and the report shows measurable
   recall gain on cases excluded from the patrol.

This is an offline mechanism validation. It is marked ``backend=mock`` and must
not be reported as a real detector quality number.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evolution.backends import MockDetectorBackend
from evolution.capability_surface import capability_surface_report
from evolution.config import load_config
from evolution.contracts import save_json
from evolution.engine import EvolutionEngine
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit

PROGRAM: dict[str, Any] = {
    "schema_version": "detector.predicate_program.v1",
    "name": "evo_missing_search_tool",
    "description": "question requires a search tool but no search-like tool was called",
    "verdicts": ["satisfied", "violated", "not_applicable"],
    "conviction_verdicts": ["violated"],
    "default_verdict": "not_applicable",
    "rules": [
        {
            "field": "tool_names",
            "operator": "any_contains",
            "value": "search",
            "verdict": "satisfied",
            "detail": "a search-like tool was called",
        },
        {
            "field": "question_text",
            "operator": "contains",
            "value": "use search",
            "verdict": "violated",
            "detail": "search was contractually required but no search tool exists",
        },
    ],
}


def _record(question: str, tool_name: str | None, *, gold: bool) -> dict[str, Any]:
    history = [{
        "role": "assistant",
        "content": "working",
        "tool_calls": (
            [{"name": tool_name, "arguments": "{}"}]
            if tool_name is not None else []
        ),
    }]
    return {
        "question": question,
        "history": history,
        "is_hallucination": gold,
        "hallucination_step": 1 if gold else None,
        "true_answer": "gold",
    }


def build_synthetic_cases(root: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    root.mkdir(parents=True, exist_ok=True)

    def add(index: int, question: str, tool: str | None, gold: bool) -> str:
        case = f"A/{index:03d}.json"
        (root / case).parent.mkdir(parents=True, exist_ok=True)
        (root / case).write_text(
            json.dumps(_record(question, tool, gold=gold)),
            encoding="utf-8",
        )
        return case

    sentinel = [
        add(0, "Use search and report X.", "search", True),
        add(1, "Compute the sum.", "python", False),
        add(2, "Use search and report Y.", "search", True),
        add(3, "Write a poem.", "python", False),
    ]
    gain = [
        add(4, "Use search and report A.", "python", True),
        add(5, "Use search and report B.", "python", True),
        add(6, "Use search and report C.", "search", True),
        add(7, "Compute the sum.", "python", False),
        add(8, "Use search and report D.", "search", True),
        add(9, "Write a poem.", "python", False),
        add(10, "Use search and report E.", "search", True),
        add(11, "Compute another sum.", "python", False),
    ]
    patrol_pool = [
        add(12, "Use search and report F.", "python", True),
        add(13, "Use search and report G.", "python", True),
        add(14, "Use search and report H.", "search", True),
        add(15, "Compute the sum.", "python", False),
        add(16, "Write a poem.", "python", False),
    ]
    validation = [
        add(17, "Use search and report I.", "python", True),
        add(18, "Use search and report J.", "search", True),
        add(19, "Compute the sum.", "python", False),
        add(20, "Write a poem.", "python", False),
    ]
    return sentinel, gain, patrol_pool, validation


def _candidate(
    intervention_id: str,
    intervention_type: str,
    family: str,
    failure_cases: list[str],
    cycle: int,
) -> dict[str, Any]:
    if intervention_type == "evidence_check":
        body = {
            "predicate": PROGRAM["name"],
            "runner": "typed_dsl",
            "output_verdicts": PROGRAM["verdicts"],
            "predicate_definition": PROGRAM,
        }
        facts: list[str] = []
        hook = "judge.post"
    else:
        body = {
            "action": "convict_mechanical_violation",
            "evidence_predicate": PROGRAM["name"],
            "action_not_when": "Never when a search tool was called.",
            "action_not_when_facts": [f"{PROGRAM['name']}=satisfied"],
        }
        facts = [
            f"{PROGRAM['name']}=violated",
            "judge_positive=false",
            "positive_source=none",
        ]
        hook = "adjudicate.guard"
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": intervention_id,
        "type": intervention_type,
        "hook": hook,
        "trigger": {"packet_features": ["has_tool_calls"], "facts": facts},
        "body": body,
        "claim": {
            "expected_behavior": (
                "typed DSL detects a contractually required search tool that "
                "is absent and the paired guard convicts the mechanical miss"
            ),
            "target_polarity": "recall",
        },
        "provenance": {
            "cycle": cycle,
            "failure_family": family,
            "failure_cases": list(failure_cases),
        },
    }


class CapabilityProposer:
    """Scripted stand-in that satisfies the LLM proposer contract."""

    name = "scripted_capability"

    def propose(self, families, *, cycle, max_proposals=3):
        family = next(
            item for item in families
            if str(item.get("failure_family") or "").startswith("FAM-fn-")
        )
        evidence = _candidate(
            "IV-1-001", "evidence_check",
            str(family["failure_family"]), list(family["failure_cases"]), cycle,
        )
        guard = _candidate(
            "IV-1-002", "decision_guard",
            str(family["failure_family"]), list(family["failure_cases"]), cycle,
        )
        return ProposalBatch(
            candidates=[evidence, guard],
            joint_groups=[["IV-1-001", "IV-1-002"]],
            notes=[
                {
                    "intervention_id": "IV-1-001",
                    "generator": "scripted_capability",
                    "capability_created": PROGRAM["name"],
                },
                {
                    "intervention_id": "IV-1-002",
                    "generator": "scripted_capability",
                },
            ],
        )


def create_smoke_root(work_root: str | Path) -> Path:
    root = Path(work_root).resolve()
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeError(f"smoke root already exists; choose an unused work root: {root}") from exc
    return root


def build_capability_smoke_engine(work_root: str | Path, config: dict[str, Any] | None = None):
    """Create an isolated engine + synthetic dataset under ``work_root``."""
    root = create_smoke_root(work_root)
    cfg = dict(load_config()) if config is None else dict(config)
    sentinel, gain, patrol_pool, validation = build_synthetic_cases(root / "data")
    cfg["dataset_root"] = str(root / "data")
    cfg["split_manifest"] = str(root / "manifest.json")
    cfg["mini_split_manifest"] = str(root / "mini_manifest.json")
    cfg["work_root"] = str(root / "work")
    cfg["registry_dir"] = str(root / "registry")
    cfg["ledger_path"] = str(root / "ledger.jsonl")
    cfg["harness_dir"] = str(root / "harness")
    cfg["memory_root"] = str(root / "memory")
    cfg["backend"]["name"] = "mock"
    cfg["backend"]["cache"] = False
    cfg["audit"]["enabled"] = False
    cfg["budget"]["patrol_cases"] = len(patrol_pool)
    cfg["budget"]["stable_failure_rerun_cases"] = 0
    split = EvolutionSplit(
        name="capability-smoke",
        pool=[*sentinel, *gain, *patrol_pool, *validation],
        sentinel=sentinel,
        gain=gain,
        patrol_pool=patrol_pool,
        validation=validation,
        mini=True,
    )
    planted_fn = [
        case for case in patrol_pool + gain + validation
        if case.endswith(("004.json", "005.json", "012.json", "013.json", "017.json"))
    ]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in planted_fn},
        overrides={},
    )
    return EvolutionEngine(
        cfg,
        backend=backend,
        split=split,
        mini=True,
        proposer=CapabilityProposer(),
    )


def run_capability_smoke(work_root: str | Path) -> dict[str, Any]:
    with build_capability_smoke_engine(work_root) as engine:
        report = engine.run_cycle(1)
        report["mini_effect"] = engine._evaluate_mini_effect()
        report["capability_audit"] = {
            "classification": (
                capability_surface_report()["classification"]
            ),
            "capability_created": PROGRAM["name"],
            "executor": "detector.interventions.InterventionPlan",
            "backend": "mock",
            "note": (
                "offline mechanism validation only; not a real detector quality "
                "number"
            ),
        }
        save_json(Path(work_root) / "capability_smoke_report.json", report)
        return report


__all__ = [
    "CapabilityProposer",
    "PROGRAM",
    "build_capability_smoke_engine",
    "create_smoke_root",
    "build_synthetic_cases",
    "run_capability_smoke",
]
