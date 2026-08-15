"""Phase 1-D evaluation: deployed-chain adjudication over the fresh set.

Reads the frozen-set and canary run trees plus their runtime bindings,
replays the deterministic adjudication under the deployed policy genome,
and joins gold (evaluator side only) into the three deliverables the
phase promises:

- overall improvement — baseline detector vs EvoGuard chain per set;
- policy contribution — per policy: triggers, correct improvements,
  false activations, missed opportunities;
- evolution trajectory — 76% -> 83% oracle -> 78% runtime -> fresh ?

plus the promotion gate: every gold-error canary stays convicted, the
frozen set loses zero true positives, and wrongful convictions drop.

Usage:
    python -m experiments.p1d_analyze --frozen-run <id> --canary-run <id>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sehd_rise2.evolution.policy import policies_of
from sehd_rise2.evolution.replay import (
    class_actions,
    load_bindings,
    load_run_tree,
    replay_case,
    run_replay,
    surviving_ids,
    validate_bindings,
)

MANIFEST = Path("datasets/eval_splits/v1/manifest.json")
POLICY_FILE = Path("datasets/policy_candidates/v1/policy_candidates.json")
FROZEN_BINDINGS = Path(
    "datasets/eval_splits/v1/runtime_bindings_frozen.json",
)
CANARY_BINDINGS = Path(
    "datasets/eval_splits/v1/runtime_bindings_canary.json",
)
OUT_DIR = Path("datasets/eval_splits/v1")
DEPLOYED_NAME = "L2-P2+L2-P3+L2-P4+L2-P5"
TRAJECTORY = [
    {"stage": "evolution pool baseline", "accuracy": 0.76},
    {"stage": "oracle policy replay", "accuracy": 0.83},
    {"stage": "runtime chain (calibrated)", "accuracy": 0.78},
]


def deployed_policies(payload: dict) -> list[dict]:
    return [
        policy for policy in policies_of(payload)
        if str(policy.get("policy_id")) in DEPLOYED_NAME.split("+")
    ]


def config_by_name(result: dict) -> dict[str, dict]:
    return {
        config["configuration"]: config
        for config in result["configurations"]
    }


def replay_states(
    run_tree: dict, bindings: dict, policies: list[dict],
) -> dict[str, str]:
    """Case -> adjudicated state under the given policy set."""
    actions = class_actions(policies)
    return {
        case_id: replay_case(
            [
                bindings[(case_id, cid)]["certificate_class"]
                for cid in surviving_ids(entry["result"])
            ],
            actions,
        )["state"]
        for case_id, entry in run_tree.items()
    }


def policy_contribution(
    *, run_tree: dict, bindings: dict, payload: dict, result: dict,
) -> list[dict]:
    """Per policy: what it did on the fresh set under single deployment."""
    configs = config_by_name(result)
    baseline = {
        case_id: entry["result"].get("state")
        for case_id, entry in run_tree.items()
    }
    rows: list[dict] = []
    for policy in policies_of(payload):
        policy_id = str(policy.get("policy_id"))
        target = str((policy.get("trigger") or {}).get("certificate_class"))
        config = configs[policy_id]
        fired = set(config["firing_cases"])
        correct = sorted(
            case for case in config["fp_fixed"] if case in fired
        )
        false_activations = sorted(
            case for case in config["tp_lost"] if case in fired
        )
        benign = sorted(
            case for case in fired
            if run_tree[case]["gold"]
            and case not in config["tp_lost"]
        )
        missed = sorted(
            case_id for case_id, entry in run_tree.items()
            if case_id not in fired
            and not entry["gold"]
            and baseline[case_id] == "verified_error"
            and target in (
                bindings[(case_id, cid)]["certificate_class"]
                for cid in surviving_ids(entry["result"])
            )
        )
        rows.append({
            "policy_id": policy_id,
            "target_class": target,
            "trigger_count": len(fired),
            "trigger_cases": sorted(fired),
            "correct_improvements": correct,
            "false_activations": false_activations,
            "benign_firings_on_gold_error": benign,
            "missed_opportunities": missed,
        })
    return rows


def canary_gate(
    *, run_tree: dict, bindings: dict, payload: dict, gates: dict[str, str],
) -> dict:
    """Paired regression gate over the gold-error canaries.

    The policy layer's only authority is withholding convictions, so a
    regression is a case THIS live baseline convicted that the deployed
    chain acquits. A canary the baseline itself already missed is a
    detector limitation recorded as ``baseline_fn`` — it cannot be a
    policy regression and is exactly the raw material Phase 2 targets.
    """
    states = replay_states(
        run_tree, bindings, deployed_policies(payload),
    )
    verdicts = [
        {
            "case_id": case_id,
            "baseline_state": run_tree[case_id]["result"].get("state"),
            "deployed_state": states[case_id],
            "verdict": (
                "baseline_fn" if (
                    run_tree[case_id]["result"].get("state")
                    != "verified_error"
                ) else (
                    "survived" if states[case_id] == "verified_error"
                    else "policy_regression"
                )
            ),
        }
        for case_id, gate in sorted(gates.items())
        if gate == "must_stay_convicted"
    ]
    failures = [
        verdict for verdict in verdicts
        if verdict["verdict"] == "policy_regression"
    ]
    return {
        "verdicts": verdicts,
        "failures": failures,
        "passed": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-run", required=True)
    parser.add_argument("--canary-run", required=True)
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    gates = {
        str(row["case_id"]): str(row["gate"])
        for row in manifest["canary"]["cases"]
    }

    frozen_tree = load_run_tree(Path(args.frozen_run))
    frozen_bindings, frozen_payload = load_bindings(FROZEN_BINDINGS)
    canary_tree = load_run_tree(Path(args.canary_run))
    canary_bindings, canary_payload = load_bindings(CANARY_BINDINGS)
    checks = {
        **validate_bindings(
            frozen_payload, frozen_bindings, frozen_tree,
            allow_unclassified=True,
        ),
        **validate_bindings(
            canary_payload, canary_bindings, canary_tree,
            allow_unclassified=True,
        ),
    }
    for name, passed in checks.items():
        print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if not all(checks.values()):
        return 1

    frozen_result = run_replay(
        run_tree=frozen_tree, bindings=frozen_bindings, payload=payload,
    )
    baseline = config_by_name(frozen_result)["baseline"]
    deployed = config_by_name(frozen_result)[DEPLOYED_NAME]
    contribution = policy_contribution(
        run_tree=frozen_tree, bindings=frozen_bindings,
        payload=payload, result=frozen_result,
    )
    canary = canary_gate(
        run_tree=canary_tree, bindings=canary_bindings,
        payload=payload, gates=gates,
    )

    net_fp = baseline["fp"] - deployed["fp"]
    gate_checks = {
        "canary_zero_known_regression": canary["passed"],
        "frozen_zero_tp_loss": not deployed["tp_lost"],
        "frozen_net_fp_improvement": net_fp > 0,
        "frozen_accuracy_not_below_baseline": (
            deployed["accuracy"] >= baseline["accuracy"]
        ),
    }

    result = {
        "schema_version": "sehd2.p1d_eval.v1",
        "frozen_run": args.frozen_run,
        "canary_run": args.canary_run,
        "baseline": {
            key: baseline[key] for key in (
                "tp", "fp", "fn", "tn", "accuracy", "firing_cases",
            )
        },
        "deployed": {
            key: deployed[key] for key in (
                "tp", "fp", "fn", "tn", "accuracy", "fp_fixed", "tp_lost",
                "changed_cases", "must_survive_violations", "firing_cases",
            )
        },
        "policy_contribution": contribution,
        "canary_gate": canary,
        "promotion_gate": {
            "checks": gate_checks,
            "promoted": all(gate_checks.values()),
        },
        "trajectory": TRAJECTORY + [
            {"stage": "fresh baseline", "accuracy": baseline["accuracy"]},
            {
                "stage": "fresh deployed chain",
                "accuracy": deployed["accuracy"],
            },
        ],
        "binding_source": {
            "frozen": frozen_payload.get("binding_source"),
            "canary": canary_payload.get("binding_source"),
        },
        "class_exposure": frozen_result["class_exposure"],
    }
    out_path = OUT_DIR / "fresh_eval_result.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )
    write_report(result)
    print(
        f"baseline fp={baseline['fp']} acc={baseline['accuracy']} | "
        f"deployed fp={deployed['fp']} acc={deployed['accuracy']} | "
        f"tp_lost={len(deployed['tp_lost'])} | "
        f"canary_passed={canary['passed']} | "
        f"promoted={all(gate_checks.values())}"
    )
    print(f"result -> {out_path}")
    return 0


def write_report(result: dict) -> None:
    baseline = result["baseline"]
    deployed = result["deployed"]
    lines = [
        "# Phase 1-D Fresh Evaluation Report", "",
        f"- frozen set: 100 cases ({result['frozen_run']})",
        f"- canary set: {len(result['canary_gate']['verdicts'])} "
        "gold-error sentinels "
        f"({result['canary_run']})", "",
        "## Overall improvement (baseline vs deployed chain)", "",
        "| chain | TP | FP | FN | TN | accuracy | TP lost | FP fixed |",
        "|---|---|---|---|---|---|---|---|",
        f"| baseline (frozen detector) | {baseline['tp']} |"
        f" {baseline['fp']} | {baseline['fn']} | {baseline['tn']} |"
        f" {baseline['accuracy']:.1%} | — | — |",
        f"| EvoGuard (binder + genome) | {deployed['tp']} |"
        f" {deployed['fp']} | {deployed['fn']} | {deployed['tn']} |"
        f" {deployed['accuracy']:.1%} |"
        f" {len(deployed['tp_lost'])} | {len(deployed['fp_fixed'])} |",
        "", "## Policy contribution", "",
        "| policy | class | triggers | correct | false activations |"
        " missed |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["policy_contribution"]:
        lines.append(
            f"| {row['policy_id']} | {row['target_class']} |"
            f" {row['trigger_count']} |"
            f" {len(row['correct_improvements'])} |"
            f" {len(row['false_activations'])} |"
            f" {len(row['missed_opportunities'])} |"
        )
    lines += [
        "", "### Trigger detail", "",
    ]
    for row in result["policy_contribution"]:
        if not row["trigger_cases"]:
            continue
        lines.append(
            f"- {row['policy_id']} fired on: "
            + ", ".join(row["trigger_cases"])
        )
        if row["correct_improvements"]:
            lines.append(
                "  - correct improvements: "
                + ", ".join(row["correct_improvements"])
            )
        if row["false_activations"]:
            lines.append(
                "  - FALSE activations (TP lost): "
                + ", ".join(row["false_activations"])
            )
    lines += [
        "", "## Canary regression gate", "",
        f"- verdict: **"
        f"{'PASSED' if result['canary_gate']['passed'] else 'FAILED'}**",
    ]
    for verdict in result["canary_gate"]["verdicts"]:
        lines.append(
            f"- {verdict['case_id']}: baseline "
            f"{verdict['baseline_state']} -> deployed "
            f"{verdict['deployed_state']} ({verdict['verdict']})"
        )
    lines += [
        "", "## Evolution trajectory", "",
        "| stage | accuracy |", "|---|---|",
    ]
    for row in result["trajectory"]:
        lines.append(
            f"| {row['stage']} | {row['accuracy']:.1%} |"
        )
    lines += [
        "", "## Promotion gate", "",
    ]
    for check, passed in result["promotion_gate"]["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} {check}")
    lines += [
        "",
        f"**Verdict: genome "
        f"{'PROMOTED' if result['promotion_gate']['promoted'] else 'NOT promoted'}**"
        " on fresh-set evidence.", "",
    ]
    (OUT_DIR / "FRESH_EVAL_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
