#!/usr/bin/env python
"""Re-run the P2.2 sandbox readings. Read-only, offline, no provider calls.

    python reports/p2_sandbox_20260923/verify_sandbox.py

Prints, in order:

1. the platform's declared and *probed* resource limits (a probe makes a real
   child violate each limit, so the reading is "something was stopped");
2. the escape-route catalogue: every forbidden source and whether it validated;
3. the runner's declared contract;
4. the metamorphic admission verdict for the new runner;
5. the reachability check: a component entry compiled and executed through the
   evolution backend, plus a tampered digest being refused.

Exit code 0 when every reading is the expected one.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.interventions import (  # noqa: E402
    COMPONENT_RUNNER,
    RUNNER_REGISTRY,
    build_intervention_registry,
)
from detector.packet import build_packet  # noqa: E402
from evolution.backends import (  # noqa: E402
    _compile_with_component_executors,
    _registry_component_executors,
)
from evolution.metamorphic import admission_error, run_gate  # noqa: E402
from evolution.sandbox import (  # noqa: E402
    DEFAULT_LIMITS,
    HARD_LIMITS,
    IMPORT_WHITELIST,
    admission_blockers,
    reference_component,
    run_forbidden_sources,
    sandbox_capabilities,
    validate_component,
)

PACKET_RECORD = {
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
}


def component_entry(component, *, digest=None):
    normalized = validate_component(component)
    return {
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
        "artifact": {
            "id": normalized["name"],
            "digest": normalized["digest"] if digest is None else digest,
        },
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


def main() -> int:
    ok = True

    print("=== 1. resource limits: declared, then probed ===")
    declared = sandbox_capabilities(verify=False)
    print(f"  mechanism: {declared['mechanism']}  platform: {declared['platform']}")
    print(f"  declared: {json.dumps(declared['declared'])}")
    print(f"  admission blockers: {admission_blockers() or 'none'}")
    print(f"  soft defaults: {json.dumps(DEFAULT_LIMITS)}")
    print(f"  hard ceiling:  {json.dumps(HARD_LIMITS)}")
    print("  probing (each probe makes a real child violate one limit)...")
    probed = sandbox_capabilities(verify=True)
    for kind, row in probed["verified"].items():
        print(f"    {kind:8} enforced={row['enforced']}  {row['detail']}")
        if declared["declared"].get(
            {"wall": "wall_seconds", "cpu": "cpu_seconds", "memory": "memory_bytes"}[kind]
        ) and not row["enforced"]:
            ok = False

    print("\n=== 2. escape routes ===")
    rows = run_forbidden_sources()
    leaked = [row for row in rows if not row["refused"]]
    print(f"  catalogue: {len(rows)} routes; refused: {len(rows) - len(leaked)}; leaked: {len(leaked)}")
    for row in leaked:
        print(f"    LEAK {row['route']}")
    ok = ok and not leaked
    print("  sample refusals:")
    for row in rows[:5]:
        print(f"    {row['route']:22} {row['reason'][:88]}")
    print(f"  import whitelist ({len(IMPORT_WHITELIST)}): {', '.join(IMPORT_WHITELIST)}")

    print("\n=== 3. the runner's declared contract ===")
    spec = RUNNER_REGISTRY[COMPONENT_RUNNER].as_dict()
    for key in (
        "supported_hooks", "intervention_types", "deterministic", "learned",
        "requires_artifact", "terminal_authority", "metamorphic_classes",
    ):
        print(f"  {key:22} {spec[key]}")

    print("\n=== 4. metamorphic admission ===")
    for name in sorted(RUNNER_REGISTRY):
        report = run_gate(name)
        print(
            f"  {name:22} admitted={report['passed']} "
            f"classes={len(report['required_classes'])} failed={report['failed']}"
        )
        if not report["passed"]:
            ok = False
    print(f"  admission_error({COMPONENT_RUNNER!r}) = {admission_error(COMPONENT_RUNNER)!r}")

    print("\n=== 5. reachability through the evolution backend ===")
    component = reference_component()
    registry = build_intervention_registry([component_entry(component)])
    executors = _registry_component_executors(registry["interventions"])
    print(f"  executors built from the registry: {sorted(executors)}")
    plan = _compile_with_component_executors(registry)
    packet = build_packet(PACKET_RECORD)
    results = plan.evidence_for("judge.post", packet, {})
    print(f"  evidence rows: {json.dumps(results, ensure_ascii=False)}")
    ok = ok and len(results) == 1 and results[0]["runner"] == COMPONENT_RUNNER

    tampered = copy.deepcopy(registry)
    tampered["interventions"][0]["artifact"]["digest"] = "0" * 64
    tampered.pop("content_hash", None)
    try:
        _compile_with_component_executors(tampered).evidence_for(
            "judge.post", packet, {},
        )
        print("  tampered digest: ACCEPTED -- BUG")
        ok = False
    except ValueError as exc:
        print(f"  tampered digest refused: {str(exc)[:96]}")

    print(f"\nverdict: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
