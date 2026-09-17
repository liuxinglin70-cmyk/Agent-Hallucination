"""End-to-end proof that capability creation yields observable detector gain.

This test is intentionally offline: a scripted proposer stands in for the
LLM proposer's exact contract and defines a NEW typed-DSL predicate that no
built-in predicate can express. The mock backend executes the real
``InterventionPlan`` (the same executor the real detector uses), so the
admitted registry flips planted false negatives without any override for the
evolved unit. The measurable gain comes from the evolved predicate + guard
pair, not from a hard-coded oracle entry.
"""

from __future__ import annotations

import json
from pathlib import Path

from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.engine import EvolutionEngine
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit


PROGRAM = {
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


def _record(question: str, tool_name: str | None, *, gold: bool) -> dict:
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


def _make_cases(root: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    root.mkdir(parents=True, exist_ok=True)
    cases: list[str] = []

    def add(index: int, question: str, tool: str | None, gold: bool) -> str:
        case = f"A/{index:03d}.json"
        (root / case).parent.mkdir(parents=True, exist_ok=True)
        (root / case).write_text(
            json.dumps(_record(question, tool, gold=gold)),
            encoding="utf-8",
        )
        cases.append(case)
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
) -> dict:
    if intervention_type == "evidence_check":
        body = {
            "predicate": PROGRAM["name"],
            "runner": "typed_dsl",
            "output_verdicts": PROGRAM["verdicts"],
            "predicate_definition": PROGRAM,
        }
        facts: list[str] = []
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
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": intervention_id,
        "type": intervention_type,
        "hook": (
            "judge.post" if intervention_type == "evidence_check"
            else "adjudicate.guard"
        ),
        "trigger": {
            "packet_features": ["has_tool_calls"],
            "facts": facts,
        },
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


class _EvolvedPredicateProposer:
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


def _engine(tmp_path: Path):
    cases = _make_cases(tmp_path / "data")
    sentinel, gain, patrol_pool, validation = cases
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["split_manifest"] = str(tmp_path / "manifest.json")
    config["mini_split_manifest"] = str(tmp_path / "mini_manifest.json")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["backend"]["name"] = "mock"
    config["backend"]["cache"] = False
    config["audit"]["enabled"] = False
    config["budget"]["patrol_cases"] = len(patrol_pool)
    config["budget"]["stable_failure_rerun_cases"] = 0
    split = EvolutionSplit(
        name="capability-gain",
        pool=[*sentinel, *gain, *patrol_pool, *validation],
        sentinel=sentinel,
        gain=gain,
        patrol_pool=patrol_pool,
        validation=validation,
        mini=True,
    )
    planted_fn = [
        case for case in patrol_pool + gain + validation
        if case.endswith(("012.json", "013.json", "004.json", "005.json", "017.json"))
    ]
    backend = MockDetectorBackend(
        base_predictions={case: False for case in planted_fn},
        overrides={},
    )
    engine = EvolutionEngine(
        config,
        backend=backend,
        split=split,
        mini=True,
        proposer=_EvolvedPredicateProposer(),
    )
    return engine


def test_evolved_predicate_produces_observable_detector_gain(tmp_path):
    engine = _engine(tmp_path)
    report = engine.run_cycle(1)

    assert report["state"] == "completed"
    assert set(report["marginal_selection"]["selected"]) == {
        "IV-1-001", "IV-1-002"
    }

    # The gain is produced by the evolved typed-DSL executor + guard, not by
    # a mock override for the evolved unit.
    effect = report["active_effect_on_g"]
    assert effect["marginal_cases"] >= 2
    assert effect["new_errors"] == 0
    assert effect["new_fn"] == 0
    assert effect["correctness_improved"] >= 2

    admitted = {entry["intervention_id"]: entry for entry in engine.registry.entries}
    assert admitted["IV-1-001"]["body"]["runner"] == "typed_dsl"
    assert (
        admitted["IV-1-001"]["body"]["predicate_definition"]["name"]
        == PROGRAM["name"]
    )

    # The real InterventionPlan trace proves the new capability executed.
    evidence_rows = []
    programs = []
    for row in engine.backend.evaluate(
        ["A/004.json"],
        dataset_root=engine.config["dataset_root"],
        registry=engine.registry.registry,
        gold_map=engine.gold_map,
    ):
        deployment = ((row.get("result") or {}).get("proof") or {}).get(
            "deployment"
        ) or {}
        evidence_rows.extend(deployment.get("evidence") or [])
        programs.extend(deployment.get("typed_predicate_programs") or [])
    assert any(
        row.get("predicate") == PROGRAM["name"]
        and row.get("verdict") == "violated"
        and row.get("runner") == "typed_dsl"
        for row in evidence_rows
    )
    assert programs, "deployment trace did not expose the typed predicate program"

    # Held-out validation arm (excluded from all B gates) also improves.
    mini = engine._evaluate_mini_effect()
    assert mini["effect"]["marginal_cases"] >= 1
    assert mini["effect"]["new_errors"] == 0
    assert mini["passed"] is True
