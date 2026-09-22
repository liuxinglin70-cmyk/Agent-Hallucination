"""Offline check of the runner registry's guarantees.

Prints the three guarantees P2.1 exists to make true, each demonstrated
rather than asserted:

1. an unregistered runner name is an error, not a silent fallthrough;
2. a learned runner is evidence-only and cannot reach a conviction;
3. the artifact an entry pins is compared with the artifact the executor
   actually loaded.

No provider call, no dataset read. Run from the repository root:

    python reports/p2_runner_registry_20260922/verify_runner_registry.py
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from detector.interventions import (  # noqa: E402
    SEMANTIC_VERDICTS,
    SemanticExecutor,
    build_intervention_registry,
    compile_runtime_plan,
    runner_declarations,
    validate_intervention,
    validate_intervention_batch,
)
from detector.packet import build_packet  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "runner_registry.json"


class StubExecutor(SemanticExecutor):
    name = "stub"

    def judge(self, predicate, packet, context):
        return {"verdict": "contradicted", "detail": f"judged {predicate}"}


def semantic_entry(*, artifact_id="classifier-a", digest="aaa", runner="typed_semantic"):
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001", "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {"predicate": "claim_supported", "runner": runner,
                 "output_verdicts": sorted(SEMANTIC_VERDICTS)},
        "claim": {"expected_behavior": "a typed judgment",
                  "target_polarity": "recall", "target_kind": "detection"},
        "provenance": {"cycle": 1, "failure_family": "FAM-fn-x",
                       "failure_cases": ["A/001.json", "A/002.json"]},
        "artifact": {"id": artifact_id, "digest": digest},
    }


def refused(fn):
    """Run ``fn`` and return the refusal message, or a marker when allowed."""
    try:
        fn()
    except ValueError as exc:
        return str(exc)
    return "ALLOWED (guard failed)"


def main() -> int:
    packet = build_packet({
        "question": "q",
        "history": [{"step": 1, "role": "assistant", "content": "a",
                     "tool_calls": [{"name": "search", "arguments": "{}"}]}],
    })
    entry = semantic_entry()
    registry = build_intervention_registry([entry])

    payload = {
        "schema_version": "evolution.p2_runner_registry_dryrun.v1",
        "evidence_grade": "diagnostic-only",
        "note": (
            "Offline: no provider call and no admission. Demonstrates the "
            "runner contract's enforcement, not detector quality."
        ),
        "declarations": runner_declarations(),
        "unregistered_runner": refused(lambda: validate_intervention(
            {**entry, "body": {**entry["body"], "runner": "no_such_runner"}},
            require_executable_bounds=True,
        )),
        "identity_mismatch": refused(lambda: compile_runtime_plan(
            registry,
            semantic_executor=StubExecutor(
                artifact_id="classifier-B", artifact_digest="bbb",
            ),
        ).evidence_for("judge.post", packet, {})),
        "no_executor": refused(lambda: compile_runtime_plan(
            registry,
        ).evidence_for("judge.post", packet, {})),
        "verdict_beyond_vocabulary": refused(lambda: compile_runtime_plan(
            registry,
            semantic_executor=_BeyondVocabulary(
                artifact_id="classifier-a", artifact_digest="aaa",
            ),
        ).evidence_for("judge.post", packet, {})),
        "conviction_on_semantic_evidence": refused(
            lambda: validate_intervention_batch(
                [entry, _conviction_guard()], require_executable_bounds=True,
            )
        ),
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("registered runners:")
    for spec in payload["declarations"]:
        print(
            f"  {spec['name']:>14}  learned={spec['learned']!s:<5} "
            f"artifact={spec['requires_artifact']!s:<5} "
            f"authority={spec['terminal_authority']}"
        )
    print()
    for key in (
        "unregistered_runner", "identity_mismatch", "no_executor",
        "verdict_beyond_vocabulary", "conviction_on_semantic_evidence",
    ):
        print(f"{key}:")
        print(f"  {payload[key][:110]}")
    print(f"\nreport -> {OUT}")
    return 0


class _BeyondVocabulary(SemanticExecutor):
    name = "beyond"

    def judge(self, predicate, packet, context):
        return {"verdict": "convicted"}


def _conviction_guard() -> dict:
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-002", "type": "decision_guard",
        "hook": "adjudicate.guard",
        "trigger": {"packet_features": ["has_tool_calls"],
                    "facts": ["claim_supported=contradicted",
                              "judge_positive=false", "positive_source=none"]},
        "body": {"action": "convict_mechanical_violation",
                 "evidence_predicate": "claim_supported",
                 "action_not_when": "never", "action_not_when_facts": []},
        "claim": {"expected_behavior": "x", "target_polarity": "recall",
                  "target_kind": "detection"},
        "provenance": {"cycle": 1, "failure_family": "FAM-fn-x",
                       "failure_cases": ["A/001.json", "A/002.json"]},
    }


if __name__ == "__main__":
    raise SystemExit(main())
