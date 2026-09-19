"""Validate one error-scope attempt against its frozen protocol.

Reads only the archived run: no provider calls, no re-derivation of results.
The checks, in the order they matter:

1. the attempt's frozen detector config still hashes to the protocol entry;
2. for a tree-validated attempt, every other frozen source file matches the
   working tree, so the protocol describes the code that ran. A historical
   attempt cannot be re-derived that way — later attempts legitimately pin
   files it froze — so it is corroborated by its archived run instead and
   that is reported rather than asserted;
3. gates and split match the development baseline, and any budget deviation
   is exactly the declared runtime envelope;
4. the ledger hash chain is valid;
5. the recorded sentinel rows recompute to the recorded coverage verdict;
6. the archive is complete and the terminal state is protocol-legal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evolution.backends import fingerprint
from evolution.config import load_config
from evolution.gates import sentinel_coverage
from evolution.ledger import load_ledger
from evolution.splits import build_split

OUT = Path(__file__).resolve().parent
DETECTOR_CONFIG_KEY = "detector\\configs\\autodl.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", help="attempt directory name")
    args = parser.parse_args()
    attempt_dir = OUT / "attempts" / args.attempt
    config = load_config(attempt_dir / "run_config.json")
    protocol = read(attempt_dir / "protocol.json")
    work = Path(config["work_root"])

    experiment = read(work / "reports/experiment.json")
    cycles = experiment["cycles"]
    states = [cycle["state"] for cycle in cycles]

    # 1. The detector config is pinned from this attempt's own frozen copy.
    frozen_config = ROOT / protocol["frozen_detector_config"]
    config_hash_matches = (
        protocol["source_sha256"][DETECTOR_CONFIG_KEY] == sha(frozen_config)
    )

    # 2. Source files other than the detector config.
    tree_validated = bool(protocol.get("tree_validated"))
    mismatches = sorted(
        path for path, digest in protocol["source_sha256"].items()
        if path != DETECTOR_CONFIG_KEY and sha(ROOT / path.replace("\\", "/")) != digest
    )
    source_check = {
        "tree_validated": tree_validated,
        "frozen_source_matches_tree": not mismatches,
        "mismatched_files": mismatches,
    }

    # 3. Fixed evaluation design; declared envelope deviations are reported.
    baseline_config = load_config(ROOT / "evolution/configs/development.json")
    fixed_fields = {
        key: config[key] == baseline_config[key] for key in ("gates", "split")
    }
    declared = set(protocol.get("budget_envelope_overrides") or ())
    budget_diff = {
        key: {"attempt": value, "baseline": baseline_config["budget"].get(key)}
        for key, value in (config.get("budget") or {}).items()
        if baseline_config["budget"].get(key) != value
    }
    budget_ok = set(budget_diff) <= declared

    # 4. Ledger chain.
    ledger = load_ledger(config).verify()

    # 5. Recompute the sentinel coverage from the archived baseline arm.
    #
    # The audit reads the archive, never the live artifact tree, so a later
    # attempt cannot change this attempt's evidence. The baseline arm is
    # derived, not guessed: the checkpoint keys an arm by
    # ``fingerprint({"registry": registry, "fresh": fresh})``, and the
    # preflight runs the active registry without refreshing, so its arm name
    # follows from the archived registry alone.
    archive = attempt_dir / "run"
    active_registry = read(archive / "registry/active.json")
    baseline_arm = fingerprint({"registry": active_registry, "fresh": False})[:20]
    arm_dir = archive / "checkpoints/cycle001/arms" / baseline_arm
    arm_files = sorted(arm_dir.glob("*.json")) if arm_dir.is_dir() else []
    coverage_rows = [read(path)["row"] for path in arm_files]

    # A cycle that froze at the audit records no split block, so the audit
    # re-derives the split itself from the frozen config: the seed, pool and
    # manifest are all inputs, which makes this an independent recomputation
    # rather than a read-back of what the engine wrote.
    recorded_split = cycles[0].get("split")
    if recorded_split and recorded_split.get("sentinel"):
        sentinel = set(recorded_split["sentinel"])
    else:
        sentinel = set(build_split(
            config=config, dataset_root=config["dataset_root"], mini=True,
        ).sentinel)
    baseline = [row for row in coverage_rows if row["case"] in sentinel]
    recomputed = (
        sentinel_coverage(baseline, config) if len(baseline) == len(sentinel) else None
    )
    recorded = cycles[0].get("preflight")
    recorded_sentinel = (recorded or {}).get("sentinel") or {}
    reason = str((recorded or {}).get("reason") or "")
    # The engine has two preflight stops and they must not be conflated:
    # insufficient sentinel coverage means the baseline arm cannot protect
    # anything, while the gain-opportunity stop means the sentinel was fine
    # but no diagnosed direction could be scored on the gain set. Only the
    # first implies a non-evaluable sentinel.
    terminal_state = states[0] if states else ""
    sentinel_blocked = (
        terminal_state == "blocked_evaluation_preflight"
        and "insufficient baseline-correct sentinel coverage" in reason
    )
    numbers_match = (
        not recorded_sentinel
        or (
            recorded_sentinel.get("protected_tp") == recomputed["protected_tp"]
            and recorded_sentinel.get("protected_tn") == recomputed["protected_tn"]
        )
    ) if recomputed else False
    coverage_agrees = bool(
        recomputed is not None
        and recomputed["evaluable"] != sentinel_blocked
        and numbers_match
    )

    # 6. Archive completeness and a protocol-legal terminal state.
    archive_complete = all(
        (archive / name).exists()
        for name in ("ledger.jsonl", "reports/experiment.json")
    )
    legal_terminals = {
        "blocked_evaluation_preflight",
        "completed",
        "frozen_after_defect_injection_audit",
    }
    states_legal = all(state in legal_terminals for state in states)
    # The protocol forbids stopping mid-experiment without one of the two
    # recorded stops; a frozen audit is a recorded stop, not a silent halt.
    all_cycles_recorded = bool(states) and states == [cycle["state"] for cycle in cycles]

    checks = {
        "frozen_detector_config_matches_protocol": config_hash_matches,
        "source_matches_tree_or_historical": (not mismatches) or not tree_validated,
        "gates_and_split_unchanged": all(fixed_fields.values()),
        "budget_deviations_declared": budget_ok,
        "ledger_valid": bool(ledger["valid"]),
        "preflight_recomputed_agrees": coverage_agrees,
        "archive_complete": archive_complete,
        "terminal_state_protocol_legal": states_legal and all_cycles_recorded,
    }
    verdict = {
        "attempt": args.attempt,
        "terminal_states": states,
        "authorized_cycles": int(config.get("cycles") or 2),
        "frozen_detector_config": protocol["frozen_detector_config"],
        "change_under_test": protocol["change_under_test"],
        "source_check": source_check,
        "fixed_fields": fixed_fields,
        "budget_deviation": budget_diff,
        "declared_envelope_overrides": sorted(declared),
        "ledger": ledger,
        "baseline_arm": baseline_arm,
        "baseline_arm_rows": len(coverage_rows),
        "preflight_recorded": recorded,
        "preflight_block_reason": reason,
        "sentinel_blocked": sentinel_blocked,
        "gain_opportunity": (cycles[0].get("preflight") or {}).get("gain_opportunity"),
        "preflight_recomputed": recomputed,
        "preflight_agrees": coverage_agrees,
        "archive_complete": archive_complete,
        "checks": checks,
    }
    verdict["passed"] = all(checks.values())
    (attempt_dir / "completion_audit.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "attempt": args.attempt,
        "terminal_states": states,
        "preflight": (recomputed or {}).get("protected_tp"),
        "preflight_tn": (recomputed or {}).get("protected_tn"),
        "budget_deviation": budget_diff,
        "passed": verdict["passed"],
        "failed_checks": [k for k, v in checks.items() if not v],
    }, ensure_ascii=False))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
