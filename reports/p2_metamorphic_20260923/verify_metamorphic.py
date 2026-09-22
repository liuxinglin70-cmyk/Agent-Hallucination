#!/usr/bin/env python
"""Re-run the metamorphic gate and print its verdicts. Read-only.

Reproduces the readings in this directory's README:

    python reports/p2_metamorphic_20260923/verify_metamorphic.py

Nothing is written, no provider is called, and no registry is touched: every
class runs against the reference subjects in ``evolution/metamorphic.py``.
Exit code is 0 when every registered runner is admitted, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.interventions import METAMORPHIC_CLASSES, RUNNER_REGISTRY  # noqa: E402
from evolution.metamorphic import (  # noqa: E402
    CLASS_ORDER,
    CompositeSubject,
    MechanismSubject,
    PacketRuleSubject,
    admitted_runners,
    admission_error,
    metamorphic_report,
    normal_record,
    run_gate,
)
from detector.packet import build_packet  # noqa: E402


def negative_controls() -> list[dict]:
    """Subjects that must be refused, one per class that can be broken."""
    out: list[dict] = []

    def keyword_rule(packet, context):
        text = " ".join(
            str(event.get("content") or "") for event in packet.get("events") or ()
        )
        return "violated" if "verbatim" in text.lower() else "not_applicable"

    subject = CompositeSubject(
        name="keyword_matcher",
        mechanism=MechanismSubject(),
        packet_rule=PacketRuleSubject(
            name="keyword_matcher",
            run=keyword_rule,
            prose="Flag verbatim phrase omissions when delivered text omits the phrase.",
            packets=[build_packet(normal_record())],
        ),
    )
    report = run_gate("deterministic", subject)
    out.append({
        "control": "keyword matcher (fires on its own words)",
        "admitted": report["passed"],
        "failed": report["failed"],
    })

    def strict_judge(expected, actual):
        return "supported" if expected == actual else "contradicted"

    from evolution.metamorphic import SemanticSubject

    subject = SemanticSubject(
        name="surface_sensitive",
        judge=strict_judge,
        pairs=[("Hello there.", "Hello there.")],
    )
    report = run_gate("typed_semantic", subject)
    out.append({
        "control": "surface-sensitive judge (exact string equality)",
        "admitted": report["passed"],
        "failed": report["failed"],
    })

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

    report = run_gate("deterministic", CaseReadingSubject())
    out.append({
        "control": "mechanism keyed on the case id",
        "admitted": report["passed"],
        "failed": report["failed"],
    })
    return out


def main() -> int:
    print("=== the six §15.2 classes, in order ===")
    for index, name in enumerate(CLASS_ORDER, start=1):
        print(f"  {index}. {name}")
    print(f"  (declared by the registry: {len(METAMORPHIC_CLASSES)} names)")

    print("\n=== registration and admission ===")
    for name in sorted(RUNNER_REGISTRY):
        spec = RUNNER_REGISTRY[name]
        print(
            f"  {name:16} declares {len(spec.metamorphic_classes)} classes  "
            f"admission_error={admission_error(name)!r}"
        )
    print(f"  admitted runners: {admitted_runners()}")

    print("\n=== executed gate per runner ===")
    report = metamorphic_report()
    for row in report["runners"]:
        print(
            f"  {row['runner']:16} admitted={row['admitted']} "
            f"classes={len(row['required_classes'])} failed={row['failed']}"
        )
    print(f"  all_admitted={report['all_admitted']} authority={report['authority']}")

    print("\n=== per-class evidence for the deterministic runner ===")
    detail = run_gate("deterministic")
    for check in detail["checks"]:
        print(
            f"  {check['class']:26} passed={check['passed']} "
            f"mutation_effective={check['mutation_effective']}"
        )
        evidence = check["evidence"]
        if isinstance(evidence, list):
            print(f"      {json.dumps(evidence[:2], ensure_ascii=False)}")
        else:
            print(f"      {json.dumps(evidence, ensure_ascii=False)[:220]}")

    print("\n=== negative controls (must all be refused) ===")
    controls = negative_controls()
    for control in controls:
        print(f"  {control['control']}: admitted={control['admitted']}")
        for failure in control["failed"]:
            print(f"      {failure[:150]}")
    refused = all(not control["admitted"] for control in controls)

    ok = report["all_admitted"] and refused
    print(f"\nverdict: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
