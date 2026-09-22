"""Migrate an arm file written before arm identities existed.

The first real promotion run was interrupted, and its completed candidate arm
was stored without an identity block. Re-measuring it would re-spend provider
budget on work that was already done, but silently trusting the old file would
defeat the reason the identity exists.

This script therefore *derives* the identity from the frozen protocol and the
candidate, then refuses to migrate unless every stored row independently
confirms the same registry and case set. What it writes is a checked claim, not
an assumed one.
"""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from evolution.contracts import make_registry  # noqa: E402
from evolution.promotion import (  # noqa: E402
    benefit_cases,
    candidate_from_research,
    protection_cases,
)


def main() -> int:
    arm_path = os.path.join(
        REPO, "artifacts/promotion-real/run/arms/candidate.json",
    )
    protocol_path = os.path.join(
        REPO, "output/promotion-planning/promotion-protocol-real.json",
    )
    protocol = json.load(open(protocol_path, encoding="utf-8-sig"))
    candidate = candidate_from_research(
        os.path.join(REPO, "reports/rsi_pilot_20260919/round4")
    )
    stored = json.load(open(arm_path, encoding="utf-8-sig"))
    if stored.get("identity"):
        print("arm already carries an identity; nothing to migrate")
        return 0
    entries = list(candidate["artifacts"])
    registry = make_registry(entries)
    registry_hash = str(registry.get("content_hash") or "")
    cases = [*benefit_cases(protocol), *protection_cases(protocol)]
    cases = sorted(cases)
    rows = stored.get("rows") or []
    problems = []
    if sorted(str(row.get("case")) for row in rows) != cases:
        problems.append("stored case set differs from the frozen protocol")
    for row in rows:
        recorded = str(
            ((row.get("result") or {}).get("provenance") or {}).get(
                "interventions_hash"
            ) or ""
        )
        if recorded != registry_hash:
            problems.append(
                f"row {row.get('case')} was measured under a different registry "
                f"({recorded[:16]}... vs {registry_hash[:16]}...)"
            )
            break
    if problems:
        print("REFUSING to migrate:")
        for problem in problems:
            print("  -", problem)
        return 2
    from evolution.splits import load_gold, load_gold_step  # noqa: E402

    dataset_root = os.path.join(REPO, "datasets/raw/agenthallu/AgentHallu")
    gold = {case: load_gold(dataset_root, case) for case in cases}
    gold_steps = {case: load_gold_step(dataset_root, case) for case in cases}
    frozen_steps = {
        str(case): step
        for case, step in (protocol.get("gold_steps") or {}).items()
    }
    disagreeing = {
        case: (frozen_steps.get(case), gold_steps.get(case))
        for case in cases
        if frozen_steps.get(case) != gold_steps.get(case)
    }
    if disagreeing:
        print("REFUSING to migrate: frozen gold steps disagree with the dataset")
        print(" ", disagreeing)
        return 2
    identity = {
        "candidate_content_hash": candidate["content_hash"],
        "registry_hash": registry_hash,
        "cases": cases,
        "fresh": True,
        "gold": gold,
        "gold_steps": gold_steps,
        "workers": int(protocol.get("workers") or 8),
        "frozen_at": protocol.get("frozen_at"),
    }
    stored["identity"] = identity
    stored["migrated_from"] = "pre-identity arm file (interrupted first run)"
    with open(arm_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(stored, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"migrated {len(rows)} rows; registry={registry_hash[:16]}...")
    print("every stored row independently confirmed the same registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
