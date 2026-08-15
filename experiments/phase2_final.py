"""Phase 2 final evaluation: the verification set under the evolved chain.

Reads the frozen verification run tree plus its runtime bindings and
adjudicates it three ways — no-policy baseline, the seed genome the
cycles started from (P2-P5), and the final evolved genome — then writes
the experiment's closing deliverables:

- overall: accuracy / FP / FN / TP for each of the three chains;
- evolution trajectory: per-cycle cumulative evolution-pool numbers
  from the library ledger, closed by the verification results;
- policy lifecycle: every policy the library ever tracked, its origin,
  status, events and lifetime contribution.

Usage:
    python -m experiments.phase2_final --run runs/phase2_verify_<id>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sehd_rise2.evolution.cycle import (
    adjudicate,
    gate_metrics,
    load_library,
    load_genome_items,
)
from sehd_rise2.evolution.replay import load_bindings, load_run_tree

LIBRARY = Path("datasets/policy_library/v1")
VERIFICATION_BINDINGS = (
    LIBRARY / "bindings/verification.json"
)
OUT_DIR = Path("datasets/eval_splits/phase2_v1")
PHASE1_ANCHORS = [
    {"stage": "phase 0 baseline (evolution pool, n=100)",
     "accuracy": 0.76},
    {"stage": "phase 1 oracle replay", "accuracy": 0.83},
    {"stage": "phase 1 calibrated runtime chain", "accuracy": 0.78},
]


def chain_metrics(tree, bindings, items) -> dict:
    return gate_metrics(tree, adjudicate(tree, bindings, items))


def lifecycle(library: dict) -> list[dict]:
    rows = []
    for policy_id, facts in sorted(library["policies"].items()):
        rows.append({
            "policy_id": policy_id,
            "status": facts["status"],
            "born_cycle": facts["born_cycle"],
            "events": [
                {
                    "cycle": event["cycle"],
                    "event": event["event"],
                }
                for event in facts["events"]
            ],
        })
    return rows


def trajectory(library: dict, verification: dict) -> list[dict]:
    rows = list(PHASE1_ANCHORS)
    for record in library["cycles"]:
        gate = record.get("gate") or {}
        current = gate.get("current") or {}
        candidate = gate.get("candidate") or current
        rows.append({
            "stage": (
                f"cycle {record['cycle']} "
                f"({'promoted' if record['survivors'] else 'no change'}, "
                f"cumulative n={sum(
                    current.get(k, 0) for k in ('tp', 'fp', 'fn', 'tn')
                )})"
            ),
            "accuracy": candidate.get("accuracy"),
            "fp": candidate.get("fp"),
            "tp_lost": len(candidate.get("tp_lost") or []),
        })
    rows.append({
        "stage": "final verification (n=200) — evolved chain",
        "accuracy": verification["final"]["accuracy"],
        "fp": verification["final"]["fp"],
        "tp_lost": len(verification["final"]["tp_lost"]),
    })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()

    library = load_library(LIBRARY)
    tree = load_run_tree(args.run)
    bindings, _ = load_bindings(VERIFICATION_BINDINGS)
    seed_items = load_genome_items(LIBRARY / "genomes/genome_v1.json")
    final_items = load_genome_items(
        LIBRARY / library["genomes"][-1]["file"],
    )

    baseline = chain_metrics(tree, bindings, [])
    seed = chain_metrics(tree, bindings, seed_items)
    final = chain_metrics(tree, bindings, final_items)
    verification = {"baseline": baseline, "seed": seed, "final": final}

    result = {
        "schema_version": "sehd2.phase2_eval.v1",
        "verification_run": str(args.run),
        "genome_final": {
            "file": library["genomes"][-1]["file"],
            "content_hash": library["genomes"][-1]["content_hash"],
        },
        "verification": {
            key: {
                sub: metrics[sub] for sub in (
                    "tp", "fp", "fn", "tn", "accuracy", "tp_lost",
                    "fp_fixed",
                )
            }
            for key, metrics in verification.items()
        },
        "policy_lifecycle": lifecycle(library),
        "trajectory": trajectory(library, verification),
        "cycles": [
            {
                "cycle": record["cycle"],
                "outcome": record["outcome"],
                "survivors": record["survivors"],
                "dropped": record["dropped"],
                "worksheet": {
                    key: len(record["worksheet"][key])
                    for key in record["worksheet"]
                },
            }
            for record in library["cycles"]
        ],
    }
    result_path = OUT_DIR / "phase2_result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Phase 2 Full Self-Evolution — Final Report", "",
        f"- verification run: {args.run} (200 frozen cases)",
        f"- final genome: {result['genome_final']['file']}"
        f" (hash {result['genome_final']['content_hash'][:12]}…)",
        "", "## Verification set (200 cases, never read by evolution)", "",
        "| chain | TP | FP | FN | TN | accuracy | TP lost | FP fixed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, label in (
        ("baseline", "no policies (frozen detector)"),
        ("seed", "seed genome (P2-P5, phase 1)"),
        ("final", "evolved genome (phase 2)"),
    ):
        metrics = result["verification"][key]
        lines.append(
            f"| {label} | {metrics['tp']} | {metrics['fp']} |"
            f" {metrics['fn']} | {metrics['tn']} |"
            f" {metrics['accuracy']:.1%} |"
            f" {len(metrics['tp_lost'])} |"
            f" {len(metrics['fp_fixed'])} |"
        )
    lines += ["", "## Evolution trajectory", "",
              "| stage | accuracy | FP | TP lost |", "|---|---|---|---|"]
    for row in result["trajectory"]:
        lines.append(
            f"| {row['stage']} |"
            f" {'' if row.get('accuracy') is None else f"{row['accuracy']:.1%}"}"
            f" | {row.get('fp', '')} | {row.get('tp_lost', '')} |"
        )
    lines += ["", "## Policy lifecycle", "",
              "| policy | born | status | events |", "|---|---|---|---|"]
    for row in result["policy_lifecycle"]:
        events = ", ".join(
            f"c{event['cycle']}:{event['event']}"
            for event in row["events"]
        )
        lines.append(
            f"| {row['policy_id']} | cycle {row['born_cycle']} |"
            f" {row['status']} | {events} |"
        )
    lines += ["", "## Cycles", ""]
    for record in result["cycles"]:
        dropped = (
            "; ".join(
                f"{entry['policy_id']} ({entry['reason']})"
                for entry in record["dropped"]
            ) or "none"
        )
        lines.append(
            f"- cycle {record['cycle']}: {record['outcome']}; "
            f"survivors {record['survivors'] or '—'}; dropped: {dropped}"
        )
    lines.append("")
    (OUT_DIR / "PHASE2_FINAL_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )
    print(f"result -> {result_path}")
    print(f"report -> {OUT_DIR / 'PHASE2_FINAL_REPORT.md'}")
    final_metrics = result["verification"]["final"]
    seed_metrics = result["verification"]["seed"]
    print(
        f"final vs seed: acc {final_metrics['accuracy']:.1%} vs "
        f"{seed_metrics['accuracy']:.1%}, fp {final_metrics['fp']} vs "
        f"{seed_metrics['fp']}, tp_lost "
        f"{len(final_metrics['tp_lost'])} vs "
        f"{len(seed_metrics['tp_lost'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
